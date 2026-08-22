"""The demo-profile seed (plan.md §14, Phase 8).

Covers the endpoint contract, idempotency, the fileless convention the demo
documents rely on, and that the hand-authored skills actually build the
graph chain the demo depends on (the Python skill hub). Uses the default
stubbed embeddings — the entity/skill edges asserted here are deterministic and
do not need the real model; similarity-edge *quality* is a `model`-marked
concern, not tested here.
"""

from __future__ import annotations

from seed.seed_demo import DOCS, load_demo


def test_seed_endpoint_loads_ten_documents(client):
    resp = client.post("/api/seed-demo")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["seeded"] == len(DOCS) == 10
    assert body["user_id"] == "demo"

    docs = client.get("/api/documents").json()
    assert len(docs) == 10


def test_seed_is_idempotent(client):
    """Re-loading replaces the demo set rather than stacking duplicates."""
    client.post("/api/seed-demo")
    client.post("/api/seed-demo")

    docs = client.get("/api/documents").json()
    assert len(docs) == 10  # not 20
    # Deterministic ids, so no accidental duplicate rows for the same document.
    ids = [d["id"] for d in docs]
    assert len(ids) == len(set(ids))


def test_reseed_preserves_a_users_own_upload(client):
    """Clearing is scoped to demo-* ids — a real upload must survive a re-seed."""
    client.post("/api/seed-demo")
    uploaded = client.post(
        "/api/upload",
        files={"file": ("mine.txt", b"my own document", "text/plain")},
    ).json()["id"]

    client.post("/api/seed-demo")  # re-seed

    ids = {d["id"] for d in client.get("/api/documents").json()}
    assert uploaded in ids  # not wiped by the demo clear
    assert len([i for i in ids if i.startswith("demo-")]) == 10


def test_demo_documents_are_fileless(client):
    """Every seeded document is url/text_entry with no original to download."""
    client.post("/api/seed-demo")
    for doc in client.get("/api/documents").json():
        if doc["id"].startswith("demo-"):
            assert doc["has_original"] is False
            assert doc["file_type"] in {"url", "text_entry"}


def test_python_skill_hub_forms_the_chain(client):
    """The money-click depends on a Python skill node wired to the journey.

    Entity edges are deterministic (no embeddings), so this is a stable check
    that the authored skills build the cert -> project -> internship chain
    plan.md §3 names.
    """
    client.post("/api/seed-demo")
    graph = client.get("/api/graph").json()

    skill_ids = {n["id"] for n in graph["nodes"] if n["type"] == "skill"}
    assert "skill:python" in skill_ids

    python_edges = [
        e for e in graph["edges"]
        if e["source"] == "skill:python" or e["target"] == "skill:python"
    ]
    # Python threads through most of the journey — cert, project, internship,
    # resume, portfolio, and more.
    assert len(python_edges) >= 6
    # The certificate certifies the skill; other documents use it.
    relations = {e["relation_type"] for e in python_edges}
    assert "certifies_skill" in relations
    assert "skill_used_in" in relations


def test_load_demo_callable_directly(client):
    """The CLI/importable entrypoint works without the HTTP layer."""
    result = load_demo("demo")
    assert result == {"seeded": 10, "user_id": "demo"}


# --- Two visitors, one demo profile ----------------------------------------


def _seed_as(client, user_id):
    return client.post("/api/seed-demo", headers={"X-User-Id": user_id})


ALICE = "aaaaaaaabbbbccccddddeeeeffff0000"
BOB = "11112222333344445555666677778888"


def test_a_second_visitor_can_also_load_the_demo(client):
    """The bug this file did not catch for a whole phase.

    `documents.id` is a global primary key and the seed's ids were fixed
    constants, so the second visitor's insert collided on `demo-python-cert`
    and the endpoint 500'd — `_clear_demo` deletes only the *caller's* rows, so
    nothing ever cleared the row in the way. On the deployed site that is every
    visitor after the first; the ephemeral disk hid it by resetting the count on
    each deploy. Every test here seeded exactly one user, which is why it lived.
    """
    assert _seed_as(client, ALICE).status_code == 200
    second = _seed_as(client, BOB)

    assert second.status_code == 200, second.text
    assert second.json()["seeded"] == len(DOCS)


def test_each_visitor_sees_only_their_own_copy(client):
    _seed_as(client, ALICE)
    _seed_as(client, BOB)

    alice_docs = client.get("/api/documents", headers={"X-User-Id": ALICE}).json()
    bob_docs = client.get("/api/documents", headers={"X-User-Id": BOB}).json()

    assert len(alice_docs) == len(bob_docs) == len(DOCS)
    # Same documents, different rows — no id is shared between the two datasets.
    assert {d["id"] for d in alice_docs}.isdisjoint({d["id"] for d in bob_docs})
    assert {d["title"] for d in alice_docs} == {d["title"] for d in bob_docs}


def test_reseeding_still_replaces_rather_than_duplicates(client):
    """The scoped id must stay *derived*, not random, or a re-seed stacks up."""
    _seed_as(client, ALICE)
    first = {d["id"] for d in client.get("/api/documents", headers={"X-User-Id": ALICE}).json()}

    _seed_as(client, ALICE)
    again = client.get("/api/documents", headers={"X-User-Id": ALICE}).json()

    assert len(again) == len(DOCS)
    assert {d["id"] for d in again} == first


def test_one_visitors_graph_holds_only_their_own_documents(client):
    """The seeded ids feed the graph, so a collision there would cross datasets."""
    _seed_as(client, ALICE)
    _seed_as(client, BOB)

    alice_nodes = {
        n["id"]
        for n in client.get("/api/graph", headers={"X-User-Id": ALICE}).json()["nodes"]
        if n["type"] == "document"
    }
    bob_nodes = {
        n["id"]
        for n in client.get("/api/graph", headers={"X-User-Id": BOB}).json()["nodes"]
        if n["type"] == "document"
    }

    assert alice_nodes and bob_nodes
    assert alice_nodes.isdisjoint(bob_nodes)
