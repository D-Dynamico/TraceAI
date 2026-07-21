"""Career-path inference + endpoint + graph merge (plan.md §4 Module 3 Layer C).

The Gemini call is stubbed: a fake model returns canned JSON (or raises), so the
inference logic — index mapping, clamping, and the never-raises degradation — is
exercised offline. The `live` marker covers the real model separately.
"""

from __future__ import annotations

import json
import uuid

import pytest

from ai import career_path
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
    """Skip the 6.5s spacer — tests must not actually sleep."""
    monkeypatch.setattr(career_path, "_rate_limiter", type("N", (), {"wait": lambda s: None})())
    monkeypatch.setattr(settings, "gemini_api_key", "fake-key")


def _seed(category, skills, *, title="Doc"):
    doc_id = uuid.uuid4().hex
    database.insert_document(
        doc_id=doc_id, user_id="demo", filename=title, original_path="/x",
        file_type="pdf", checksum="c", raw_text="text", upload_date="2025-01-01 00:00:00",
        category=category, title=title, skills=skills,
    )
    return doc_id


def _model_returning(paths):
    return _FakeModel(text=json.dumps({"career_paths": paths}))


def test_infer_maps_evidence_numbers_to_document_ids(monkeypatch, no_rate_limit):
    a = _seed("Certifications", ["Python"], title="Python Cert")
    b = _seed("Projects", ["Python", "pandas"], title="ML Project")

    monkeypatch.setattr(career_path, "_get_model", lambda: _model_returning([
        {"title": "AI/ML Engineer", "match_score": 0.87, "evidence": [0, 1],
         "skill_gaps": ["MLOps"]},
    ]))

    result = career_path.infer("demo")

    assert result.degraded_reason is None
    assert len(result.paths) == 1
    path = result.paths[0]
    assert path.title == "AI/ML Engineer"
    assert path.match_score == 0.87
    assert set(path.evidence_doc_ids) == {a, b}
    assert path.skill_gaps == ["MLOps"]


def test_out_of_range_evidence_index_is_dropped(monkeypatch, no_rate_limit):
    a = _seed("Projects", ["Python"])
    _seed("Projects", ["SQL"])

    monkeypatch.setattr(career_path, "_get_model", lambda: _model_returning([
        {"title": "Data Analyst", "match_score": 0.6, "evidence": [0, 99]},
    ]))

    path = career_path.infer("demo").paths[0]
    assert path.evidence_doc_ids == [a], "a hallucinated document number must not fabricate an edge"


def test_score_over_one_is_treated_as_a_percentage(monkeypatch, no_rate_limit):
    _seed("Projects", ["Python"])
    _seed("Projects", ["SQL"])
    monkeypatch.setattr(career_path, "_get_model", lambda: _model_returning([
        {"title": "X", "match_score": 87, "evidence": [0]},
    ]))
    assert career_path.infer("demo").paths[0].match_score == 0.87


def test_api_failure_never_raises_and_reports_reason(monkeypatch, no_rate_limit):
    _seed("Projects", ["Python"])
    _seed("Projects", ["SQL"])
    monkeypatch.setattr(
        career_path, "_get_model",
        lambda: _FakeModel(exc=RuntimeError("429 Resource exhausted (quota)")),
    )

    result = career_path.infer("demo")
    assert result.paths == []
    assert result.degraded_reason == "quota"
    assert result.retryable is True


def test_missing_key_degrades_without_calling_the_model(monkeypatch):
    _seed("Projects", ["Python"])
    _seed("Projects", ["SQL"])
    monkeypatch.setattr(settings, "gemini_api_key", "")

    result = career_path.infer("demo")
    assert result.paths == []
    assert result.degraded_reason == "no_api_key"
    assert result.retryable is False


def test_too_few_documents_is_empty_not_degraded(monkeypatch, no_rate_limit):
    _seed("Projects", ["Python"])  # only one
    result = career_path.infer("demo")
    assert result.paths == []
    assert result.degraded_reason is None


# --- endpoint + graph merge ------------------------------------------------


def test_endpoint_persists_and_graph_merges_career_nodes(client, monkeypatch):
    a = _seed("Certifications", ["Python"], title="Cert")
    b = _seed("Internships", ["Python"], title="Internship")

    def _fake_infer(user_id="demo"):
        return career_path.CareerPathInference(
            paths=[career_path.CareerPath(
                id="cp1", title="AI/ML Engineer", match_score=0.9,
                evidence_doc_ids=[a, b], skill_gaps=["MLOps"],
            )],
            degraded_reason=None, retryable=False,
        )

    monkeypatch.setattr(career_path, "infer", _fake_infer)

    resp = client.post("/api/career-paths").json()
    assert resp["paths"][0]["title"] == "AI/ML Engineer"
    assert resp["degraded_reason"] is None

    graph = client.get("/api/graph").json()
    career = [n for n in graph["nodes"] if n["type"] == "career_path"]
    assert len(career) == 1 and career[0]["match_score"] == 0.9

    leads = [e for e in graph["edges"] if e["relation_type"] == "leads_to"]
    assert {e["source"] for e in leads} == {a, b}
    assert all(e["target"] == career[0]["id"] for e in leads)


def test_degraded_inference_does_not_wipe_existing_paths(client, monkeypatch):
    """A quota wall on re-inference must not erase a good previous set."""
    _seed("Projects", ["Python"])
    database.replace_career_paths([
        {"id": "keep", "title": "Kept Path", "match_score": 0.8,
         "evidence_doc_ids": [], "skill_gaps": []},
    ])

    monkeypatch.setattr(
        career_path, "infer",
        lambda user_id="demo": career_path.CareerPathInference([], "quota", True),
    )

    resp = client.post("/api/career-paths").json()
    assert resp["degraded_reason"] == "quota"
    assert database.list_career_paths()[0]["title"] == "Kept Path"
