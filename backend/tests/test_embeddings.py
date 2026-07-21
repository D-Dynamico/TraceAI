"""Vector store mechanics: chunking, add/query/delete, sync, and isolation.

Ranking *quality* is not tested here — the stubbed embeddings return hash
vectors, so cosine order is arbitrary but deterministic. These tests pin the
mechanics that must hold regardless of the model: distinct-document dedup, the
user_id isolation boundary, idempotent replace, and rebuild-from-SQLite. The one
test that needs real semantics carries the `model` marker.
"""

from __future__ import annotations

import math

import pytest

from ai import embeddings
from tests.conftest import upload


# --- Chunking -------------------------------------------------------------


def test_chunk_short_text_is_single_window():
    chunks = embeddings.chunk_text("A short achievement.", title="")
    assert len(chunks) == 1
    assert chunks[0] == "A short achievement."


def test_chunk_prepends_title_to_every_window():
    text = "word " * 800  # ~4000 chars -> several windows
    chunks = embeddings.chunk_text(text, title="My Resume")
    assert len(chunks) > 1
    assert all(c.startswith("My Resume\n\n") for c in chunks)


def test_chunk_windows_overlap():
    # Distinct 3-char tokens make the overlap visible.
    text = " ".join(f"tok{i:04d}" for i in range(400))  # ~3200 chars
    chunks = embeddings.chunk_text(text, title="")
    assert len(chunks) >= 2
    # The tail of one window should reappear at the head of the next.
    first_tokens = chunks[0].split()
    second_tokens = chunks[1].split()
    assert set(first_tokens[-3:]) & set(second_tokens)


def test_chunk_empty_text_returns_no_windows():
    assert embeddings.chunk_text("   ", title="Title") == []
    assert embeddings.chunk_text("", title="") == []


def test_chunk_caps_at_max_chunks():
    huge = "x " * 200_000  # far past MAX_CHUNKS worth of windows
    chunks = embeddings.chunk_text(huge, title="")
    assert len(chunks) == embeddings.MAX_CHUNKS


# --- Add / query / delete -------------------------------------------------


def test_add_and_query_roundtrip():
    embeddings.add_document(
        doc_id="d1", user_id="demo", title="Python Cert", raw_text="Learned Python."
    )
    hits = embeddings.query("Python", user_id="demo", k=5)
    assert [h["doc_id"] for h in hits] == ["d1"]
    assert hits[0]["score"] is not None


def test_query_dedups_multi_chunk_document_to_one_result():
    long_text = "sentence about data. " * 400  # multiple chunks
    n = embeddings.add_document(
        doc_id="multi", user_id="demo", title="Report", raw_text=long_text
    )
    assert n > 1  # really did chunk
    hits = embeddings.query("data", user_id="demo", k=5)
    assert [h["doc_id"] for h in hits].count("multi") == 1


def test_add_is_idempotent_replace_not_duplicate():
    embeddings.add_document(
        doc_id="dup", user_id="demo", title="V1", raw_text="first version"
    )
    embeddings.add_document(
        doc_id="dup", user_id="demo", title="V2", raw_text="second version entirely"
    )
    hits = embeddings.query("version", user_id="demo", k=5)
    assert [h["doc_id"] for h in hits] == ["dup"]  # exactly once, not twice


def test_delete_removes_document():
    embeddings.add_document(
        doc_id="gone", user_id="demo", title="T", raw_text="to be deleted"
    )
    embeddings.delete_document("gone")
    hits = embeddings.query("deleted", user_id="demo", k=5)
    assert hits == []


def test_empty_query_returns_no_results():
    embeddings.add_document(
        doc_id="d", user_id="demo", title="T", raw_text="some content here"
    )
    assert embeddings.query("   ", user_id="demo", k=5) == []


# --- Isolation boundary (mutation-tested) ---------------------------------


def test_query_is_filtered_by_user_id():
    """A query must never return another user's documents.

    The isolation boundary. Mutation check: drop the `where={"user_id": ...}`
    filter in embeddings.query and this test must fail — the `other` document
    then leaks into `demo`'s results. Verified by mutation on 2026-07-21.
    """
    embeddings.add_document(
        doc_id="mine", user_id="demo", title="Mine", raw_text="my private document"
    )
    embeddings.add_document(
        doc_id="theirs", user_id="other", title="Theirs", raw_text="their private document"
    )

    hits = embeddings.query("document", user_id="demo", k=10)
    returned = {h["doc_id"] for h in hits}
    assert returned == {"mine"}
    assert "theirs" not in returned


# --- Rebuild from SQLite (source of truth) --------------------------------


def test_reindex_rebuilds_from_sqlite(client):
    """A wiped store is fully regenerable from SQLite."""
    assert upload(client, "a.txt", b"Alpha content about python").status_code == 200
    assert upload(client, "b.txt", b"Beta content about cooking").status_code == 200

    embeddings._reset_collection()  # simulate a lost/emptied store
    assert embeddings.query("python", user_id="demo", k=5) == []

    count = embeddings.reindex()
    assert count == 2
    assert len(embeddings.query("python", user_id="demo", k=5)) >= 1


def test_ensure_synced_indexes_missing_documents(client):
    """A partial index (store emptied under a populated DB) heals on sync."""
    assert upload(client, "a.txt", b"Content one about databases").status_code == 200

    embeddings._reset_collection()
    assert embeddings.query("databases", user_id="demo", k=5) == []

    embeddings.ensure_synced()
    assert len(embeddings.query("databases", user_id="demo", k=5)) == 1


def test_ensure_synced_rebuilds_when_store_unreadable(client, monkeypatch):
    """A corrupt store (raises on read) is wiped and rebuilt from SQLite."""
    assert upload(client, "a.txt", b"Recoverable content about graphs").status_code == 200

    def _boom(_collection):
        raise RuntimeError("simulated corrupt store")

    monkeypatch.setattr(embeddings, "_indexed_doc_ids", _boom)
    embeddings.ensure_synced()  # must not raise; falls into wipe + reindex

    # Note: do NOT monkeypatch.undo() here — the embed stub shares this
    # function-scoped monkeypatch, and undoing it would load the real model.
    # query() never calls _indexed_doc_ids, so the _boom patch is harmless below.
    assert len(embeddings.query("graphs", user_id="demo", k=5)) == 1


# --- Real model (opt-in) --------------------------------------------------


@pytest.mark.model
def test_real_model_produces_normalized_384d_vectors():
    vectors = embeddings.embed_texts(["a sentence to embed"])
    assert len(vectors) == 1
    assert len(vectors[0]) == 384  # all-MiniLM-L6-v2 dimension
    assert math.isclose(math.sqrt(sum(x * x for x in vectors[0])), 1.0, abs_tol=1e-3)


@pytest.mark.model
def test_real_model_ranks_relevant_document_first():
    embeddings.add_document(
        doc_id="ml",
        user_id="demo",
        title="ML Project",
        raw_text="Built a neural network for image classification using PyTorch.",
    )
    embeddings.add_document(
        doc_id="cook",
        user_id="demo",
        title="Recipe",
        raw_text="A traditional Italian pasta recipe with tomatoes and basil.",
    )
    hits = embeddings.query("deep learning and data science", user_id="demo", k=2)
    assert hits[0]["doc_id"] == "ml"
