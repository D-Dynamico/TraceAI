"""RAG answer synthesis + the /answer endpoint (plan.md §4 Module 5 Path 2).

The Gemini call is stubbed: a fake model returns canned JSON (or raises), so the
synthesis logic — citation mapping, the grounding drop of out-of-range indices,
and the never-raises degradation — is exercised offline. The `live` marker covers
the real model separately (test_live_gemini.py).
"""

from __future__ import annotations

import json
import uuid

import pytest

from ai import rag
from config import settings
from db import database


class _FakeModel:
    def __init__(self, *, text=None, exc=None):
        self._text = text
        self._exc = exc

    def generate_content(self, prompt):
        if self._exc is not None:
            raise self._exc
        return type("R", (), {"text": self._text})()


@pytest.fixture
def no_rate_limit(monkeypatch):
    """Skip the 6.5s spacer — tests must not actually sleep — and set a key."""
    monkeypatch.setattr(rag, "_rate_limiter", type("N", (), {"wait": lambda s: None})())
    monkeypatch.setattr(settings, "gemini_api_key", "fake-key")


def _docs(*specs):
    """Build source dicts as synthesize() receives them (hydrated documents)."""
    out = []
    for title, summary in specs:
        out.append(
            {"id": uuid.uuid4().hex, "title": title, "category": "Projects",
             "summary": summary, "raw_text": summary}
        )
    return out


def _model_returning(answer, citations):
    return _FakeModel(text=json.dumps({"answer": answer, "citations": citations}))


def test_synthesize_maps_citation_numbers_to_document_ids(monkeypatch, no_rate_limit):
    docs = _docs(("Python Cert", "Learned Python."), ("ML Project", "Used Python."))
    monkeypatch.setattr(rag, "_get_model", lambda: _model_returning("Your cert taught the Python used in your project.", [0, 1]))

    result = rag.synthesize("how does my cert relate to my project?", docs)

    assert result.degraded_reason is None
    assert result.answer.startswith("Your cert")
    assert result.cited_doc_ids == [docs[0]["id"], docs[1]["id"]]


def test_out_of_range_citation_is_dropped(monkeypatch, no_rate_limit):
    docs = _docs(("A", "text"), ("B", "text"))
    monkeypatch.setattr(rag, "_get_model", lambda: _model_returning("answer", [0, 99]))

    result = rag.synthesize("q?", docs)
    assert result.cited_doc_ids == [docs[0]["id"]], "a hallucinated source number must not cite a document the answer never used"


def test_duplicate_citations_are_collapsed(monkeypatch, no_rate_limit):
    docs = _docs(("A", "text"), ("B", "text"))
    monkeypatch.setattr(rag, "_get_model", lambda: _model_returning("answer", [1, 1, 0]))

    assert rag.synthesize("q?", docs).cited_doc_ids == [docs[1]["id"], docs[0]["id"]]


def test_empty_answer_text_becomes_none(monkeypatch, no_rate_limit):
    docs = _docs(("A", "text"))
    monkeypatch.setattr(rag, "_get_model", lambda: _model_returning("   ", []))
    assert rag.synthesize("q?", docs).answer is None


def test_no_documents_is_not_a_failure(no_rate_limit):
    result = rag.synthesize("anything?", [])
    assert result.answer is None
    assert result.cited_doc_ids == []
    assert result.degraded_reason is None


def test_empty_query_is_not_a_failure(no_rate_limit):
    result = rag.synthesize("   ", _docs(("A", "text")))
    assert result.answer is None
    assert result.degraded_reason is None


def test_api_failure_never_raises_and_reports_reason(monkeypatch, no_rate_limit):
    docs = _docs(("A", "text"))
    monkeypatch.setattr(rag, "_get_model", lambda: _FakeModel(exc=RuntimeError("429 Resource exhausted (quota)")))

    result = rag.synthesize("q?", docs)
    assert result.answer is None
    assert result.degraded_reason == "quota"
    assert result.retryable is True


def test_unparseable_response_degrades(monkeypatch, no_rate_limit):
    docs = _docs(("A", "text"))
    monkeypatch.setattr(rag, "_get_model", lambda: _FakeModel(text="not json at all"))

    result = rag.synthesize("q?", docs)
    assert result.answer is None
    assert result.degraded_reason == "unreadable_response"
    assert result.retryable is True


def test_missing_key_degrades_without_calling_the_model(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    # If this reached the model it would raise (no key); it must degrade first.
    result = rag.synthesize("q?", _docs(("A", "text")))
    assert result.answer is None
    assert result.degraded_reason == "no_api_key"
    assert result.retryable is False


# --- endpoint --------------------------------------------------------------


def _seed(title, category="Projects", skills=("Python",)):
    doc_id = uuid.uuid4().hex
    database.insert_document(
        doc_id=doc_id, user_id="demo", filename=title, original_path="/x",
        file_type="pdf", checksum="c", raw_text="text", upload_date="2025-01-01 00:00:00",
        category=category, title=title, skills=list(skills),
    )
    return doc_id


def test_answer_endpoint_synthesizes_over_given_ids(client, monkeypatch):
    a = _seed("Cert", "Certifications")
    b = _seed("Internship", "Internships")

    captured = {}

    def _fake_synthesize(query, docs):
        captured["query"] = query
        captured["ids"] = [d["id"] for d in docs]
        return rag.RagAnswer(answer="Synthesized.", cited_doc_ids=[a], degraded_reason=None, retryable=False)

    monkeypatch.setattr(rag, "synthesize", _fake_synthesize)

    resp = client.post("/api/answer", json={"query": "how do these connect?", "doc_ids": [a, b]}).json()
    assert resp["answer"] == "Synthesized."
    assert resp["cited_doc_ids"] == [a]
    # The endpoint hydrated exactly the requested ids, in order, from SQLite.
    assert captured["ids"] == [a, b]


def test_answer_endpoint_skips_ids_that_no_longer_exist(client, monkeypatch):
    a = _seed("Cert")
    seen = {}

    def _fake(query, docs):
        seen["ids"] = [d["id"] for d in docs]
        return rag.RagAnswer(None, [], None, False)

    monkeypatch.setattr(rag, "synthesize", _fake)

    client.post("/api/answer", json={"query": "q?", "doc_ids": [a, "ghost"]})
    assert seen["ids"] == [a], "an id gone from SQLite is dropped, not passed as None"


def test_answer_endpoint_rejects_empty_query(client):
    assert client.post("/api/answer", json={"query": "  ", "doc_ids": []}).status_code == 400


def test_search_flags_questions_answerable_but_not_filters(client):
    question = client.post("/api/search", json={"query": "how does my cert connect to my internship?"}).json()
    assert question["answerable"] is True

    filt = client.post("/api/search", json={"query": "show all my certificates"}).json()
    assert filt["answerable"] is False
