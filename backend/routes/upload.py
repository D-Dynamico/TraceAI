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
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import storage
from ai import categorizer
from config import settings
from db import database
from ingestion import file_parser, url_scraper
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


def _preview(text: str, limit: int = 800) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _to_response(result: Categorization) -> CategorizationResponse:
    return CategorizationResponse(**result.model_dump())


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
    # date" distinguishable from "assumed date" (plan.md §10).
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
        categorization=_to_response(category_result),
    )


@router.post("/ingest-url", response_model=UrlIngestResponse)
async def ingest_url(payload: UrlIngestRequest) -> UrlIngestResponse:
    try:
        result = url_scraper.scrape_url(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("URL ingestion failed for %s", payload.url)
        raise HTTPException(status_code=422, detail=f"URL ingestion failed: {exc}") from exc

    return UrlIngestResponse(
        id=uuid.uuid4().hex,
        url=result.url,
        title=result.title,
        source_type=result.source_type,
        char_count=len(result.text),
        warnings=result.warnings,
        text_preview=_preview(result.text),
    )
