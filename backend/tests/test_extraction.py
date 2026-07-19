"""Multi-format text extraction and upload error paths (Phase 1)."""

from __future__ import annotations

import pytest
from conftest import DOCX_MIME, PPTX_MIME, make_docx, make_pptx, upload


def test_health_reports_ok(client):
    body = client.get("/api/health").json()

    assert body["status"] == "ok"
    assert "ai_configured" in body


def test_health_never_exposes_the_api_key(client, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "gemini_api_key", "SECRET-KEY-VALUE-123")

    body = client.get("/api/health").text

    assert "SECRET-KEY-VALUE-123" not in body


def test_extracts_plain_text(client):
    resp = upload(client, "notes.txt", b"Achievement: Won the AI Hackathon 2025.")

    body = resp.json()
    assert resp.status_code == 200
    assert body["file_type"] == "text"
    assert body["method"] == "native"
    assert body["used_ocr"] is False
    assert "Hackathon" in body["text_preview"]


def test_extracts_docx_including_headings(client):
    resp = upload(client, "cert.docx", make_docx(), DOCX_MIME)

    body = resp.json()
    assert resp.status_code == 200
    assert body["file_type"] == "docx"
    assert "Internship Completion Certificate" in body["text_preview"]
    assert "Acme AI" in body["text_preview"]


def test_extracts_pptx_slide_text(client):
    resp = upload(client, "deck.pptx", make_pptx(), PPTX_MIME)

    body = resp.json()
    assert resp.status_code == 200
    assert body["file_type"] == "pptx"
    assert "ML Pipeline Project" in body["text_preview"]


@pytest.mark.parametrize(
    "name, data, expected_status",
    [
        ("data.xyz", b"nope", 415),          # unsupported extension
        ("noext", b"nope", 415),             # no extension at all
        ("empty.txt", b"", 400),             # empty file
    ],
)
def test_rejects_bad_uploads(client, name, data, expected_status):
    assert upload(client, name, data).status_code == expected_status


def test_rejects_oversized_upload(client, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "max_upload_bytes", 10)

    assert upload(client, "big.txt", b"x" * 50).status_code == 413


def test_rejects_bad_url_scheme(client):
    resp = client.post("/api/ingest-url", json={"url": "ftp://example.com"})

    assert resp.status_code == 400
