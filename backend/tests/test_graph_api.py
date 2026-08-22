"""Knowledge graph endpoint + builder (plan.md §4 Module 3, §6 View 3)."""

from __future__ import annotations

import uuid

from db import database
from graph import builder


def _seed(user, category, skills, *, title="Doc", raw_text="some text", original_path="/x"):
    doc_id = uuid.uuid4().hex
    database.insert_document(
        doc_id=doc_id,
        user_id=user,
        filename=title,
        original_path=original_path,
        file_type="pdf",
        checksum="c",
        raw_text=raw_text,
        upload_date="2025-01-01 00:00:00",
        category=category,
        title=title,
        skills=skills,
    )
    return doc_id


def test_shared_skill_produces_one_hub_with_typed_edges(client):
    """A cert and a project on Python collapse to one skill node they both link."""
    cert = _seed("demo", "Certifications", ["Python"], title="Python Cert")
    proj = _seed("demo", "Projects", ["Python"], title="ML Project")

    data = client.get("/api/graph").json()
    nodes = {n["id"]: n for n in data["nodes"]}

    assert "skill:python" in nodes
    assert nodes["skill:python"]["type"] == "skill"
    assert cert in nodes and proj in nodes

    typed = {(e["source"], e["relation_type"]) for e in data["edges"] if e["target"] == "skill:python"}
    assert (cert, "certifies_skill") in typed
    assert (proj, "skill_used_in") in typed


def test_document_node_carries_has_original(client):
    """The panel branches download-vs-open on the same flag the other views use."""
    filed = _seed("demo", "Projects", [], title="Has file", original_path="/uploads/x.pdf")
    fileless = _seed("demo", "Projects", [], title="URL doc", original_path="")

    nodes = {n["id"]: n for n in client.get("/api/graph").json()["nodes"]}
    assert nodes[filed]["has_original"] is True
    assert nodes[fileless]["has_original"] is False


def test_graph_excludes_other_users_documents(client):
    """Isolation: another user's document must never enter the graph.

    Mutation-tested: dropping the user_id filter on `list_documents` in
    `builder.build_graph` makes the foreign node appear and turns this red.
    """
    mine = _seed("demo", "Projects", ["Python"], title="Mine")
    theirs = _seed("intruder", "Projects", ["Python"], title="Theirs")

    ids = {n["id"] for n in client.get("/api/graph").json()["nodes"]}
    assert mine in ids
    assert theirs not in ids


def test_similarity_edges_are_wired(client):
    """With neighbours above threshold, documents get a similar_to edge.

    Uses an injected neighbor_fn so neither the embedding model nor the vector
    store is needed; this covers the wiring from builder through
    relationship_engine.similarity_edges.
    """
    a = _seed("demo", "Projects", [], title="A", raw_text="alpha")
    b = _seed("demo", "Projects", [], title="B", raw_text="beta")

    def fake_neighbors(doc_id, *, user_id, k):
        # Every document is a strong neighbour of every other (self dropped
        # downstream); enough to force one undirected similar_to edge.
        return [{"doc_id": a, "score": 0.9}, {"doc_id": b, "score": 0.9}]

    data = builder.build_graph("demo", neighbor_fn=fake_neighbors)
    sim = [e for e in data["edges"] if e["relation_type"] == "similar_to"]

    assert len(sim) == 1
    assert {sim[0]["source"], sim[0]["target"]} == {a, b}


def test_graph_read_embeds_nothing(client, monkeypatch):
    """The graph must cost zero model inferences once documents are indexed.

    This is the regression guard for the fix that replaced
    `embeddings.query(raw_text)`-per-document with a lookup of the vectors the
    store already holds. Before it, one graph read was N forward passes — on an
    instance measured at ~1/40th this machine's CPU, which is why
    `ai/precomputed.py` had to ship vectors for the demo profile's whole
    documents just to make the page usable.

    Counting `embed_texts` is the honest assertion: it is the single choke point
    every embedding flows through, so zero calls means no inference happened,
    table hit or not.
    """
    from ai import embeddings

    a = _seed("demo", "Projects", [], title="A", raw_text="alpha text about python")
    b = _seed("demo", "Projects", [], title="B", raw_text="beta text about python")
    for doc_id, text in ((a, "alpha text about python"), (b, "beta text about python")):
        embeddings.add_document(doc_id=doc_id, user_id="demo", title="T", raw_text=text)

    calls: list[list[str]] = []
    real = embeddings.embed_texts
    monkeypatch.setattr(
        embeddings, "embed_texts", lambda texts: calls.append(texts) or real(texts)
    )

    assert client.get("/api/graph").status_code == 200
    assert calls == []


def test_neighbors_come_from_stored_vectors(client):
    """The neighbour lookup finds the other document, and never itself."""
    from ai import embeddings

    a = _seed("demo", "Projects", [], title="A", raw_text="python machine learning")
    b = _seed("demo", "Projects", [], title="B", raw_text="python machine learning")
    for doc_id in (a, b):
        embeddings.add_document(
            doc_id=doc_id, user_id="demo", title="T", raw_text="python machine learning"
        )

    hits = embeddings.neighbors_of_document(a, user_id="demo", k=5)

    assert [h["doc_id"] for h in hits] == [b]


def test_neighbors_of_an_unindexed_document_are_empty():
    """No chunks in the store means no edges, not an error."""
    from ai import embeddings

    assert embeddings.neighbors_of_document("nope", user_id="demo", k=5) == []
