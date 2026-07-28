"""Upload + ingestion endpoints.

Accepts a file (or URL), persists the original, extracts text, classifies it
with Gemini, and stores the structured metadata in SQLite.

The original file is never modified. Extracted text and AI metadata are written
to two separate places — the `{file}.meta.json` sidecar (on-disk source of truth
for integrity) and the `documents` table (queryable metadata for search, the
timeline, and the graph).
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

import storage
from ai import categorizer, embeddings
from config import settings
from db import database
from identity import DEFAULT_USER, current_user
from ingestion import file_parser, text_entry, url_scraper
from models.document import Categorization

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ingestion"])


class CategorizationResponse(BaseModel):
    document_type: str
    category: str
    title: str
    date: str | None
    summary: str
    skills: list[str]
    organizations: list[str]
    people: list[str]
    tags: list[str]
    confidence: float
    # The date a card should actually show, and whether we know it or guessed
    # it. Resolved server-side by `database.resolve_date` so the UI cannot
    # reimplement plan.md § Risk Mitigation's fallback and get the "flag it" half wrong —
    # which is exactly what the client-side `dateAssumed = cat && !cat.date`
    # check it replaces was doing.
    effective_date: str | None = None
    date_source: str = "assumed"  # "extracted" | "assumed"
    # Structured degradation (deferred item B). None on a normal result; on a
    # degraded one, the reason code and whether a retry can help — so the card
    # can offer "try again" for a quota failure but not for a missing key,
    # instead of pattern-matching the summary prose.
    degraded_reason: str | None = None
    retryable: bool = False


class ExtractionResponse(BaseModel):
    id: str
    filename: str
    stored_path: str
    file_type: str
    method: str
    char_count: int
    used_ocr: bool
    checksum: str
    size_bytes: int
    warnings: list[str]
    text_preview: str
    categorization: CategorizationResponse
    # Why extraction produced no text, structurally — the same reason/retryable
    # contract `categorization` carries, one layer upstream. Named with a prefix
    # because the two coexist on this response and mean different things: a
    # document can extract cleanly and fail to classify, or vice versa. None
    # whenever text was obtained.
    extraction_degraded_reason: str | None = None
    extraction_retryable: bool = False


class ReExtractionResponse(BaseModel):
    """The outcome of re-running extraction over a preserved original."""

    id: str
    filename: str
    file_type: str
    method: str
    char_count: int
    used_ocr: bool
    # Did *this* run produce text? False is the ordinary outcome when the cause
    # has not cleared (still no quota, still no key) — an honest "not yet",
    # not an error, so the response is a 200 carrying the current reason.
    recovered: bool
    # Whether the recovered text was re-classified. False when nothing was
    # recovered (no point) or when the text is unchanged and its existing
    # categorization was not a degraded guess (no gain, and a Gemini call is
    # 5% of the day).
    recategorized: bool
    warnings: list[str]
    extraction_degraded_reason: str | None = None
    extraction_retryable: bool = False
    text_preview: str = ""
    categorization: CategorizationResponse | None = None


class UrlIngestRequest(BaseModel):
    url: str


class UrlIngestResponse(BaseModel):
    id: str
    url: str
    title: str
    source_type: str
    char_count: int
    warnings: list[str]
    text_preview: str
    categorization: CategorizationResponse
    # Structured facts the source stated about itself — stars, languages, a
    # profile's repo list. Empty for a generic web page. See
    # `ScrapeResult.details`; `details["kind"]` names the shape.
    details: dict = Field(default_factory=dict)


class TextIngestRequest(BaseModel):
    text: str


class TextIngestResponse(BaseModel):
    id: str
    filename: str
    file_type: str
    char_count: int
    warnings: list[str]
    text_preview: str
    categorization: CategorizationResponse


def _preview(text: str, limit: int = 800) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _extraction_metadata(result, size_bytes: int | None = None) -> dict:
    """The derived-extraction block stored in `metadata_json`.

    One helper so `/upload` and `/reextract` cannot drift into writing different
    keys for the same facts — which would leave a re-extracted document's
    metadata shaped unlike an uploaded one's, and the UI reading a key that is
    there only sometimes.
    """
    meta = {
        "method": result.method,
        "used_ocr": result.used_ocr,
        "char_count": result.char_count,
        "extraction_warnings": result.warnings,
        # Always present, including as None on success — a stale reason left
        # behind would keep the UI offering a retry for a document that no
        # longer needs one.
        "extraction_degraded_reason": result.degraded.reason if result.degraded else None,
        "extraction_retryable": bool(result.degraded and result.degraded.retryable),
    }
    if size_bytes is not None:
        meta["size_bytes"] = size_bytes
    return meta


async def _index_document(
    *, doc_id: str, title: str, raw_text: str, user_id: str = DEFAULT_USER
) -> None:
    """Embed a just-stored document into the vector store, best-effort.

    Runs *after* the SQLite insert, never before: the row is the source of truth,
    so a failure here loses nothing — the document is simply left unindexed until
    the next startup sync (`embeddings.ensure_synced`) fills it in. `embedding_id`
    is set only on success, so a NULL value reliably means "not yet in Chroma".

    Off the event loop for CPU reasons (model inference), not rate limiting —
    embeddings are local and free, unlike the Gemini call above.
    """
    try:
        chunks = await run_in_threadpool(
            embeddings.add_document,
            doc_id=doc_id,
            user_id=user_id,
            title=title,
            raw_text=raw_text,
        )
        if chunks:
            await run_in_threadpool(database.set_embedding_id, doc_id, doc_id)
    except Exception:
        logger.exception("Embedding failed for %s — document left unindexed.", doc_id)


def _to_response(result: Categorization, upload_date: str) -> CategorizationResponse:
    effective_date, date_source = database.resolve_date(result.date, upload_date)
    return CategorizationResponse(
        **result.model_dump(),
        effective_date=effective_date,
        date_source=date_source,
    )


@router.post("/upload", response_model=ExtractionResponse)
async def upload_file(
    file: UploadFile = File(...), user_id: str = Depends(current_user)
) -> ExtractionResponse:
    filename = file.filename or "unnamed"
    if not file_parser.is_supported(filename):
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type: {Path(filename).suffix or '(none)'}",
        )

    # Read with a size guard.
    contents = await file.read()
    if len(contents) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds max size of {settings.max_upload_bytes} bytes.",
        )
    if not contents:
        raise HTTPException(status_code=400, detail="Empty file.")

    # Persist the original byte-for-byte, with a checksum verified on write.
    doc_id = uuid.uuid4().hex
    try:
        stored_path, checksum = storage.save_original(
            user_id, doc_id, filename, contents
        )
    except IOError as exc:
        logger.error("Failed to store original for %s: %s", filename, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Extract text. The original is only ever read from here on — never written.
    # Off the event loop: extraction is CPU-bound for a text-layer document, but a
    # scanned one now falls through to Gemini Vision, which blocks on the shared
    # rate limiter (whose lock is held across a sleep, deliberately). Left inline,
    # one scanned upload would stall every other request — health checks and
    # search included — for the limiter's interval plus the round trip.
    try:
        result = await run_in_threadpool(file_parser.extract_text, stored_path)
    except file_parser.UnsupportedFileError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except Exception as exc:  # parser blew up on a corrupt file
        logger.exception("Extraction failed for %s", stored_path)
        raise HTTPException(status_code=422, detail=f"Extraction failed: {exc}") from exc

    rel_path = str(stored_path.relative_to(settings.upload_dir.parent))
    upload_date = storage.now_iso()

    # Classify. categorize() never raises — a failure degrades to a filename-based
    # guess with confidence 0.0 rather than losing the upload. It blocks on a
    # network call and the rate limiter, so keep it off the event loop.
    category_result = await run_in_threadpool(categorizer.categorize, result.text, filename)

    warnings = list(result.warnings)
    if category_result.confidence == 0.0:
        warnings.append("Categorization is unverified — review suggested.")

    # Extracted text/metadata are stored separately from the original.
    manifest = storage.DocumentManifest(
        id=doc_id,
        filename=filename,
        stored_path=rel_path,
        file_type=result.file_type,
        checksum=checksum,
        size_bytes=len(contents),
        upload_date=upload_date,
        extraction={
            "text": result.text,
            "method": result.method,
            "char_count": result.char_count,
            "used_ocr": result.used_ocr,
            "warnings": result.warnings,
        },
    )
    storage.write_manifest(manifest, stored_path)

    # Persist to SQLite. `extracted_date` stays null when no date was found —
    # the timeline falls back to upload_date at read time, which keeps "known
    # date" distinguishable from "assumed date" (plan.md § Risk Mitigation).
    try:
        await run_in_threadpool(
            database.insert_document,
            doc_id=doc_id,
            user_id=user_id,
            filename=filename,
            original_path=rel_path,
            file_type=result.file_type,
            checksum=checksum,
            raw_text=result.text,
            upload_date=upload_date,
            document_type=category_result.document_type,
            category=category_result.category,
            title=category_result.title,
            summary=category_result.summary,
            extracted_date=category_result.date,
            confidence=category_result.confidence,
            metadata=_extraction_metadata(result, size_bytes=len(contents)),
            skills=category_result.skills,
            organizations=category_result.organizations,
            people=category_result.people,
            tags=category_result.tags,
        )
    except Exception as exc:
        # The original and its sidecar are already safely on disk, so the
        # preservation guarantee holds — but the document would be invisible to
        # search and the timeline. Surface that rather than reporting success.
        # Log the detail server-side; the client gets a generic message so
        # internal paths and schema details are not echoed back over HTTP.
        logger.exception("Database write failed for %s (%s)", filename, doc_id)
        raise HTTPException(
            status_code=500,
            detail="File was stored successfully but could not be indexed.",
        ) from exc

    # Embed for semantic search. Best-effort: the row is already persisted, so a
    # failure here leaves the document searchable-later, not lost.
    await _index_document(
        doc_id=doc_id, title=category_result.title, raw_text=result.text,
        user_id=user_id,
    )

    return ExtractionResponse(
        id=doc_id,
        filename=filename,
        stored_path=rel_path,
        file_type=result.file_type,
        method=result.method,
        char_count=result.char_count,
        used_ocr=result.used_ocr,
        checksum=checksum,
        size_bytes=len(contents),
        warnings=warnings,
        text_preview=_preview(result.text),
        categorization=_to_response(category_result, upload_date),
        extraction_degraded_reason=result.degraded.reason if result.degraded else None,
        extraction_retryable=bool(result.degraded and result.degraded.retryable),
    )


@router.post("/documents/{doc_id}/recategorize", response_model=CategorizationResponse)
async def recategorize(
    doc_id: str, user_id: str = Depends(current_user)
) -> CategorizationResponse:
    """Re-run categorization over a document's preserved text (the retry path).

    The UI offers this only on a *retryable* degradation (a quota wall, a
    timeout) via the item B contract — a re-run when the quota has refilled
    turns a filename-based fallback into a real classification without a
    re-upload. categorize() still never raises, so a retry that also fails just
    returns another degraded result with its reason; the original is untouched.
    """
    doc = await run_in_threadpool(database.get_document, doc_id)
    if doc is None or doc.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found.")

    raw_text = doc.get("raw_text") or ""
    filename = doc.get("filename") or ""
    result = await run_in_threadpool(categorizer.categorize, raw_text, filename)

    stored_category = await run_in_threadpool(
        database.update_categorization,
        doc_id,
        document_type=result.document_type,
        category=result.category,
        title=result.title,
        summary=result.summary,
        extracted_date=result.date,
        confidence=result.confidence,
        skills=result.skills,
        organizations=result.organizations,
        people=result.people,
        tags=result.tags,
    )

    # The write is what decides the category — a manual override survives a
    # re-run (see database.update_categorization), so the fresh model answer is
    # not necessarily what is stored. Report what is, or the card contradicts
    # the row it was just written from.
    if stored_category != result.category:
        result = result.model_copy(update={"category": stored_category})

    # The title is prepended to each embedded chunk, so a changed title means the
    # vectors are stale — re-index. Best-effort, as on the ingest paths.
    await _index_document(
        doc_id=doc_id, title=result.title, raw_text=raw_text, user_id=user_id
    )

    upload_date = doc.get("upload_date") or storage.now_iso()
    return _to_response(result, upload_date)


@router.post("/documents/{doc_id}/reextract", response_model=ReExtractionResponse)
async def reextract(
    doc_id: str, user_id: str = Depends(current_user)
) -> ReExtractionResponse:
    """Re-run *extraction* over the preserved original, then re-classify.

    The gap this closes: extraction failure was **terminal**. `/recategorize`
    re-runs the model over `raw_text`, which is empty exactly when Vision hit the
    wall — so it re-classified nothing and the only cure was delete-and-reupload,
    losing the upload date and the document id. Rare at the 1500/day this repo
    once assumed; **normal at 20/day**, where one scanned upload is 2 calls and
    the daily ceiling is reached in a single sitting.

    The original is what makes this possible: it was stored byte-for-byte and is
    only ever *read*, so the pixels are still there to try again. This re-reads
    them, and rewrites only what was derived (raw_text, the sidecar's extraction
    block, the vectors).

    Quota-aware by design, because the endpoint exists for a quota problem:
      - a run that recovers nothing spends **zero** further calls — it does not
        classify an empty string, it records why and returns 200;
      - a run that recovers text classifies it (1 call), because text recovered
        into a document still wearing a filename guess is the broken state this
        is repairing;
      - a run whose text is unchanged and whose categorization was not degraded
        skips the call entirely.

    409, not 404, for a document with no original: a URL or text entry exists
    and is fine, it simply has nothing to re-extract from (`original_path` is
    "" — see CLAUDE.md). Re-fetching a URL is a *different* operation, and not
    this one: the stored checksum pins which snapshot was ingested, so silently
    replacing it with today's page would break that guarantee.
    """
    doc = await run_in_threadpool(database.get_document, doc_id)
    if doc is None or doc.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found.")

    if not doc.get("original_path"):
        raise HTTPException(
            status_code=409,
            detail=(
                "This document has no stored original to re-extract from "
                "(it was ingested from a URL or entered as text)."
            ),
        )

    found = storage.find_by_id(doc_id, user_id)
    if found is None:
        raise HTTPException(
            status_code=409,
            detail="The original file for this document is no longer on disk.",
        )
    stored_path, manifest = found

    # Refuse to derive anything from an original that no longer matches its
    # checksum, exactly as the download path does. Re-extracting a corrupted
    # file would overwrite good text with garbage and report success.
    if not await run_in_threadpool(storage.verify_integrity, stored_path, manifest):
        logger.error("Integrity check FAILED for %s — refusing to re-extract.", doc_id)
        raise HTTPException(
            status_code=500,
            detail="Stored file failed its integrity check; it may be corrupted.",
        )

    # Off the event loop for the same reason as /upload: a scanned document
    # reaches Vision, which blocks on the shared rate limiter.
    try:
        result = await run_in_threadpool(file_parser.extract_text, stored_path)
    except file_parser.UnsupportedFileError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Re-extraction failed for %s", stored_path)
        raise HTTPException(status_code=422, detail=f"Extraction failed: {exc}") from exc

    text = result.text.strip()
    reason = result.degraded.reason if result.degraded else None
    retryable = bool(result.degraded and result.degraded.retryable)
    warnings = list(result.warnings)

    if not text:
        # Nothing recovered. Record *this* attempt's reason so the UI stops
        # showing the stale one, and leave raw_text untouched — overwriting it
        # with "" would destroy text a previous run had recovered.
        await run_in_threadpool(
            database.update_extraction,
            doc_id,
            raw_text=None,
            extraction=_extraction_metadata(result),
            user_id=user_id,
        )
        return ReExtractionResponse(
            id=doc_id,
            filename=doc.get("filename") or manifest.filename,
            file_type=result.file_type,
            method=result.method,
            char_count=result.char_count,
            used_ocr=result.used_ocr,
            recovered=False,
            recategorized=False,
            warnings=warnings,
            extraction_degraded_reason=reason,
            extraction_retryable=retryable,
        )

    # Text recovered. The sidecar is derived data, so it is rewritten in place —
    # the original beside it is not touched. Its identity fields (checksum,
    # size, filename, upload_date) are carried over from the stored manifest,
    # never recomputed, so the integrity record still describes the upload.
    manifest.extraction = {
        "text": result.text,
        "method": result.method,
        "char_count": result.char_count,
        "used_ocr": result.used_ocr,
        "warnings": result.warnings,
        "reextracted_at": storage.now_iso(),
    }
    try:
        await run_in_threadpool(storage.write_manifest, manifest, stored_path)
    except Exception:
        # Best-effort, like indexing: SQLite is what the app reads. A stale
        # sidecar is a weaker integrity record, not a lost document.
        logger.exception("Sidecar rewrite failed for %s", doc_id)

    previous_text = doc.get("raw_text") or ""
    was_degraded = (doc.get("confidence") or 0.0) == 0.0
    should_categorize = text != previous_text.strip() or was_degraded

    await run_in_threadpool(
        database.update_extraction,
        doc_id,
        raw_text=result.text,
        extraction=_extraction_metadata(result),
        user_id=user_id,
    )

    categorization: CategorizationResponse | None = None
    title = doc.get("title") or ""
    if should_categorize:
        # Never raises; a failure here still leaves the recovered text stored.
        category_result = await run_in_threadpool(
            categorizer.categorize, result.text, doc.get("filename") or ""
        )
        stored_category = await run_in_threadpool(
            database.update_categorization,
            doc_id,
            document_type=category_result.document_type,
            category=category_result.category,
            title=category_result.title,
            summary=category_result.summary,
            extracted_date=category_result.date,
            confidence=category_result.confidence,
            skills=category_result.skills,
            organizations=category_result.organizations,
            people=category_result.people,
            tags=category_result.tags,
        )
        # A manual override survives, so report what was stored, not what the
        # model said — same rule as /recategorize.
        if stored_category != category_result.category:
            category_result = category_result.model_copy(
                update={"category": stored_category}
            )
        if category_result.confidence == 0.0:
            warnings.append("Categorization is unverified — review suggested.")
        title = category_result.title
        categorization = _to_response(
            category_result, doc.get("upload_date") or storage.now_iso()
        )

    # The text changed, so the vectors are stale — re-index. Best-effort.
    await _index_document(
        doc_id=doc_id, title=title, raw_text=result.text, user_id=user_id
    )

    return ReExtractionResponse(
        id=doc_id,
        filename=doc.get("filename") or manifest.filename,
        file_type=result.file_type,
        method=result.method,
        char_count=result.char_count,
        used_ocr=result.used_ocr,
        recovered=True,
        recategorized=should_categorize,
        warnings=warnings,
        text_preview=_preview(result.text),
        categorization=categorization,
    )


async def _categorize_and_store(
    *,
    doc_id: str,
    text: str,
    filename: str,
    file_type: str,
    user_id: str = DEFAULT_USER,
    source_url: str = "",
    metadata: dict | None = None,
    date_fallback: str | None = None,
) -> tuple[Categorization, list[str], str]:
    """Classify fileless text and persist it.

    Returns (categorization, warnings, upload_date). The upload date comes back
    because the caller needs it to resolve the displayed date — the row and the
    response must agree on which timestamp the fallback was measured against.

    Shared by the URL and written-response paths. Both differ from `/upload` in
    one way that matters: there is **no original file**, so there is no sidecar
    and nothing to preserve byte-for-byte (plan.md §4 Module 1 — a text entry is
    explicitly stored with no original). `original_path` is the empty string
    rather than NULL, keeping the schema's NOT NULL intact so every reader has a
    single code path.

    `checksum` is the SHA-256 of the extracted text, not of an original file.
    For a written response the text *is* the artifact; for a URL it pins which
    snapshot of a page was ingested, since the page can change under us.
    """
    warnings: list[str] = []
    upload_date = storage.now_iso()

    # categorize() never raises — worst case is a filename-based guess at
    # confidence 0.0. Blocks on network + the rate limiter, so keep it off the
    # event loop.
    result = await run_in_threadpool(categorizer.categorize, text, filename)
    if result.confidence == 0.0:
        warnings.append("Categorization is unverified — review suggested.")

    # Resolve the date once, here, so the row and the response cannot disagree.
    # A date read out of the content wins: it describes the achievement.
    # `date_fallback` is the source's own metadata (a repo's creation date) —
    # still *known*, so it belongs in extracted_date rather than being left NULL
    # for the timeline's upload-date fallback to invent.
    if not result.date and date_fallback:
        result = result.model_copy(update={"date": date_fallback})

    try:
        await run_in_threadpool(
            database.insert_document,
            doc_id=doc_id,
            user_id=user_id,
            filename=filename,
            original_path="",
            file_type=file_type,
            source_url=source_url,
            checksum=storage.sha256_bytes(text.encode("utf-8")),
            raw_text=text,
            upload_date=upload_date,
            document_type=result.document_type,
            category=result.category,
            title=result.title,
            summary=result.summary,
            extracted_date=result.date,
            confidence=result.confidence,
            metadata=metadata or {},
            skills=result.skills,
            organizations=result.organizations,
            people=result.people,
            tags=result.tags,
        )
    except Exception as exc:
        # Unlike /upload there is no file on disk, so a failed write means the
        # document is gone entirely. Never report success.
        logger.exception("Database write failed for %s (%s)", filename, doc_id)
        raise HTTPException(
            status_code=500, detail="Content could not be indexed."
        ) from exc

    # Fileless documents (URL / text entry) embed the same way — they have
    # raw_text even without an original file. Best-effort, as above.
    await _index_document(
        doc_id=doc_id, title=result.title, raw_text=text, user_id=user_id
    )

    return result, warnings, upload_date


@router.post("/ingest-url", response_model=UrlIngestResponse)
async def ingest_url(
    payload: UrlIngestRequest, user_id: str = Depends(current_user)
) -> UrlIngestResponse:
    # Scraping blocks on network I/O; keep it off the event loop.
    try:
        result = await run_in_threadpool(url_scraper.scrape_url, payload.url)
    except ValueError as exc:
        # Covers BlockedUrlError (bad scheme, non-public destination, oversized
        # response) — all caller errors.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("URL ingestion failed for %s", payload.url)
        raise HTTPException(status_code=422, detail=f"URL ingestion failed: {exc}") from exc

    if not result.text.strip():
        # Nothing was extracted, so there is nothing to categorize or store.
        # plan.md § Risk Mitigation: degrade gracefully and tell the user to upload manually.
        raise HTTPException(
            status_code=422,
            detail=(
                "No readable content could be extracted from that URL. "
                "Try uploading the content as a file instead."
            ),
        )

    doc_id = uuid.uuid4().hex
    category_result, warnings, upload_date = await _categorize_and_store(
        doc_id=doc_id,
        text=result.text,
        # The page title is the best filename stand-in; the URL is the fallback.
        filename=result.title or result.url,
        file_type="url",
        user_id=user_id,
        source_url=result.url,
        metadata={
            "source_type": result.source_type,
            "scrape_warnings": result.warnings,
            "char_count": len(result.text),
            "source_date": result.source_date,
            # Persisted so a later reader (the Phase 6 timeline, the graph) can
            # render a repo as a repo without re-scraping it.
            "details": result.details,
        },
        date_fallback=result.source_date,
    )

    return UrlIngestResponse(
        id=doc_id,
        url=result.url,
        title=category_result.title or result.title,
        source_type=result.source_type,
        char_count=len(result.text),
        warnings=result.warnings + warnings,
        text_preview=_preview(result.text),
        categorization=_to_response(category_result, upload_date),
        details=result.details,
    )


@router.post("/ingest-text", response_model=TextIngestResponse)
async def ingest_text(
    payload: TextIngestRequest, user_id: str = Depends(current_user)
) -> TextIngestResponse:
    """Ingest a written response — an achievement with no supporting document."""
    try:
        entry = text_entry.prepare(payload.text)
    except text_entry.InvalidTextEntry as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    filename = text_entry.derive_filename(entry.text)
    doc_id = uuid.uuid4().hex
    category_result, warnings, upload_date = await _categorize_and_store(
        doc_id=doc_id,
        text=entry.text,
        filename=filename,
        file_type="text_entry",
        user_id=user_id,
        metadata={"char_count": entry.char_count, "entered_manually": True},
    )

    return TextIngestResponse(
        id=doc_id,
        filename=filename,
        file_type="text_entry",
        char_count=entry.char_count,
        warnings=warnings,
        text_preview=_preview(entry.text),
        categorization=_to_response(category_result, upload_date),
    )
