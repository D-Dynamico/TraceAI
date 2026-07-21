"""Document listing and retrieval.

Two responsibilities:

  - **Browse** the categorized metadata in SQLite (list + detail).
  - **Download** originals in their native format. This is the second half of
    the "Original Format Preservation" guarantee: every stored original comes
    back byte-for-byte, and its SHA-256 checksum is re-verified before it is
    served. A mismatch is surfaced as a 500 rather than quietly handing back a
    corrupted file.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

import storage
from db import database
from models.document import DocumentDetail, DocumentSummary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])

DEFAULT_USER = "demo"


class IntegrityResponse(BaseModel):
    id: str
    filename: str
    checksum: str
    size_bytes: int
    verified: bool


def _lookup(doc_id: str):
    found = storage.find_by_id(doc_id, DEFAULT_USER)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found.")
    return found


@router.get("", response_model=list[DocumentSummary])
def list_documents(
    category: str | None = Query(default=None, description="Filter by category"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[DocumentSummary]:
    """List categorized documents, newest first."""
    rows = database.list_documents(user_id=DEFAULT_USER, category=category, limit=limit)
    for row in rows:
        # Empty original_path is the fileless (url / text_entry) convention; the
        # column is NOT NULL. bool("") is False — no original to download.
        row["has_original"] = bool(row.get("original_path"))
    return [DocumentSummary.model_validate(row) for row in rows]


@router.get("/{doc_id}", response_model=DocumentDetail)
def get_document(doc_id: str) -> DocumentDetail:
    """Fetch one document with its entities, tags, and extracted text."""
    row = database.get_document(doc_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found.")
    return DocumentDetail.model_validate(row)


@router.get("/{doc_id}/verify", response_model=IntegrityResponse)
def verify_document(doc_id: str) -> IntegrityResponse:
    """Recompute the stored file's checksum and report whether it still matches."""
    stored_path, manifest = _lookup(doc_id)
    return IntegrityResponse(
        id=manifest.id,
        filename=manifest.filename,
        checksum=manifest.checksum,
        size_bytes=manifest.size_bytes,
        verified=storage.verify_integrity(stored_path, manifest),
    )


@router.get("/{doc_id}/download")
def download_document(doc_id: str) -> FileResponse:
    """Serve the original file unchanged, after verifying its integrity."""
    stored_path, manifest = _lookup(doc_id)

    if not storage.verify_integrity(stored_path, manifest):
        logger.error(
            "Integrity check FAILED for %s (%s) — refusing to serve.",
            doc_id, manifest.filename,
        )
        raise HTTPException(
            status_code=500,
            detail="Stored file failed its integrity check; it may be corrupted.",
        )

    # filename= restores the user's original name on download.
    return FileResponse(
        path=stored_path,
        filename=manifest.filename,
        headers={"X-Content-SHA256": manifest.checksum},
    )
