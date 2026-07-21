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

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

import storage
from ai import categorizer
from config import settings
from db import database
from ingestion import file_parser, text_entry, url_scraper
from models.document import Categorization

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ingestion"])

# No auth/multi-user yet; everything lands under a single demo user.
DEFAULT_USER = "demo"


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


def _to_response(result: Categorization, upload_date: str) -> CategorizationResponse:
    effective_date, date_source = database.resolve_date(result.date, upload_date)
    return CategorizationResponse(
        **result.model_dump(),
        effective_date=effective_date,
        date_source=date_source,
    )


@router.post("/upload", response_model=ExtractionResponse)
async def upload_file(file: UploadFile = File(...)) -> ExtractionResponse:
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
            DEFAULT_USER, doc_id, filename, contents
        )
    except IOError as exc:
        logger.error("Failed to store original for %s: %s", filename, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Extract text. The original is only ever read from here on — never written.
    try:
        result = file_parser.extract_text(stored_path)
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
            user_id=DEFAULT_USER,
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
            metadata={
                "method": result.method,
                "used_ocr": result.used_ocr,
                "size_bytes": len(contents),
                "extraction_warnings": result.warnings,
            },
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
    )


async def _categorize_and_store(
    *,
    doc_id: str,
    text: str,
    filename: str,
    file_type: str,
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
            user_id=DEFAULT_USER,
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

    return result, warnings, upload_date


@router.post("/ingest-url", response_model=UrlIngestResponse)
async def ingest_url(payload: UrlIngestRequest) -> UrlIngestResponse:
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
async def ingest_text(payload: TextIngestRequest) -> TextIngestResponse:
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
