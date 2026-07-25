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
from ai import embeddings
from db import database
from models.document import CATEGORIES, DocumentDetail, DocumentSummary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])

DEFAULT_USER = "demo"


class IntegrityResponse(BaseModel):
    id: str
    filename: str
    checksum: str
    size_bytes: int
    verified: bool


class DeleteResponse(BaseModel):
    id: str
    deleted: bool


class CategoryRequest(BaseModel):
    category: str


class CategoryResponse(BaseModel):
    id: str
    category: str
    category_source: str


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


@router.patch("/{doc_id}/category", response_model=CategoryResponse)
def set_category(doc_id: str, payload: CategoryRequest) -> CategoryResponse:
    """Manually override a document's category (plan.md § Risk Mitigation).

    Categorization is a Gemini judgment against a fixed six-category taxonomy,
    and some documents genuinely have no clean slot — a whole GitHub *profile*
    is not a project, a skill, or a certification, and lands in *Projects*.
    Rather than fight the model, the user gets the last word.

    PATCH, not POST: this is a partial update of the document resource, and it
    changes one field. The category must be one of the six in the taxonomy — an
    override may correct a classification, not invent a seventh category that
    the palette, the filter chips, and the graph know nothing about.

    Deliberately narrow. It does not re-run Gemini (that is `/recategorize`), it
    does not touch the original, the extracted text, or the extracted entities,
    and it does not re-index: the graph computes its edges from `category` on
    read, so the override takes effect on the next graph load by itself.
    """
    category = payload.category.strip()
    if category not in CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Category must be one of: {', '.join(sorted(CATEGORIES))}.",
        )

    updated = database.set_category(doc_id, category, DEFAULT_USER)
    if not updated:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found.")

    return CategoryResponse(
        id=doc_id,
        category=category,
        category_source=database.MANUAL_CATEGORY_SOURCE,
    )


@router.delete("/{doc_id}", response_model=DeleteResponse)
def delete_document(doc_id: str) -> DeleteResponse:
    """Delete a document from every store: SQLite (the row plus its entity/tag
    rows), the vector index, and — for an uploaded file — the original and its
    sidecar. Returns 404 if the document does not exist for this user.

    The authoritative SQLite row is removed first; the derived stores are then
    cleaned best-effort. If one of those hiccups it leaves a harmless orphan
    (an unindexed vector, or a file with no row), never a dangling record that
    outlives its document — search hydrates from SQLite, so an orphan vector
    cannot surface, and the download path needs a row that no longer exists.
    """
    existed = database.delete_document(doc_id, DEFAULT_USER)
    if not existed:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found.")

    try:
        embeddings.delete_document(doc_id)
    except Exception:
        logger.exception("Failed to delete embeddings for %s", doc_id)
    try:
        storage.delete_original(doc_id, DEFAULT_USER)
    except Exception:
        logger.exception("Failed to delete original file for %s", doc_id)

    return DeleteResponse(id=doc_id, deleted=True)


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
