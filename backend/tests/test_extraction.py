"""Multi-format text extraction and upload error paths (Phase 1)."""

from __future__ import annotations

import pytest
from conftest import DOCX_MIME, PPTX_MIME, make_docx, make_pptx, upload


def test_health_reports_ok(client):
    resp = client.get("/api/health")
    body = resp.json()

    assert resp.status_code == 200
    assert body["status"] == "ok"
    assert "ai_configured" in body
    # "ok" has to mean the stores answered, not just that the process is up.
    assert body["database"] == "ok"
    assert body["vector_store"] == "ok"


def test_health_reports_a_broken_vector_store(client, monkeypatch):
    """The failure this check exists for.

    `main.py` swallows a vector-store sync error on purpose — a broken store
    must not stop the app booting — so the health check is the only thing that
    can say search is degraded. Reporting `ok` regardless made it useless in
    exactly the case it was wired to Render for.
    """
    from ai import embeddings

    def _broken():
        raise RuntimeError("chroma is gone")

    monkeypatch.setattr(embeddings, "indexed_count", _broken)

    resp = client.get("/api/health")

    assert resp.status_code == 503
    assert resp.json()["status"] == "degraded"
    assert resp.json()["vector_store"] == "error"
    assert resp.json()["database"] == "ok"  # named separately, not lumped together


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


def test_an_oversized_upload_is_refused_while_it_is_still_arriving(client, monkeypatch):
    """The 413 must fire *before* the body is buffered, not after.

    `await file.read()` read the whole thing and compared afterwards, so the cap
    documented the limit without enforcing it: the memory was already spent by
    the time the status was chosen. On a 512 MB instance with no auth in front
    of it, that is a one-request kill.

    Asserted by counting bytes actually read: the reader must stop within a
    chunk of the limit rather than consuming the full body.
    """
    from config import settings
    from routes import upload as upload_route

    monkeypatch.setattr(settings, "max_upload_bytes", 1024)
    monkeypatch.setattr(upload_route, "_UPLOAD_CHUNK", 256)

    read_total = 0
    real_read = upload_route._read_capped

    async def counting(file):
        class _Counting:
            async def read(self, size=-1):
                nonlocal read_total
                chunk = await file.read(size)
                read_total += len(chunk)
                return chunk

        return await real_read(_Counting())

    monkeypatch.setattr(upload_route, "_read_capped", counting)

    resp = upload(client, "big.txt", b"x" * (2 * 1024 * 1024))

    assert resp.status_code == 413
    assert read_total <= 1024 + 256  # the limit plus at most one chunk


def test_rejects_bad_url_scheme(client):
    resp = client.post("/api/ingest-url", json={"url": "ftp://example.com"})

    assert resp.status_code == 400
