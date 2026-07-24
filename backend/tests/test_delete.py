"""Phase 9: deleting a document removes it from every store.

DELETE /api/documents/{id} must clean the SQLite row (and its entity/tag rows),
the vector index, and — for an uploaded file — the original plus its sidecar.
It is scoped to the user, so one user cannot delete another's document.
"""

from __future__ import annotations

from conftest import make_docx, upload, DOCX_MIME

from ai import embeddings
from db import database


def _chunk_ids(doc_id: str) -> list[str]:
    """Vector-store chunk ids currently indexed for a document."""
    return embeddings._get_collection().get(where={"doc_id": doc_id})["ids"]


def test_delete_removes_the_document_everywhere(client, stored_doc):
    doc_id, _, _ = stored_doc

    # Preconditions: the row, its file, and its vectors all exist.
    assert database.get_document(doc_id) is not None
    found = __import__("storage").find_by_id(doc_id, "demo")
    assert found is not None
    stored_path = found[0]
    assert stored_path.exists()
    assert _chunk_ids(doc_id)  # non-empty

    resp = client.delete(f"/api/documents/{doc_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"id": doc_id, "deleted": True}

    # SQLite row and its detail are gone.
    assert database.get_document(doc_id) is None
    assert client.get(f"/api/documents/{doc_id}").status_code == 404

    # Vectors are gone (so it cannot even surface as an orphan in search).
    assert _chunk_ids(doc_id) == []

    # Original file and its sidecar are gone.
    assert not stored_path.exists()
    assert __import__("storage").find_by_id(doc_id, "demo") is None


def test_delete_clears_entity_and_tag_rows(client, stored_doc):
    doc_id, _, _ = stored_doc
    client.delete(f"/api/documents/{doc_id}")

    with database.get_connection() as conn:
        entities = conn.execute(
            "SELECT COUNT(*) AS n FROM entities WHERE document_id = ?", (doc_id,)
        ).fetchone()["n"]
        tags = conn.execute(
            "SELECT COUNT(*) AS n FROM tags WHERE document_id = ?", (doc_id,)
        ).fetchone()["n"]
    assert entities == 0
    assert tags == 0


def test_delete_drops_it_from_the_listing(client, stored_doc):
    doc_id, _, _ = stored_doc
    assert any(d["id"] == doc_id for d in client.get("/api/documents").json())

    client.delete(f"/api/documents/{doc_id}")

    assert all(d["id"] != doc_id for d in client.get("/api/documents").json())


def test_delete_a_fileless_document(client):
    """A text entry has no original on disk; delete must still fully succeed."""
    doc_id = client.post("/api/ingest-text", json={"text": "Led the Data Science Club in 2024."}).json()["id"]
    assert database.get_document(doc_id) is not None

    resp = client.delete(f"/api/documents/{doc_id}")

    assert resp.status_code == 200, resp.text
    assert database.get_document(doc_id) is None
    assert _chunk_ids(doc_id) == []


def test_delete_missing_document_is_404(client):
    resp = client.delete("/api/documents/does-not-exist")
    assert resp.status_code == 404


def test_delete_is_scoped_to_the_user(client):
    """A document owned by another user cannot be deleted through this endpoint.

    Mutation check: dropping the `AND user_id = ?` filter in
    database.delete_document lets this delete succeed and turns this test red.
    """
    database.insert_document(
        doc_id="other-user-doc",
        user_id="someone-else",
        filename="secret.txt",
        original_path="",
        file_type="text_entry",
        checksum="deadbeef",
        raw_text="Not yours to delete.",
        upload_date="2026-01-01T00:00:00",
        category="Projects",
        title="Not yours",
    )

    resp = client.delete("/api/documents/other-user-doc")

    assert resp.status_code == 404
    # The other user's document survives.
    assert database.get_document("other-user-doc") is not None
