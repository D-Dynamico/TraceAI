"""Phase 2: categorization is persisted and readable back (with a stubbed LLM)."""

from __future__ import annotations

import sqlite3

from conftest import DOCX_MIME, make_docx, upload

from config import settings
from db import database


def test_upload_returns_categorization(client, stub_result):
    body = upload(client, "cert.docx", make_docx(), DOCX_MIME).json()

    cat = body["categorization"]
    assert cat["category"] == stub_result.category
    assert cat["document_type"] == stub_result.document_type
    assert cat["skills"] == stub_result.skills


def test_document_row_is_written(client, stored_doc, stub_result):
    doc_id, _, meta = stored_doc

    row = database.get_document(doc_id)

    assert row is not None
    assert row["category"] == stub_result.category
    assert row["checksum"] == meta["checksum"]
    assert row["confidence"] == stub_result.confidence
    assert row["user_id"] == "demo"
    assert "Internship" in row["raw_text"]


def test_entities_and_tags_are_written_as_rows(client, stored_doc, stub_result):
    """Module 3 joins documents on shared entity values, so these need rows."""
    doc_id, _, _ = stored_doc

    row = database.get_document(doc_id)

    assert sorted(row["skills"]) == sorted(stub_result.skills)
    assert row["organizations"] == stub_result.organizations
    assert row["people"] == stub_result.people
    assert row["tags"] == stub_result.tags


def test_missing_date_is_stored_as_null_not_upload_date(client, monkeypatch):
    """Keeps a known date distinguishable from an assumed one (plan.md 10)."""
    from models.document import Categorization
    import routes.upload as upload_route

    undated = Categorization(title="No date", summary="x", category="Projects", date=None)
    monkeypatch.setattr(upload_route.categorizer, "categorize", lambda t, f="": undated)

    doc_id = upload(client, "notes.txt", b"Some text with no date in it.").json()["id"]

    assert database.get_document(doc_id)["extracted_date"] is None


def test_low_confidence_is_flagged_for_review(client, monkeypatch):
    from models.document import Categorization
    import routes.upload as upload_route

    unsure = Categorization(title="?", summary="x", confidence=0.0)
    monkeypatch.setattr(upload_route.categorizer, "categorize", lambda t, f="": unsure)

    body = upload(client, "notes.txt", b"Ambiguous document text.").json()

    assert any("review" in w.lower() for w in body["warnings"])


def test_list_and_detail_endpoints(client, stored_doc, stub_result):
    doc_id, _, _ = stored_doc

    listing = client.get("/api/documents").json()
    assert [d["id"] for d in listing] == [doc_id]
    assert "raw_text" not in listing[0], "listings must not carry full text"

    detail = client.get(f"/api/documents/{doc_id}").json()
    assert detail["category"] == stub_result.category
    assert sorted(detail["skills"]) == sorted(stub_result.skills)
    assert "Internship" in detail["raw_text"]
    assert detail["metadata"]["method"] == "native"


def test_has_original_distinguishes_files_from_fileless(client, stored_doc):
    """The Phase 6 timeline/search branch download-vs-open on this flag.

    A file upload has an original to download; a text entry does not
    (original_path == ""). Mutation-tested: hard-coding has_original=True in the
    list route turns the text-entry assertion red.
    """
    file_id, _, _ = stored_doc
    text_id = client.post(
        "/api/ingest-text",
        json={"text": "Led the Data Science Club in 2024, ran five workshops."},
    ).json()["id"]

    by_id = {d["id"]: d for d in client.get("/api/documents").json()}

    assert by_id[file_id]["has_original"] is True
    assert by_id[text_id]["has_original"] is False


def test_category_filter(client, stored_doc):
    assert len(client.get("/api/documents", params={"category": "Certifications"}).json()) == 1
    assert client.get("/api/documents", params={"category": "Projects"}).json() == []


def test_detail_404_for_unknown_id(client):
    assert client.get("/api/documents/" + "0" * 32).status_code == 404


def test_failed_index_still_preserves_the_original(client, monkeypatch):
    """A DB failure must not cost the file — preservation outranks indexing."""
    import routes.upload as upload_route

    def _boom(**kwargs):
        raise RuntimeError("db is down")

    monkeypatch.setattr(upload_route.database, "insert_document", _boom)

    original = make_docx()
    resp = upload(client, "cert.docx", original, DOCX_MIME)

    assert resp.status_code == 500
    stored = list((settings.upload_dir / "demo").glob("*.docx"))
    assert len(stored) == 1
    assert stored[0].read_bytes() == original


def test_entities_are_removed_with_their_document(client, stored_doc):
    """ON DELETE CASCADE must actually be enforced (needs PRAGMA foreign_keys)."""
    doc_id, _, _ = stored_doc

    with database.get_connection() as conn:
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    with database.get_connection() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM entities WHERE document_id = ?", (doc_id,)
        ).fetchone()[0]

    assert remaining == 0


def test_malformed_metadata_json_does_not_crash_reads(client, stored_doc):
    doc_id, _, _ = stored_doc

    with database.get_connection() as conn:
        conn.execute("UPDATE documents SET metadata_json = ? WHERE id = ?",
                     ("{not valid json", doc_id))

    assert database.get_document(doc_id)["metadata"] == {}
