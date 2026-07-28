"""Per-visitor isolation — the deployed app is one URL with many visitors.

**The bug this closes.** Every route pinned `DEFAULT_USER = "demo"`, so one
dataset was shared by everyone: the first visitor's "Load Demo Profile" click
populated the app for every visitor after them, and anything a visitor uploaded
was readable, downloadable, and deletable by the next one.

**Why the rest of the suite cannot cover this.** Every other test sends no
header, so every other test runs as the fallback user and would pass just as
happily with the isolation removed. Isolation is only observable when two
distinct ids are used against the same store — which is all this file does.

The storage layer was always user-scoped and its scoping was already
mutation-tested; what was missing was any *identity* to scope by. These tests
therefore aim at the seam that was actually absent: the route layer.
"""

from __future__ import annotations

import pytest

from conftest import DOCX_MIME, make_docx
from db import database
from identity import DEFAULT_USER, is_valid_user_id, resolve_user

ALICE = "11111111-1111-4111-8111-111111111111"
BOB = "22222222-2222-4222-8222-222222222222"


def _headers(user_id: str) -> dict[str, str]:
    return {"X-User-Id": user_id}


def _upload_as(client, user_id: str, name: str = "cert.docx"):
    resp = client.post(
        "/api/upload",
        files={"file": (name, make_docx(), DOCX_MIME)},
        headers=_headers(user_id),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


# --- The id itself ----------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "../../etc/passwd",          # path traversal — the reason this is validated
        "..",
        "a/b",
        "a\\b",
        "demo user",                 # space
        "%2e%2e",                    # percent-encoded traversal
        "ALLCAPS-1111-4111-8111-111",  # uppercase is outside the allowlist
        "short",
        "",
        "x" * 100,
    ],
)
def test_a_malformed_id_never_becomes_a_user(value):
    """`user_id` is interpolated into a filesystem path (storage.user_dir), so a
    header straight off the internet must not reach it unvalidated."""
    assert not is_valid_user_id(value)
    assert resolve_user(value) == DEFAULT_USER


def test_a_uuid_is_accepted_with_or_without_dashes():
    assert resolve_user(ALICE) == ALICE
    assert resolve_user(ALICE.replace("-", "")) == ALICE.replace("-", "")


def test_no_header_keeps_the_shared_dataset(client):
    """Backward compatibility: curl, the docs page, and the whole existing test
    suite send no header and must keep working."""
    assert client.get("/api/documents").status_code == 200


def test_a_traversing_id_cannot_escape_the_uploads_directory(client, tmp_path):
    """The end-to-end version of the validation test: a traversal id must land
    in the shared dataset, not write outside uploads/."""
    from config import settings

    doc_id = _upload_as(client, "../../../evil")

    row = database.get_document(doc_id)
    assert row["user_id"] == DEFAULT_USER
    written = list(settings.upload_dir.rglob("*.docx"))
    assert written, "the upload should still have been stored somewhere"
    for path in written:
        assert settings.upload_dir in path.parents or path.parent == settings.upload_dir / DEFAULT_USER


# --- Documents are not shared ----------------------------------------------


def test_one_visitors_documents_are_invisible_to_another(client):
    _upload_as(client, ALICE)

    assert len(client.get("/api/documents", headers=_headers(ALICE)).json()) == 1
    assert client.get("/api/documents", headers=_headers(BOB)).json() == []
    assert client.get("/api/documents").json() == [], "nor to the shared dataset"


def test_seeding_the_demo_does_not_populate_it_for_everyone(client):
    """The reported symptom: one visitor loads the demo profile and the next
    visitor arrives to a pre-filled app instead of the empty state."""
    seeded = client.post("/api/seed-demo", headers=_headers(ALICE)).json()
    assert seeded["seeded"] > 0

    assert len(client.get("/api/documents", headers=_headers(ALICE)).json()) > 0
    assert client.get("/api/documents", headers=_headers(BOB)).json() == []


def test_a_document_cannot_be_read_by_id_across_users(client):
    """Ids are unguessable uuid4s, which was sufficient with one dataset. With
    separation it is not — a shared or leaked id must not read across."""
    doc_id = _upload_as(client, ALICE)

    assert client.get(f"/api/documents/{doc_id}", headers=_headers(ALICE)).status_code == 200
    resp = client.get(f"/api/documents/{doc_id}", headers=_headers(BOB))
    assert resp.status_code == 404, "404, not 403 — never confirm the id exists"


