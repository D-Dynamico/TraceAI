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
    result = fallback_categorization(filename, "test")

    assert result.category == category
    assert result.document_type == doc_type
    assert result.confidence == 0.0, "fallback must be marked unverified"


def test_fallback_extracts_a_year_from_the_filename():
    assert fallback_categorization("cert_2023.pdf", "test").date == "2023"


# --- degradation -----------------------------------------------------------


def test_missing_api_key_degrades_instead_of_raising(monkeypatch):
    monkeypatch.setattr(categorizer.settings, "gemini_api_key", "")

    result = categorizer.categorize("A Python certificate.", "python_certificate.pdf")

    assert result.confidence == 0.0
    assert result.category == "Certifications"


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
    assert "error" in result.summary.lower()
