"""SQLite setup and queries.

Connections are opened per-operation rather than shared: SQLite connections are
not safe to reuse across threads, and FastAPI runs sync endpoints in a thread
pool. Opening a connection is cheap for a local file database.

The `documents` table is the Phase 2 home for metadata that Phase 1 kept in
`{file}.meta.json` sidecars. The sidecar is still written — it remains the
on-disk source of truth for integrity, so an original plus its sidecar can be
verified without the database.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from config import settings

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """Yield a connection with row access by column name.

    Commits on clean exit, rolls back if the body raises.
    """
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with get_connection() as conn:
        conn.executescript(schema)
    logger.info("Database ready at %s", settings.db_path)


# --- Writes ---------------------------------------------------------------


def insert_document(
    *,
    doc_id: str,
    user_id: str,
    filename: str,
    original_path: str,
    file_type: str,
    checksum: str,
    raw_text: str,
    upload_date: str,
    source_url: str = "",
    document_type: str | None = None,
    category: str | None = None,
    title: str | None = None,
    summary: str | None = None,
    extracted_date: str | None = None,
    confidence: float | None = None,
    metadata: dict[str, Any] | None = None,
    skills: list[str] | None = None,
    organizations: list[str] | None = None,
    people: list[str] | None = None,
    tags: list[str] | None = None,
) -> None:
    """Persist a document plus its extracted entities and tags in one transaction.

    Entities and tags are written as rows (not just JSON) because Module 3's
    relationship engine joins documents on shared entity values.
    """
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO documents (
                id, user_id, filename, original_path, file_type, source_url,
                checksum, document_type, category, title, summary,
                extracted_date, upload_date, raw_text, confidence, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id, user_id, filename, original_path, file_type, source_url,
                checksum, document_type, category, title, summary,
                extracted_date, upload_date, raw_text, confidence,
                json.dumps(metadata or {}),
            ),
        )

        entity_rows = [
            (uuid.uuid4().hex, doc_id, entity_type, value)
            for entity_type, values in (
                ("skill", skills or []),
                ("organization", organizations or []),
                ("person", people or []),
            )
            for value in values
            if value and value.strip()
        ]
        if entity_rows:
            conn.executemany(
                "INSERT INTO entities (id, document_id, entity_type, entity_value)"
                " VALUES (?, ?, ?, ?)",
                entity_rows,
            )

        tag_rows = [(doc_id, tag.strip()) for tag in (tags or []) if tag and tag.strip()]
        if tag_rows:
            conn.executemany(
                "INSERT INTO tags (document_id, tag) VALUES (?, ?)", tag_rows
            )


def set_embedding_id(doc_id: str, embedding_id: str) -> None:
    """Mark a document as indexed in the vector store.

    Set only after `ai/embeddings.add_document` succeeds, so a NULL
    `embedding_id` reliably means "not yet in Chroma" — which is exactly what
    the startup sync in `ensure_synced` keys off to heal a partial index.
    """
    with get_connection() as conn:
        conn.execute(
            "UPDATE documents SET embedding_id = ? WHERE id = ?",
            (embedding_id, doc_id),
        )


# --- Reads ----------------------------------------------------------------


def get_document(doc_id: str) -> dict[str, Any] | None:
    """Fetch one document with its entities and tags attached."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if row is None:
            return None

        doc = _row_to_dict(row)
        entities = conn.execute(
            "SELECT entity_type, entity_value FROM entities WHERE document_id = ?",
            (doc_id,),
        ).fetchall()
        tags = conn.execute(
            "SELECT tag FROM tags WHERE document_id = ?", (doc_id,)
        ).fetchall()

    doc["skills"] = [e["entity_value"] for e in entities if e["entity_type"] == "skill"]
    doc["organizations"] = [
        e["entity_value"] for e in entities if e["entity_type"] == "organization"
    ]
    doc["people"] = [e["entity_value"] for e in entities if e["entity_type"] == "person"]
    doc["tags"] = [t["tag"] for t in tags]
    return doc


def list_documents(
    user_id: str = "demo",
    category: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List documents newest-first, optionally filtered by category.

    `raw_text` is omitted — listings can hold many documents and the full text
    is large. Use get_document() when the text is actually needed.
    """
    sql = """
        SELECT id, user_id, filename, original_path, file_type, source_url,
               checksum, document_type, category, title, summary,
               extracted_date, upload_date, confidence, metadata_json
        FROM documents
        WHERE user_id = ?
    """
    params: list[Any] = [user_id]
    if category:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY upload_date DESC LIMIT ?"
    params.append(limit)

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(row) for row in rows]


def documents_for_indexing() -> list[dict[str, Any]]:
    """Every document with just the fields the vector index needs.

    Unlike `list_documents`, this includes `raw_text` — it is what gets embedded
    — and omits the display/date machinery. Used to (re)build the Chroma store
    from SQLite, the source of truth, so a lost or corrupt vector store is fully
    regenerable (see `ai/embeddings.py::reindex`).
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, user_id, title, raw_text FROM documents"
        ).fetchall()
    return [dict(row) for row in rows]


def resolve_date(
    extracted_date: str | None, upload_date: str | None
) -> tuple[str | None, str]:
    """Collapse (extracted, upload) into (effective_date, date_source).

    plan.md § Risk Mitigation has two halves: fall back to the upload date when no date was
    found, **and flag it for user review**. Everything so far implemented only
    the first half, which is how a repo created in 2011 ends up sitting on the
    timeline at the moment it was ingested — silently wrong, and plausible
    enough that nobody notices.

    `extracted_date` stays NULL when nothing was found (a deliberate Phase 2
    choice) precisely so the two cases stay distinguishable here. This is the
    single place that collapses them, so no reader can apply the fallback while
    forgetting the flag — which is exactly the mistake the timeline was set up
    to make.

    Public because the ingest endpoints need the same answer at *write* time —
    they return a card to the user before any read path runs, and computing the
    flag a second time in the route layer is precisely the duplication this
    function exists to prevent.

    `effective_date` is trimmed to "YYYY-MM" to match the granularity of
    `extracted_date`; mixed-granularity values still sort correctly as strings
    ("2024" < "2024-03" < "2025").
    """
    if extracted_date:
        return extracted_date, "extracted"

    upload = upload_date or ""
    return (upload[:7] if len(upload) >= 7 else (upload or None)), "assumed"


def _resolve_date(doc: dict[str, Any]) -> None:
    """Attach `effective_date` and `date_source` to a document dict, in place."""
    doc["effective_date"], doc["date_source"] = resolve_date(
        doc.get("extracted_date"), doc.get("upload_date")
    )


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    doc = dict(row)
    raw = doc.pop("metadata_json", None)
    try:
        doc["metadata"] = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        logger.warning("Malformed metadata_json for document %s", doc.get("id"))
        doc["metadata"] = {}
    _resolve_date(doc)
    return doc
