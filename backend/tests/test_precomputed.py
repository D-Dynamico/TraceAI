"""The shipped embedding table (`ai/precomputed.py`).

Two things need guarding, and they pull in opposite directions:

* **It must actually cover the demo profile**, or "Load Demo Profile" goes back
  to ~90s on the free instance — that is the whole point of the file existing.
* **It must never be able to serve a vector for text it was not generated
  from.** Committed derived data is only safe because a drifted entry misses its
  hash and falls through to the real model.

`embed_texts` is stubbed globally by conftest, so these tests hold the real
function captured at import time — before any fixture can replace the module
attribute. It reaches `_get_model` and `precomputed` through module globals, so
monkeypatching those still works normally.
"""

from __future__ import annotations

import json

import pytest
from ai import embeddings, precomputed
from ai.embeddings import embed_texts as real_embed_texts
from seed.precompute_vectors import demo_texts

DIM = 384


@pytest.fixture
def no_model(monkeypatch):
    """Make loading the ONNX model a hard failure.

    Every test using this asserts a *negative* — that the model was not needed.
    Blowing up is the only way to state that: a slow-but-passing test would not
    distinguish "served from the table" from "quietly embedded live", which is
    exactly the regression this file exists to catch.
    """
    def _boom():
        raise AssertionError("The embedding model was loaded when it should not have been.")

    monkeypatch.setattr(embeddings, "_get_model", _boom)


@pytest.fixture
def fake_model(monkeypatch):
    """A stand-in model that records what it was asked to embed."""
    calls: list[list[str]] = []

    def _model(texts):
        calls.append(list(texts))
        return [_FakeVector([0.5] * DIM) for _ in texts]

    monkeypatch.setattr(embeddings, "_get_model", lambda: _model)
    return calls


class _FakeVector(list):
    """Mimics the ndarray `embed_texts` gets back, which it calls `.tolist()` on."""

    def tolist(self):
        return list(self)


# --- Coverage: the table has to match what the demo actually asks for --------


def test_table_covers_every_demo_text():
    """The drift guard. Edit DOCS or the chunking constants without rerunning
    `seed/precompute_vectors.py` and this fails — loudly, offline, and before the
    slowdown reaches the deployed instance."""
    table = precomputed.vectors_for(embeddings.MODEL_NAME)
    missing = [t for t in demo_texts() if precomputed.key(t) not in table]
    assert not missing, (
        f"{len(missing)} demo text(s) have no shipped vector. "
        "Run: PYTHONPATH=backend python -m seed.precompute_vectors"
    )


def test_table_covers_the_indexing_call_site():
    """Every `chunk_text` window the seed writes has a shipped vector."""
    from seed.seed_demo import DOCS

    table = precomputed.vectors_for(embeddings.MODEL_NAME)
    for doc in DOCS:
        for chunk in embeddings.chunk_text(doc["raw_text"], doc["title"]):
            assert precomputed.key(chunk) in table, f"no chunk vector for {doc['id']}"


def test_table_carries_no_whole_document_vectors():
    """The graph stopped embedding whole documents, so the table stopped shipping them.

    `build_graph` used to call `embeddings.query(raw_text)` per document on every
    graph read, and the table had to cover those strings or the graph paid ~37s.
    It now looks neighbours up by id against the vectors already in the store
    (`embeddings.neighbors_of_document`) and embeds nothing at all. Keeping the
    entries would not be *wrong* — a table only ever makes things faster — but it
    would be 10 dead vectors, and this asserts the two facts stay in step.
    """
    from seed.seed_demo import DOCS

    table = precomputed.vectors_for(embeddings.MODEL_NAME)
    for doc in DOCS:
        assert precomputed.key(doc["raw_text"].strip()) not in table


def test_shipped_vectors_are_normalized_and_the_right_shape():
    table = precomputed.vectors_for(embeddings.MODEL_NAME)
    assert table
    for vector in table.values():
        assert len(vector) == DIM
        # Cosine distance in Chroma assumes unit vectors (module docstring).
        assert abs(sum(x * x for x in vector) - 1.0) < 1e-4


# --- Safety: a stale or absent table degrades to slow, never to wrong --------


def test_edited_text_misses_the_table_and_reaches_the_model(fake_model):
    """The property that makes committing derived vectors safe at all."""
    original = demo_texts()[0]
    edited = original + " (edited since the table was generated)"

    result = real_embed_texts([edited])

    assert fake_model == [[edited]]  # went to the model, not the table
    assert result == [[0.5] * DIM]


