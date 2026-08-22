"""Direct tests for the data layer's batch reads and its index set.

`db/database.py` is the largest non-route module and had no test file of its
own — it was covered only through the endpoints. `get_documents` is the one
worth testing directly: it is search's hydration path *and* the place the
user-isolation filter now lives for that path.
"""

from __future__ import annotations

import uuid

from db import database


def _insert(user: str, *, title="Doc", skills=None, tags=None, raw_text="text"):
    doc_id = uuid.uuid4().hex
    database.insert_document(
        doc_id=doc_id,
        user_id=user,
        filename=f"{title}.pdf",
        original_path="/x",
        file_type="pdf",
        checksum="c",
        raw_text=raw_text,
        upload_date="2025-01-01 00:00:00",
        category="Projects",
        title=title,
        skills=skills or [],
        tags=tags or [],
    )
    return doc_id


def test_batch_fetch_matches_the_single_fetch():
    """Same shape as get_document, so callers can swap one for the other."""
    doc_id = _insert("demo", title="Alpha", skills=["Python"], tags=["ml"])

    one = database.get_document(doc_id)
    batch = database.get_documents([doc_id], "demo")[doc_id]

    assert batch["title"] == one["title"]
    assert batch["skills"] == one["skills"] == ["Python"]
    assert batch["tags"] == one["tags"] == ["ml"]
    assert batch["effective_date"] == one["effective_date"]
    assert batch["date_source"] == one["date_source"]
    assert batch["category_source"] == one["category_source"]


def test_another_users_document_is_not_returned():
    """The isolation boundary for search hydration.

    Mutation check: drop `user_id = ?` from get_documents' WHERE clause and this
    test is the one that turns red. The vector store filters by user, but
    hydration reads by id — a stale or spoofed index entry must not be able to
    surface someone else's document.
    """
    mine = _insert("demo", title="Mine")
    theirs = _insert("intruder", title="Theirs")

    got = database.get_documents([mine, theirs], "demo")

    assert set(got) == {mine}


def test_missing_ids_are_dropped_not_faked():
    real = _insert("demo")
    got = database.get_documents([real, "does-not-exist"], "demo")
    assert set(got) == {real}


def test_entities_land_on_the_right_document():
    """Two documents in one batch must not swap their skills."""
    a = _insert("demo", title="A", skills=["Python"], tags=["one"])
    b = _insert("demo", title="B", skills=["SQL"], tags=["two"])

    got = database.get_documents([a, b], "demo")

    assert got[a]["skills"] == ["Python"] and got[a]["tags"] == ["one"]
    assert got[b]["skills"] == ["SQL"] and got[b]["tags"] == ["two"]


def test_empty_input_touches_no_connection():
    assert database.get_documents([], "demo") == {}


def test_duplicate_ids_collapse():
    doc_id = _insert("demo")
    assert list(database.get_documents([doc_id, doc_id], "demo")) == [doc_id]


def test_the_listing_index_exists():
    """Every listing filters on user_id and sorts on upload_date."""
    with database.get_connection() as conn:
        names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
    assert "idx_documents_user_date" in names


def test_a_write_scoped_to_another_user_changes_nothing():
    """The write is scoped, not just the check that precedes it.

    /recategorize and /reextract verified ownership and *then* wrote — two
    statements with a gap between them. Putting user_id in the WHERE clause
    means the row itself decides, so a document that changed hands (or was
    deleted) between the two cannot be written by the earlier check's authority.

    Mutation check: drop `AND user_id = ?` from update_categorization and this
    test turns red.
    """
    theirs = _insert("intruder", title="Theirs")

    stored = database.update_categorization(
        theirs,
        user_id="demo",
        document_type="certificate",
        category="Certifications",
        title="Hijacked",
        summary="",
        extracted_date=None,
        confidence=0.9,
    )

    assert stored is None
    assert database.get_document(theirs)["title"] == "Theirs"


def test_the_embedding_marker_is_scoped_too():
    theirs = _insert("intruder")

    database.set_embedding_id(theirs, "x", user_id="demo")

    assert database.get_document(theirs)["embedding_id"] is None


def test_init_db_upgrades_a_database_from_before_the_user_id_column(tmp_path, monkeypatch):
    """Startup must survive a database older than the newest column.

    Migrations used to run *after* the schema script, and the script creates an
    index on `career_paths(user_id)` — a column the migration had not added yet.
    On any pre-migration database `init_db` raised "no such column: user_id" and
    the app did not boot at all. It hid because the deploy target's disk is
    ephemeral: every deployed database is new, and only a long-lived developer
    one is old enough to reach it.
    """
    import sqlite3

    from config import settings

    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    # The real shape of a database from that era: today's schema, minus the one
    # column the migration adds. Built by stripping it from schema.sql rather
    # than hand-writing a subset, so this cannot drift into testing a table the
    # app never had.
    era_schema = database.SCHEMA_PATH.read_text(encoding="utf-8")
    era_schema = era_schema.replace(
        "    user_id TEXT NOT NULL DEFAULT 'demo',\n    title TEXT,", "    title TEXT,"
    )
    era_schema = era_schema.replace(
        "CREATE INDEX IF NOT EXISTS idx_career_paths_user ON career_paths(user_id);", ""
    )
    assert "career_paths(user_id)" not in era_schema
    conn.executescript(era_schema)
    conn.execute("INSERT INTO career_paths (id, title) VALUES ('cp1', 'AI/ML Engineer')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(settings, "db_path", old)
    database.init_db()

    with database.get_connection() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(career_paths)")}
        # The pre-existing row keeps the shared dataset it was inferred for.
        owner = conn.execute("SELECT user_id FROM career_paths WHERE id = 'cp1'").fetchone()

    assert "user_id" in columns
    assert owner["user_id"] == "demo"
