"""Opt-in tests that call the real Gemini API.

Deselected by default (see pytest.ini). Run with:

    pytest -m live

These cost quota and need GEMINI_API_KEY, but they are the only tests that
catch a retired model id, a changed response shape, or an expired key — the
stubbed suite would keep passing through all three.
"""

from __future__ import annotations

import pytest

from ai import categorizer
from conftest import upload
from db import database

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def require_api_key():
    if not categorizer.is_configured():
        pytest.skip("GEMINI_API_KEY not set")


CERTIFICATE = """Coursera

CERTIFICATE OF COMPLETION

This is to certify that the bearer has successfully completed the online
non-credit course: Python for Data Science and Machine Learning,
offered through Coursera by Stanford University.

Date of completion: March 2024
Grade achieved: 94%

Topics covered: Python programming, NumPy, Pandas, data visualization
with Matplotlib, supervised learning, and scikit-learn.
"""


def test_model_id_and_key_are_valid():
    """Fails fast if the configured model was retired or the key was revoked."""
    result = categorizer.categorize(CERTIFICATE, "python_ml_certificate.txt")

    assert result.confidence > 0.0, (
        "got a fallback result — the model id, key, or quota is the likely cause"
    )


def test_classifies_a_certificate_correctly():
    result = categorizer.categorize(CERTIFICATE, "python_ml_certificate.txt")

    assert result.category == "Certifications"
    assert result.document_type == "certificate"
    assert result.date == "2024-03"
    assert result.title.strip()
    assert result.summary.strip()
    assert result.skills
    assert any("coursera" in o.lower() or "stanford" in o.lower()
               for o in result.organizations), result.organizations


def test_full_upload_pipeline_against_the_live_api(client):
    """upload -> live Gemini -> JSON parse -> SQLite write -> read back."""
    resp = upload(client, "python_ml_certificate.txt", CERTIFICATE.encode())
    assert resp.status_code == 200, resp.text

    doc_id = resp.json()["id"]
    cat = resp.json()["categorization"]
    assert cat["confidence"] > 0.0
    assert cat["category"] == "Certifications"

    row = database.get_document(doc_id)
    assert row["category"] == cat["category"]
    assert sorted(row["skills"]) == sorted(cat["skills"])

    assert client.get(f"/api/documents/{doc_id}/verify").json()["verified"] is True