def test_a_document_cannot_be_downloaded_across_users(client):
    doc_id = _upload_as(client, ALICE)

    assert client.get(
        f"/api/documents/{doc_id}/download", headers=_headers(ALICE)
    ).status_code == 200
    assert client.get(
        f"/api/documents/{doc_id}/download", headers=_headers(BOB)
    ).status_code == 404


def test_the_download_query_fallback_works_for_a_plain_link(client):
    """A browser navigation carries no custom header, so the href passes ?u=."""
    doc_id = _upload_as(client, ALICE)

    assert client.get(f"/api/documents/{doc_id}/download?u={ALICE}").status_code == 200
    assert client.get(f"/api/documents/{doc_id}/download?u={BOB}").status_code == 404
    assert client.get(f"/api/documents/{doc_id}/download").status_code == 404


def test_a_header_beats_a_query_param(client):
    """Ordinary XHR traffic must not be redirectable by a stray query param."""
    doc_id = _upload_as(client, ALICE)

    resp = client.get(
        f"/api/documents/{doc_id}/download?u={BOB}", headers=_headers(ALICE)
    )
    assert resp.status_code == 200


def test_a_document_cannot_be_deleted_across_users(client):
    """The destructive one: a stranger must not be able to remove your upload."""
    doc_id = _upload_as(client, ALICE)

    assert client.delete(f"/api/documents/{doc_id}", headers=_headers(BOB)).status_code == 404
    assert database.get_document(doc_id) is not None, "Bob's 404 must not have deleted it"
    assert client.delete(f"/api/documents/{doc_id}", headers=_headers(ALICE)).status_code == 200


def test_a_category_cannot_be_overridden_across_users(client):
    doc_id = _upload_as(client, ALICE)

    resp = client.patch(
        f"/api/documents/{doc_id}/category",
        json={"category": "Projects"},
        headers=_headers(BOB),
    )
    assert resp.status_code == 404
    assert database.get_document(doc_id)["category"] != "Projects"


# --- Derived views are not shared either -----------------------------------


def test_search_does_not_return_another_users_documents(client):
    _upload_as(client, ALICE)

    hits = client.post(
        "/api/search", json={"query": "show all my certificates"}, headers=_headers(BOB)
    ).json()
    assert hits["results"] == []


def test_the_graph_is_scoped_to_the_caller(client):
    _upload_as(client, ALICE)

    assert client.get("/api/graph", headers=_headers(ALICE)).json()["nodes"]
    assert client.get("/api/graph", headers=_headers(BOB)).json()["nodes"] == []


def test_rag_will_not_summarize_another_users_document(client):
    """/answer takes doc_ids straight from the client, which would otherwise be
    the easiest read-across in the app: post someone's ids and let Gemini
    summarize the contents back."""
    doc_id = _upload_as(client, ALICE)

    resp = client.post(
        "/api/answer",
        json={"query": "what is this?", "doc_ids": [doc_id]},
        headers=_headers(BOB),
    ).json()
    assert resp["cited_doc_ids"] == []


def test_career_paths_are_scoped_to_the_caller(client):
    """career_paths had no user_id column at all — one visitor's inferred paths
    appeared in every other visitor's graph."""
    database.replace_career_paths(
        [{"id": "p_alice", "title": "Alice Path", "match_score": 0.9,
          "evidence_doc_ids": [], "skill_gaps": []}],
        ALICE,
    )
    database.replace_career_paths(
        [{"id": "p_bob", "title": "Bob Path", "match_score": 0.8,
          "evidence_doc_ids": [], "skill_gaps": []}],
        BOB,
    )

    assert [p["title"] for p in database.list_career_paths(ALICE)] == ["Alice Path"]
    assert [p["title"] for p in database.list_career_paths(BOB)] == ["Bob Path"]


def test_inference_does_not_wipe_another_users_paths(client):
    """The DELETE in replace_career_paths is scoped — unscoped, one visitor
    re-inferring would erase everyone else's."""
    database.replace_career_paths(
        [{"id": "keep", "title": "Kept", "match_score": 0.8,
          "evidence_doc_ids": [], "skill_gaps": []}],
        ALICE,
    )
    database.replace_career_paths(
        [{"id": "fresh", "title": "New", "match_score": 0.7,
          "evidence_doc_ids": [], "skill_gaps": []}],
        BOB,
    )

    assert [p["title"] for p in database.list_career_paths(ALICE)] == ["Kept"]


def test_the_retry_routes_are_scoped(client):
    doc_id = _upload_as(client, ALICE)

    assert client.post(
        f"/api/documents/{doc_id}/recategorize", headers=_headers(BOB)
    ).status_code == 404
    assert client.post(
        f"/api/documents/{doc_id}/reextract", headers=_headers(BOB)
    ).status_code == 404
