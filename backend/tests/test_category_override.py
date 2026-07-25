"""Phase 9: the manual category override.

PATCH /api/documents/{id}/category lets the user overrule Gemini's category
(plan.md § Risk Mitigation — "show confidence score, allow manual override").
The rules it has to keep:

  - only the six categories in the taxonomy are accepted;
  - it changes the category and nothing else — the original, the extracted
    text, the checksum, the entities, and the model's own confidence all stand;
  - the choice is marked `manual`, so nothing presents it as the AI's judgment;
  - a later re-categorization does not silently undo it;
  - it is scoped to the user, like delete and the graph.
"""

from __future__ import annotations

import pytest

from db import database

CATEGORY_URL = "/api/documents/{}/category"


def _patch(client, doc_id: str, category: str):
    return client.patch(CATEGORY_URL.format(doc_id), json={"category": category})


def test_override_changes_the_category(client, stored_doc):
    doc_id, _, response = stored_doc
    # The stub categorizes every upload as a Certification.
    assert response["categorization"]["category"] == "Certifications"

    resp = _patch(client, doc_id, "Projects")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "id": doc_id,
        "category": "Projects",
        "category_source": "manual",
    }
    assert database.get_document(doc_id)["category"] == "Projects"


def test_override_is_visible_to_the_listing_and_its_filter(client, stored_doc):
    """The timeline reads the listing, so the override has to land there."""
    doc_id, _, _ = stored_doc
    _patch(client, doc_id, "Achievements")

    listed = client.get("/api/documents").json()
    entry = next(d for d in listed if d["id"] == doc_id)
    assert entry["category"] == "Achievements"
    assert entry["category_source"] == "manual"

    # And the category filter follows it to its new home.
    assert [d["id"] for d in client.get("/api/documents?category=Achievements").json()] == [doc_id]
    assert client.get("/api/documents?category=Certifications").json() == []


def test_category_source_defaults_to_ai(client, stored_doc):
    """An untouched document must not read as user-chosen."""
    doc_id, _, _ = stored_doc

    assert client.get(f"/api/documents/{doc_id}").json()["category_source"] == "ai"
    listed = next(d for d in client.get("/api/documents").json() if d["id"] == doc_id)
    assert listed["category_source"] == "ai"


@pytest.mark.parametrize(
    "bad", ["Hobbies", "", "   ", "certifications; DROP TABLE documents", "Uncategorized"]
)
def test_rejects_a_category_outside_the_taxonomy(client, stored_doc, bad):
    """Mutation check: dropping the `category not in CATEGORIES` guard in
    routes/documents.set_category accepts these and turns this test red.

    "Uncategorized" is in the list on purpose — it is the *model's* fallback for
    "couldn't tell", not something a user should be able to choose.
    """
    doc_id, _, _ = stored_doc

    resp = _patch(client, doc_id, bad)

    assert resp.status_code == 400
    # The document keeps the category it had.
    assert database.get_document(doc_id)["category"] == "Certifications"


def test_override_touches_nothing_but_the_category(client, stored_doc):
    """The preserved original and everything derived from it must not move.

    An override corrects a *label*. CLAUDE.md's "originals are never modified"
    rule and the entity rows Module 3 joins on are both outside its remit.
    """
    doc_id, original, _ = stored_doc
    before = database.get_document(doc_id)
    stored_path, manifest = __import__("storage").find_by_id(doc_id, "demo")

    _patch(client, doc_id, "Internships")

    after = database.get_document(doc_id)
    assert after["category"] == "Internships"
    for field in (
        "title", "summary", "raw_text", "checksum", "original_path",
        "extracted_date", "upload_date", "confidence", "document_type",
        "skills", "organizations", "people", "tags",
    ):
        assert after[field] == before[field], f"{field} changed"

    # The original file itself is untouched, byte for byte.
    assert stored_path.read_bytes() == original
    assert __import__("storage").verify_integrity(stored_path, manifest)


def test_override_reshapes_the_graph_without_reindexing(client, stored_doc):
    """The graph types skill edges by the document's category, and computes them
    on read — so an override re-forms them with no re-embedding.

    A certificate `certifies_skill`; anything else merely `skill_used_in`.
    """
    doc_id, _, _ = stored_doc

    relations = {e["relation_type"] for e in client.get("/api/graph").json()["edges"]}
    assert "certifies_skill" in relations

    _patch(client, doc_id, "Projects")

    after = {e["relation_type"] for e in client.get("/api/graph").json()["edges"]}
    assert "certifies_skill" not in after
    assert "skill_used_in" in after


def test_recategorize_does_not_revert_a_manual_override(client, stored_doc):
    """The retry path re-runs Gemini over the same text and would classify it
    'Certifications' again. The user already said otherwise.

    Mutation check: removing the manual-source guard in
    database.update_categorization lets the re-run overwrite the category and
    turns this test red.
    """
    doc_id, _, _ = stored_doc
    _patch(client, doc_id, "Projects")

    resp = client.post(f"/api/documents/{doc_id}/recategorize")

    assert resp.status_code == 200, resp.text
    assert resp.json()["category"] == "Projects"
    stored = database.get_document(doc_id)
    assert stored["category"] == "Projects"
    assert stored["category_source"] == "manual"
    # Everything else the model produced is still refreshed by the re-run.
    assert stored["title"] == "Test Certificate"


def test_recategorize_still_sets_the_category_when_it_was_never_overridden(
    client, stored_doc
):
    """The guard must protect a user's choice, not freeze every category."""
    doc_id, _, _ = stored_doc
    with database.get_connection() as conn:
        conn.execute("UPDATE documents SET category = ? WHERE id = ?", ("Skills", doc_id))

    client.post(f"/api/documents/{doc_id}/recategorize")

    assert database.get_document(doc_id)["category"] == "Certifications"


def test_override_a_fileless_document(client):
    """A URL/text entry has no original; the override still applies."""
    doc_id = client.post(
        "/api/ingest-text", json={"text": "Led the Data Science Club in 2024."}
    ).json()["id"]

    resp = _patch(client, doc_id, "Achievements")

    assert resp.status_code == 200, resp.text
    assert database.get_document(doc_id)["category"] == "Achievements"


def test_override_missing_document_is_404(client):
    assert _patch(client, "does-not-exist", "Projects").status_code == 404


def test_override_is_scoped_to_the_user(client):
    """Mutation check: dropping the `AND user_id = ?` filter in
    database.set_category lets this succeed and turns this test red.
    """
    database.insert_document(
        doc_id="other-user-doc",
        user_id="someone-else",
        filename="secret.txt",
        original_path="",
        file_type="text_entry",
        checksum="deadbeef",
        raw_text="Not yours to relabel.",
        upload_date="2026-01-01T00:00:00",
        category="Projects",
        title="Not yours",
    )

    resp = _patch(client, "other-user-doc", "Achievements")

    assert resp.status_code == 404
    assert database.get_document("other-user-doc")["category"] == "Projects"


def test_malformed_metadata_does_not_claim_a_manual_override(client, stored_doc):
    """A junk metadata_json blob must read as 'ai', never as a user's choice."""
    doc_id, _, _ = stored_doc
    with database.get_connection() as conn:
        conn.execute(
            "UPDATE documents SET metadata_json = ? WHERE id = ?", ("{not json", doc_id)
        )

    assert database.get_document(doc_id)["category_source"] == "ai"
    # And an override still writes cleanly over the junk.
    assert _patch(client, doc_id, "Projects").status_code == 200
    assert database.get_document(doc_id)["category_source"] == "manual"
