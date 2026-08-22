"""Categorization: response parsing, normalization, and failure degradation.

No network. These cover the paths a live test would only reach by luck — a
model that wraps JSON in a fence, returns "Certification" instead of
"Certifications", or reports confidence as 85 instead of 0.85.
"""

from __future__ import annotations

import pytest

from ai import categorizer
from ai.categorizer import CategorizationError, _parse_response, fallback_categorization
from models.document import Categorization

# This module tests the real categorizer, so it opts out of the stub fixture.
pytestmark = pytest.mark.nostub


# --- response parsing ------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        'Here you go:\n{"a": 1}\nHope that helps!',
    ],
    ids=["bare", "json-fence", "plain-fence", "prose-wrapped"],
)
def test_parses_json_through_common_model_drift(raw):
    assert _parse_response(raw) == {"a": 1}


@pytest.mark.parametrize(
    "raw", ["I cannot help with that.", "[1, 2]", ""],
    ids=["prose", "array-not-object", "empty"],
)
def test_rejects_unusable_responses(raw):
    with pytest.raises(CategorizationError):
        _parse_response(raw)


# --- normalization ---------------------------------------------------------


def test_normalizes_a_drifted_but_plausible_response():
    result = Categorization.model_validate({
        "document_type": "Certificate",           # wrong case
        "category": "Certification",              # missing plural
        "title": "  Python Cert  ",
        "date": "2024-3",                         # unpadded month
        "skills": "Python, SQL",                  # string instead of list
        "organizations": ["Coursera", "", None],  # junk entries
        "people": None,
        "confidence": 85,                         # percentage not fraction
    })

    assert result.document_type == "certificate"
    assert result.category == "Certifications"
    assert result.title == "Python Cert"
    assert result.date == "2024-03"
    assert result.skills == ["Python", "SQL"]
    assert result.organizations == ["Coursera"]
    assert result.people == []
    assert result.confidence == 0.85


def test_unknown_vocabulary_falls_back_instead_of_raising():
    result = Categorization.model_validate({
        "document_type": "receipt",
        "category": "Groceries",
        "date": "sometime in 2024",
        "confidence": "high",
    })

    assert result.document_type == "other"
    assert result.category == "Uncategorized"
    assert result.date is None
    assert result.confidence == 0.0


@pytest.mark.parametrize(
    "value, expected",
    [("2023", "2023"), ("2024-3", "2024-03"), ("null", None), ("", None),
     ("unknown", None), ("March 2024", None), (None, None)],
)
def test_date_normalization(value, expected):
    """A malformed date is worse than none — the timeline sorts on this column."""
    assert Categorization.model_validate({"date": value}).date == expected


def test_empty_payload_is_valid():
    assert Categorization.model_validate({}).category == "Uncategorized"


@pytest.mark.parametrize("value, expected", [(85, 0.85), (0.9, 0.9), (5, 0.05), (-1, 0.0)])
def test_confidence_is_clamped_to_unit_range(value, expected):
    assert Categorization.model_validate({"confidence": value}).confidence == expected


# --- fallback --------------------------------------------------------------


@pytest.mark.parametrize(
    "filename, category, doc_type",
    [
        ("python_certificate_2023.pdf", "Certifications", "certificate"),
        ("XYZ-Internship-Offer.pdf", "Internships", "internship_letter"),
        ("my_resume.pdf", "Academics", "resume"),
        ("semester3_marksheet.pdf", "Academics", "other"),
        ("scan001.pdf", "Uncategorized", "other"),
    ],
)
def test_fallback_infers_from_filename(filename, category, doc_type):
    result = fallback_categorization(filename, "no_text")

    assert result.category == category
    assert result.document_type == doc_type
    assert result.confidence == 0.0, "fallback must be marked unverified"


def test_fallback_extracts_a_year_from_the_filename():
    assert fallback_categorization("cert_2023.pdf", "no_text").date == "2023"


# --- degradation -----------------------------------------------------------


def test_missing_api_key_degrades_instead_of_raising(monkeypatch):
    monkeypatch.setattr(categorizer.settings, "gemini_api_key", "")

    result = categorizer.categorize("A Python certificate.", "python_certificate.pdf")

    assert result.confidence == 0.0
    assert result.category == "Certifications"
    # Item B: a missing key is a structured, NON-retryable degradation.
    assert result.degraded_reason == "no_api_key"
    assert result.retryable is False


