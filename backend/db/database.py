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

# Who chose a document's category. Stored inside `metadata_json` rather than as
# a column: it describes the *provenance* of one field, not the document, and
# `metadata_json` is already where per-document extras live (extraction method,
# scrape warnings). "manual" means the user overrode Gemini's answer — a fixed
# six-category taxonomy cannot fit everything (a whole GitHub *profile* has no
# clean slot and lands in Projects), so plan.md § Risk Mitigation calls for an
# explicit override rather than fighting the model.
MANUAL_CATEGORY_SOURCE = "manual"
AI_CATEGORY_SOURCE = "ai"


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
    """Create tables if they don't exist. Safe to call on every startup.

    **Migrations run first.** They used to run after the schema script, which
    was the wrong order and broke startup outright on any database predating
    the `career_paths.user_id` column: the script's
    `CREATE INDEX ... ON career_paths(user_id)` referenced a column the
    migration had not added yet, so `init_db` raised "no such column: user_id"
    and the app would not boot. It went unnoticed because the deploy target's
    disk is ephemeral — every deployed database is brand new, and only a
    long-lived developer one is old enough to hit it.

    Running first is safe both ways: on a fresh database the tables do not exist
    yet, `PRAGMA table_info` returns nothing, and every step is a no-op before
    the script creates everything correctly.
    """
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with get_connection() as conn:
        _migrate(conn)
        conn.executescript(schema)
    logger.info("Database ready at %s", settings.db_path)


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive column migrations for databases created by an earlier schema.

    `CREATE TABLE IF NOT EXISTS` is a no-op on an existing table, so a column
    added to schema.sql never reaches a database that already has that table —
    it appears on a fresh deploy (Render's disk is ephemeral) and is missing on
    every developer machine, which is the worst possible split. Each step here
    must be idempotent and safe to run on every startup.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(career_paths)")}
    if not columns:
        return  # fresh database — the schema script below creates it correctly
    if "user_id" not in columns:
        # Existing rows were inferred for the shared dataset, so they belong to
        # it — the DEFAULT backfills them rather than orphaning them under an id
        # no visitor will ever send.
        conn.execute(
            "ALTER TABLE career_paths ADD COLUMN user_id TEXT NOT NULL DEFAULT 'demo'"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_career_paths_user ON career_paths(user_id)"
        )
        logger.info("Migrated career_paths: added user_id.")


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


def replace_career_paths(paths: list[dict[str, Any]], user_id: str = "demo") -> None:
    """Persist one user's inferred career paths, replacing their previous set.

    Inference runs over the whole profile at once, so its output *is* the
    complete set — the user's rows are cleared and rewritten rather than
    appended to, which keeps a re-run from stacking stale trajectories.
    Supporting document ids and skill gaps are stored as JSON in the existing
    `evidence` / `skill_gaps` columns (persisted because the Gemini call that
    produced them is not free to repeat on every graph read).

    **The DELETE is scoped to `user_id`.** Unscoped, one visitor running
    inference would wipe every other visitor's paths — the same class of bug as
    an unscoped read, but destructive rather than merely leaky.
    """
    with get_connection() as conn:
        conn.execute("DELETE FROM career_paths WHERE user_id = ?", (user_id,))
        conn.executemany(
            """
            INSERT INTO career_paths
                (id, user_id, title, match_score, evidence, skill_gaps)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    p["id"],
                    user_id,
                    p["title"],
                    p.get("match_score"),
                    json.dumps({"doc_ids": p.get("evidence_doc_ids") or []}),
                    json.dumps(p.get("skill_gaps") or []),
                )
                for p in paths
            ],
        )


def list_career_paths(user_id: str = "demo") -> list[dict[str, Any]]:
    """Read one user's career paths, with evidence ids and skill gaps parsed."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, match_score, evidence, skill_gaps FROM career_paths"
            " WHERE user_id = ?",
            (user_id,),
        ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            evidence = json.loads(row["evidence"]) if row["evidence"] else {}
        except json.JSONDecodeError:
            evidence = {}
        try:
            gaps = json.loads(row["skill_gaps"]) if row["skill_gaps"] else []
        except json.JSONDecodeError:
            gaps = []
        out.append(
            {
                "id": row["id"],
                "title": row["title"],
                "match_score": row["match_score"],
                "evidence_doc_ids": evidence.get("doc_ids", []),
                "skill_gaps": gaps,
            }
        )
    return out


def update_categorization(
    doc_id: str,
    *,
    user_id: str,
    document_type: str | None,
    category: str | None,
    title: str | None,
    summary: str | None,
    extracted_date: str | None,
    confidence: float | None,
    skills: list[str] | None = None,
    organizations: list[str] | None = None,
    people: list[str] | None = None,
    tags: list[str] | None = None,
) -> str | None:
    """Overwrite a document's AI metadata after a re-run (the retry path).

    Returns the category actually stored, which is the caller's value unless a
    manual override was protected below. The caller needs it: the response it
    builds from its own fresh `Categorization` would otherwise name a category
    the row does not hold, and the UI believes the response. Returning it keeps
    the decision in exactly one place instead of asking the route to re-derive
    it — a second copy of this rule is a second thing to get wrong, and it would
    mask the loss of this one.

    Updates the categorization columns and *replaces* the entity/tag rows —
    Module 3 joins on those, so a re-categorization that changed the skills must
    not leave the old ones behind. The original file, checksum, and raw_text are
    never touched: re-categorizing re-reads the same preserved text, it does not
    alter it (see CLAUDE.md — originals are never modified).

    A **manually overridden category survives the re-run.** The user picked it
    because the model's answer was wrong; silently replacing it with a fresh
    model answer would undo their correction the next time a retryable
    degradation is retried. Everything else the model produces (title, summary,
    skills, date) is still overwritten — only the field the user took ownership
    of is protected. Held here rather than in the route so no future caller can
    forget it.

    **`user_id` is required and is part of the WHERE clause.** The routes check
    ownership before calling, but that check and this write were two statements
    with a gap between them — the document could be deleted, or its owner change,
    in between. Scoping the write itself closes the gap and means the rule is
    enforced where the row is touched rather than in each route that remembers.
    """
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT category, metadata_json FROM documents WHERE id = ? AND user_id = ?",
            (doc_id, user_id),
        ).fetchone()
        if existing is None:
            return None  # gone, or never theirs
        if _category_source(existing["metadata_json"], doc_id) == MANUAL_CATEGORY_SOURCE:
            category = existing["category"]

        conn.execute(
            """
            UPDATE documents
            SET document_type = ?, category = ?, title = ?, summary = ?,
                extracted_date = ?, confidence = ?
            WHERE id = ? AND user_id = ?
            """,
            (document_type, category, title, summary, extracted_date, confidence, doc_id, user_id),
        )
        conn.execute("DELETE FROM entities WHERE document_id = ?", (doc_id,))
        conn.execute("DELETE FROM tags WHERE document_id = ?", (doc_id,))

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

    return category


def update_extraction(
    doc_id: str,
    *,
    raw_text: str | None,
    extraction: dict[str, Any],
    user_id: str = "demo",
) -> bool:
    """Record the result of a *re-extraction*. Returns True if the row existed.

    Companion to `update_categorization`, one layer upstream: that one replaces
    what the model concluded, this one replaces the text it concludes *from*.
    Extraction failure used to be terminal precisely because no such write
    existed — `/recategorize` re-ran the model over a `raw_text` that was empty
    and stayed empty, so the only cure for a Vision call lost to the daily quota
    was deleting the document and uploading it again.

    `raw_text=None` means **leave the column alone**, which is the failure path:
    a re-extraction that recovered nothing must not overwrite whatever text is
    already stored with an empty string. It still merges `extraction`, so the
    reason shown to the user is this attempt's (`quota`), not the stale one from
    the upload (`no_api_key`).

    `extraction` is the derived-extraction block, built by the route's
    `_extraction_metadata` — the same helper `/upload` writes at ingest, so a
    re-extracted document's metadata is shaped exactly like an uploaded one's
    and no reader meets a key that is present only sometimes.

    Never touches the original, the checksum, or the categorization columns.
    Rewriting derived text is not the forbidden in-place modification of a
    preserved original (CLAUDE.md): the original is re-read, byte-for-byte
    unchanged, and only what was derived from it moves.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT metadata_json FROM documents WHERE id = ? AND user_id = ?",
            (doc_id, user_id),
        ).fetchone()
        if row is None:
            return False

        # Merged, not replaced: `size_bytes` and anything a future ingest path
        # records belong to the upload, not to this re-run.
        metadata = _parse_metadata(row["metadata_json"], doc_id)
        metadata.update(extraction)

        if raw_text is None:
            conn.execute(
                "UPDATE documents SET metadata_json = ? WHERE id = ? AND user_id = ?",
                (json.dumps(metadata), doc_id, user_id),
            )
        else:
            conn.execute(
                "UPDATE documents SET raw_text = ?, metadata_json = ?"
                " WHERE id = ? AND user_id = ?",
                (raw_text, json.dumps(metadata), doc_id, user_id),
            )
    return True


def set_category(doc_id: str, category: str, user_id: str = "demo") -> bool:
    """Manually override a document's category. Returns True if it existed.

    Writes exactly two things: the `category` column and a `category_source`
    marker in `metadata_json`. Nothing else moves — not the original file, not
    `raw_text`, not the checksum, not the entity/tag rows, not `confidence`.
    Confidence is the model's report on its *own* classification; leaving it
    untouched keeps that honest, and `category_source` is what tells a reader the
    category no longer came from the model.

    Scoped to `user_id`, the same isolation boundary `delete_document` and the
    graph enforce.

    The graph and the search filters both read `category` straight from this
    table (edges are computed on read, per Phase 5), so an override re-forms the
    graph — a certificate reclassified as a project stops emitting
    `certifies_skill` edges — with no re-index. The vector store embeds title and
    text, never the category, so it is untouched too.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT metadata_json FROM documents WHERE id = ? AND user_id = ?",
            (doc_id, user_id),
        ).fetchone()
        if row is None:
            return False

        metadata = _parse_metadata(row["metadata_json"], doc_id)
        metadata["category_source"] = MANUAL_CATEGORY_SOURCE
        conn.execute(
            "UPDATE documents SET category = ?, metadata_json = ?"
            " WHERE id = ? AND user_id = ?",
            (category, json.dumps(metadata), doc_id, user_id),
        )
    return True