def test_wrong_model_discards_the_table(monkeypatch):
    monkeypatch.setattr(precomputed, "_table", None)
    monkeypatch.setattr(precomputed, "_table_model", None)
    assert precomputed.vectors_for("some-other-model") == {}


def test_missing_file_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(precomputed, "_table", None)
    monkeypatch.setattr(precomputed, "_table_model", None)
    monkeypatch.setattr(precomputed, "VECTORS_PATH", tmp_path / "gone.json")
    assert precomputed.vectors_for(embeddings.MODEL_NAME) == {}


def test_corrupt_file_is_not_an_error(monkeypatch, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json at all", encoding="utf-8")
    monkeypatch.setattr(precomputed, "_table", None)
    monkeypatch.setattr(precomputed, "_table_model", None)
    monkeypatch.setattr(precomputed, "VECTORS_PATH", bad)
    assert precomputed.vectors_for(embeddings.MODEL_NAME) == {}


def test_wrong_dimension_entry_is_dropped(monkeypatch, tmp_path):
    """A truncated vector would poison retrieval silently, so it is discarded."""
    path = tmp_path / "short.json"
    path.write_text(
        json.dumps(
            {
                "model": embeddings.MODEL_NAME,
                "dim": DIM,
                "encoding": precomputed.ENCODING,
                "vectors": {precomputed.key("hello"): precomputed.encode([0.1, 0.2])},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(precomputed, "_table", None)
    monkeypatch.setattr(precomputed, "_table_model", None)
    monkeypatch.setattr(precomputed, "VECTORS_PATH", path)
    assert precomputed.vectors_for(embeddings.MODEL_NAME) == {}


def test_encode_decode_round_trips_exactly():
    vector = precomputed.vectors_for(embeddings.MODEL_NAME)[
        precomputed.key(demo_texts()[0])
    ]
    assert precomputed._decode(precomputed.encode(vector)) == vector


# --- embed_texts: hits skip the model, misses do not -------------------------


def test_all_hits_never_load_the_model(no_model):
    texts = demo_texts()[:5]
    vectors = real_embed_texts(texts)
    assert len(vectors) == 5
    assert all(len(v) == DIM for v in vectors)


def test_empty_input_never_loads_the_model(no_model):
    assert real_embed_texts([]) == []


def test_only_misses_are_sent_to_the_model(fake_model):
    """Mixed batch: order is preserved and hits are not re-embedded."""
    hit_a, hit_b = demo_texts()[0], demo_texts()[1]
    miss = "a document nobody precomputed"
    table = precomputed.vectors_for(embeddings.MODEL_NAME)

    result = real_embed_texts([hit_a, miss, hit_b])

    assert fake_model == [[miss]]  # one call, carrying only the miss
    assert result[0] == table[precomputed.key(hit_a)]
    assert result[1] == [0.5] * DIM
    assert result[2] == table[precomputed.key(hit_b)]


# --- The regression this whole file exists for -------------------------------


def test_seeding_and_graphing_the_demo_loads_no_model(client, monkeypatch, no_model):
    """End to end: the two requests behind the "Load Demo Profile" click.

    Both ran the model before the table existed — ~55s for the seed and ~37s for
    the graph on Render free. `no_model` turns any remaining inference into a
    failure, so this pins the fix rather than just measuring it.
    """
    monkeypatch.setattr(embeddings, "embed_texts", real_embed_texts)

    assert client.post("/api/seed-demo").json()["seeded"] == 10

    graph = client.get("/api/graph").json()
    assert len([n for n in graph["nodes"] if n["type"] == "document"]) == 10


# --- Opt-in: the shipped vectors are still what the real model produces -------


@pytest.mark.model
def test_shipped_vectors_match_the_live_model():
    """Catches the table drifting away from the model itself — a model upgrade,
    a chromadb change to tokenization or pooling. Bypasses `embed_texts`, which
    would otherwise answer from the very table under test."""
    texts = demo_texts()
    table = precomputed.vectors_for(embeddings.MODEL_NAME)
    fresh = [v.tolist() for v in embeddings._get_model()(texts)]

    for text, expected in zip(texts, fresh):
        stored = table[precomputed.key(text)]
        cosine = sum(a * b for a, b in zip(stored, expected))
        assert cosine > 0.9999, f"shipped vector drifted for: {text[:60]!r}"
