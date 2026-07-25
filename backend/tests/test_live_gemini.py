"""Opt-in tests that call the real Gemini API.

Deselected by default (see pytest.ini). Run with:

    pytest -m live

These cost quota and need GEMINI_API_KEY, but they are the only tests that
catch a retired model id, a changed response shape, or an expired key — the
stubbed suite would keep passing through all three.
"""

from __future__ import annotations

import io
import uuid

import pytest

from ai import career_path, categorizer, rag, vision
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


def test_career_path_inference_against_the_live_api(client):
    """A coherent profile -> live Gemini -> parsed, index-mapped career paths."""
    profile = [
        ("Certifications", ["Python", "scikit-learn"], "Python ML Certificate"),
        ("Projects", ["Python", "pandas"], "ML Pipeline Project"),
        ("Internships", ["Python", "SQL"], "Data Automation Intern at XYZ Corp"),
    ]
    for category, skills, title in profile:
        database.insert_document(
            doc_id=uuid.uuid4().hex, user_id="demo", filename=title,
            original_path="/x", file_type="pdf", checksum="c",
            raw_text=f"{title}. Demonstrated skills: {', '.join(skills)}.",
            upload_date="2025-01-01 00:00:00", category=category, title=title,
            skills=skills,
        )

    result = career_path.infer("demo")

    assert result.degraded_reason is None, result.degraded_reason
    assert result.paths, "live inference returned no paths for a clear profile"
    path = result.paths[0]
    assert path.title.strip()
    assert 0.0 <= path.match_score <= 1.0
    known = {d["id"] for d in database.list_documents("demo")}
    assert all(doc_id in known for doc_id in path.evidence_doc_ids), (
        "evidence must map to real documents, not hallucinated indices"
    )


CERTIFICATE_LINES = [
    "CERTIFICATE OF COMPLETION",
    "Awarded to Dayanand Kori",
    "Deep Learning Specialization",
    "Issued by Coursera",
    "March 2024",
]


def _rendered_certificate() -> bytes:
    """Render the lines above into a PNG — a stand-in for a scanned certificate.

    Generated rather than committed: a fixture image would be a binary blob in
    the repo whose expected transcript nobody could check by reading the diff.
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (960, 420), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=34)
    for i, line in enumerate(CERTIFICATE_LINES):
        draw.text((50, 40 + i * 68), line, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_vision_ocr_against_the_live_api(tmp_path):
    """The only test that proves the Vision rung actually works.

    The offline suite stubs `vision._generate`, so nothing else would catch the
    inline-blob part format changing, the configured model losing vision
    support, or the request exceeding a limit. Run it after touching `ai/`.
    """
    path = tmp_path / "certificate_scan.png"
    path.write_bytes(_rendered_certificate())

    result = vision.extract_text(path)

    assert result.degraded is None, result.degraded
    assert result.text.strip(), "live Vision returned no transcript"
    lowered = result.text.lower()
    assert "coursera" in lowered, result.text
    assert "2024" in result.text, result.text
    # A transcript, not a description: the model was told to transcribe, and a
    # description of a certificate would be indexed as if it had been read.
    assert "certificate" in lowered, result.text


def test_a_scanned_upload_is_searchable_end_to_end(client, tmp_path):
    """Scan -> live Vision -> live categorization -> SQLite, with real text.

    The failure this guards is silent: with no Vision rung and no Tesseract, the
    upload still returns 200 and stores `raw_text = ""`.
    """
    resp = upload(client, "certificate_scan.png", _rendered_certificate(), "image/png")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["method"] == "vision", body["method"]
    assert body["char_count"] > 0

    row = database.get_document(body["id"])
    assert "coursera" in row["raw_text"].lower()
    # And the text was good enough for the categorizer to work from.
    assert row["confidence"] > 0.0


def test_rag_synthesis_against_the_live_api():
    """A question over real sources -> live Gemini -> a grounded, cited answer."""
    docs = [
        {"id": "d0", "title": "Python ML Certificate", "category": "Certifications",
         "summary": "Completed a Python for Data Science and Machine Learning course.",
         "raw_text": "Certificate: Python for Data Science and Machine Learning. Skills: Python, pandas, scikit-learn."},
        {"id": "d1", "title": "Data Automation Internship", "category": "Internships",
         "summary": "Six-month internship automating data pipelines in Python and SQL.",
         "raw_text": "Internship at XYZ Corp. Built data automation with Python and SQL, deployed ML models."},
    ]

    result = rag.synthesize(
        "How does my Python certificate connect to my internship?", docs
    )

    assert result.degraded_reason is None, result.degraded_reason
    assert result.answer and result.answer.strip(), "live synthesis returned no answer"
    # Citations must map back to the sources provided, never a fabricated index.
    assert all(cid in {"d0", "d1"} for cid in result.cited_doc_ids), result.cited_doc_ids