def delete_document(doc_id: str, user_id: str = "demo") -> bool:
    """Delete a document plus its entity/tag rows. Returns True if it existed.

    Scoped to `user_id` — the same isolation boundary `list_documents` and the
    graph enforce, so one user cannot delete another's document once auth lands
    (plan.md § Stretch Goals). Entities and tags are removed explicitly rather
    than left to `ON DELETE CASCADE`, mirroring `update_categorization` and not
    depending on the per-connection foreign_keys pragma. The vector store and any
    original file are cleaned by the caller (they are derived / on-disk, not part
    of this SQL transaction).
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM documents WHERE id = ? AND user_id = ?",
            (doc_id, user_id),
        ).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM entities WHERE document_id = ?", (doc_id,))
        conn.execute("DELETE FROM tags WHERE document_id = ?", (doc_id,))
        conn.execute(
            "DELETE FROM documents WHERE id = ? AND user_id = ?", (doc_id, user_id)
        )
    return True


def set_embedding_id(doc_id: str, embedding_id: str, *, user_id: str) -> None:
    """Mark a document as indexed in the vector store.

    Set only after `ai/embeddings.add_document` succeeds, so a NULL
    `embedding_id` reliably means "not yet in Chroma".

    Scoped like every other write here: the id comes from the same request that
    supplied `user_id`, so a mismatch means the row is not the caller's and the
    update should touch nothing.
    """
    with get_connection() as conn:
        conn.execute(
            "UPDATE documents SET embedding_id = ? WHERE id = ? AND user_id = ?",
            (embedding_id, doc_id, user_id),
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


def get_documents(doc_ids: list[str], user_id: str) -> dict[str, dict[str, Any]]:
    """Fetch several documents at once, scoped to their owner. Returns {id: doc}.

    **Three queries and one connection, where the caller used to spend three per
    hit.** Search hydrates every vector-store hit from SQLite; doing that with
    `get_document` in a loop opened a fresh connection and issued three queries
    per result, and the answer path does it for up to twenty. Same shape as
    `get_document`, minus the ordering — the caller already knows the ranking.

    `user_id` is required, not defaulted: this is the search path's isolation
    boundary. The vector store is filtered by user, but hydration reads by id,
    so a stale or spoofed index entry must not be able to surface another
    visitor's document. Filtering in the SQL closes that here rather than asking
    every caller to remember.
    """
    ids = list(dict.fromkeys(doc_ids))  # dedup, order-preserving
    if not ids:
        return {}

    placeholders = ",".join("?" * len(ids))
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM documents WHERE user_id = ? AND id IN ({placeholders})",
            [user_id, *ids],
        ).fetchall()
        docs = {row["id"]: _row_to_dict(row) for row in rows}
        if not docs:
            return {}

        owned = ",".join("?" * len(docs))
        entities = conn.execute(
            f"SELECT document_id, entity_type, entity_value FROM entities "
            f"WHERE document_id IN ({owned})",
            list(docs),
        ).fetchall()
        tags = conn.execute(
            f"SELECT document_id, tag FROM tags WHERE document_id IN ({owned})",
            list(docs),
        ).fetchall()

    for doc in docs.values():
        doc["skills"] = []
        doc["organizations"] = []
        doc["people"] = []
        doc["tags"] = []
    _ENTITY_FIELD = {"skill": "skills", "organization": "organizations", "person": "people"}
    for row in entities:
        field = _ENTITY_FIELD.get(row["entity_type"])
        if field:
            docs[row["document_id"]][field].append(row["entity_value"])
    for row in tags:
        docs[row["document_id"]]["tags"].append(row["tag"])
    return docs


def list_documents(
    user_id: str = "demo",
    category: str | None = None,
    document_type: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List documents newest-first, optionally filtered by category and/or type.

    Given **both**, a document matching *either* is returned. That is deliberate
    and is the fix for a real miss: search maps a typed word to a category, but
    the category is the model's judgment while `document_type` is what the
    document plainly is — Gemini filed a résumé under *Skills*, so a filter on
    *Academics* alone hid it from "show my resume". A word that names a type
    must find documents of that type wherever the model filed them. Either
    argument alone still filters on exactly that column, so
    `GET /api/documents?category=` stays an exact category filter.

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
    if category and document_type:
        sql += " AND (category = ? OR document_type = ?)"
        params += [category, document_type]
    elif category:
        sql += " AND category = ?"
        params.append(category)
    elif document_type:
        sql += " AND document_type = ?"
        params.append(document_type)
    sql += " ORDER BY upload_date DESC LIMIT ?"
    params.append(limit)

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(row) for row in rows]


def documents_with_skills(user_id: str = "demo") -> list[dict[str, Any]]:
    """Every document with its skill entities attached, for the relationship graph.

    Two queries, not N+1: one for the documents, one for all their skill rows,
    joined in Python.

    **No `raw_text`.** It used to be selected because the similarity layer
    re-embedded it on every graph read; that layer now queries the vector store
    by document id (`embeddings.neighbors_of_document`), so pulling the whole
    corpus into memory per request bought nothing. Both remaining callers —
    `graph.builder` and `ai.career_path._build_profile` — use only the title,
    category and skills.
    """
    with get_connection() as conn:
        docs = conn.execute(
            "SELECT id, category, title FROM documents WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        skill_rows = conn.execute(
            """
            SELECT e.document_id AS doc_id, e.entity_value AS value
            FROM entities e
            JOIN documents d ON d.id = e.document_id
            WHERE d.user_id = ? AND e.entity_type = 'skill'
            """,
            (user_id,),
        ).fetchall()

    skills_by_doc: dict[str, list[str]] = {}
    for row in skill_rows:
        skills_by_doc.setdefault(row["doc_id"], []).append(row["value"])

    return [
        {
            "id": d["id"],
            "category": d["category"],
            "title": d["title"],
            "skills": skills_by_doc.get(d["id"], []),
        }
        for d in docs
    ]


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


def _parse_metadata(raw: str | None, doc_id: str | None = None) -> dict[str, Any]:
    """Decode a `metadata_json` blob, tolerating junk. Always a dict."""
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        logger.warning("Malformed metadata_json for document %s", doc_id)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _category_source(raw: str | None, doc_id: str | None = None) -> str:
    """Who chose this document's category — `manual` or `ai`.

    Anything other than an explicit "manual" marker reads as the model's own
    answer, so a missing or malformed blob can never claim a user override that
    did not happen.
    """
    if _parse_metadata(raw, doc_id).get("category_source") == MANUAL_CATEGORY_SOURCE:
        return MANUAL_CATEGORY_SOURCE
    return AI_CATEGORY_SOURCE


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    doc = dict(row)
    raw = doc.pop("metadata_json", None)
    doc["metadata"] = _parse_metadata(raw, doc.get("id"))
    # Derived here, in the one place every reader passes through, for the same
    # reason `effective_date` is: a consumer that read `category` without this
    # would present a user's correction as the model's judgment.
    doc["category_source"] = _category_source(raw, doc.get("id"))
    _resolve_date(doc)
    return doc