def test_empty_text_skips_the_api_call(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("should not call the API for empty text")

    monkeypatch.setattr(categorizer, "_get_model", _boom)

    assert categorizer.categorize("", "scan.pdf").confidence == 0.0


def test_api_failure_degrades_instead_of_raising(monkeypatch):
    monkeypatch.setattr(categorizer.settings, "gemini_api_key", "fake-key")
    monkeypatch.setattr(categorizer, "_rate_limiter", type("N", (), {"wait": lambda s: None})())

    class _Exploding:
        def generate_content(self, prompt):
            raise RuntimeError("503 backend unavailable")

    monkeypatch.setattr(categorizer, "_get_model", lambda: _Exploding())

    result = categorizer.categorize("Some certificate text here.", "cert.pdf")

    assert result.confidence == 0.0
    # Asserts the failure is *communicated*, not that a particular word is
    # used — the previous version pinned the literal string "error", which is
    # the sort of assertion that blocks a wording fix without catching a bug.
    assert result.summary and "filename" in result.summary.lower()
    assert result.category  # still usable, still lands in the database
    # Item B: a 503 is transport-level and retryable.
    assert result.degraded_reason == "unreachable"
    assert result.retryable is True


@pytest.mark.nostub
@pytest.mark.parametrize(
    "exc,expected",
    [
        (RuntimeError("429 Resource has been exhausted (quota)"), "quota"),
        (type("ResourceExhausted", (Exception,), {})("slow down"), "quota"),
        (RuntimeError("504 Deadline Exceeded"), "respond in time"),
        (RuntimeError("503 backend unavailable"), "could not be reached"),
        (RuntimeError("something odd"), "could not be reached"),
    ],
)
def test_failure_reasons_are_readable(exc, expected):
    """A rate limit clears itself; the card should say so rather than printing
    an SDK class name.

    "unavailable" is gRPC's service-unavailable status, not a timeout — the
    503 case above pins that, because bucketing it as a timeout tells the user
    their request was slow when it was actually refused.
    """
    assert expected in categorizer._human_reason(exc)


# --- result cache: the one RPD mitigation this module can make ---------------


class _CountingModel:
    """Returns a valid classification and counts how often it was asked."""

    def __init__(self):
        self.calls = 0

    def generate_content(self, prompt):
        self.calls += 1
        payload = (
            '{"document_type": "certificate", "category": "Certifications",'
            ' "title": "Python Certificate", "summary": "A Python course certificate.",'
            ' "date": "2024-03", "skills": ["Python"], "organizations": [], "people": [],'
            ' "confidence": 0.9}'
        )
        return type("R", (), {"text": payload})()


@pytest.fixture
def counting_model(monkeypatch):
    monkeypatch.setattr(categorizer.settings, "gemini_api_key", "fake-key")
    monkeypatch.setattr(categorizer, "_rate_limiter", type("N", (), {"wait": lambda s: None})())
    model = _CountingModel()
    monkeypatch.setattr(categorizer, "_get_model", lambda: model)
    categorizer.clear_cache()
    yield model
    categorizer.clear_cache()


def test_the_same_text_is_classified_once(counting_model):
    """The cheapest win against 20 requests/day: re-uploading a file already
    sent, or /recategorize on text that has not changed, used to spend a call."""
    text = "This certifies completion of the Python Programming course."

    first = categorizer.categorize(text, "cert.pdf")
    second = categorizer.categorize(text, "cert.pdf")

    assert counting_model.calls == 1
    assert second.title == first.title
    assert second.category == first.category


def test_a_different_filename_is_a_different_classification(counting_model):
    """The key is the whole prompt, and the filename is in it — the model is
    told the filename and can classify on it, so two names are two questions."""
    text = "This certifies completion of the Python Programming course."

    categorizer.categorize(text, "cert.pdf")
    categorizer.categorize(text, "resume.pdf")

    assert counting_model.calls == 2


def test_a_cache_hit_cannot_be_mutated_by_an_earlier_caller(counting_model):
    text = "This certifies completion of the Python Programming course."

    first = categorizer.categorize(text, "cert.pdf")
    first.title = "MUTATED"
    second = categorizer.categorize(text, "cert.pdf")

    assert second.title == "Python Certificate"


def test_a_degraded_result_is_never_cached(monkeypatch):
    """A failure must not be replayed — `retryable` promises a fresh attempt."""
    monkeypatch.setattr(categorizer.settings, "gemini_api_key", "fake-key")
    monkeypatch.setattr(categorizer, "_rate_limiter", type("N", (), {"wait": lambda s: None})())
    categorizer.clear_cache()

    calls = []

    class _Exploding:
        def generate_content(self, prompt):
            calls.append(prompt)
            raise RuntimeError("503 backend unavailable")

    monkeypatch.setattr(categorizer, "_get_model", lambda: _Exploding())

    text = "Some certificate text here."
    assert categorizer.categorize(text, "cert.pdf").degraded_reason == "unreachable"
    assert categorizer.categorize(text, "cert.pdf").degraded_reason == "unreachable"

    assert len(calls) == 2
    categorizer.clear_cache()


def test_the_cache_is_bounded(counting_model, monkeypatch):
    monkeypatch.setattr(categorizer, "_CACHE_MAX", 3)
    for i in range(5):
        categorizer.categorize(f"Certificate number {i} for the Python course.", "c.pdf")

    assert len(categorizer._cache) <= 3
