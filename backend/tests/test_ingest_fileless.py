"""End-to-end tests for the two fileless ingestion paths: URLs and text entries.

Both were dead ends before Phase 3 — `/api/ingest-url` scraped and discarded,
`/api/ingest-text` did not exist. The point of most of these tests is that the
content actually reaches SQLite and shows up alongside uploads.

Network is never touched: `url_guard.safe_get` is stubbed for happy paths, and
the blocked-destination cases resolve locally.
"""

from __future__ import annotations

import pytest

from db import database
from ingestion import url_guard, web_scraper
from ingestion.scrape_result import ScrapeResult

PAGE = b"""
<html><head><title>Jane Doe - Portfolio</title></head>
<body>
  <nav>Home About Contact</nav>
  <h1>Machine Learning Projects</h1>
  <p>Built an ML pipeline with scikit-learn during 2024.</p>
  <script>console.log('ignored')</script>
  <footer>(c) 2024</footer>
</body></html>
"""


@pytest.fixture
def fake_page(monkeypatch):
    """Serve PAGE for any URL, bypassing DNS and the network but not the route."""
    def _get(url, **kwargs):
        return url_guard.SafeResponse(
            url=url, status_code=200, content=PAGE, encoding="utf-8"
        )

    monkeypatch.setattr(url_guard, "safe_get", _get)
    monkeypatch.setattr(web_scraper.url_guard, "safe_get", _get)
    monkeypatch.setattr(url_guard, "_resolve", lambda host, port: ["93.184.216.34"])


# --- URL ingestion ---------------------------------------------------------


def test_ingest_url_persists_the_document(client, fake_page):
    """The Phase 2 gap: URLs were scraped and then thrown away."""
    resp = client.post("/api/ingest-url", json={"url": "https://example.com/portfolio"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    stored = database.get_document(body["id"])
    assert stored is not None, "URL ingestion must write a documents row"
    assert stored["file_type"] == "url"
    assert stored["source_url"] == "https://example.com/portfolio"
    assert "scikit-learn" in stored["raw_text"]


def test_ingest_url_returns_categorization(client, fake_page, stub_result):
    body = client.post(
        "/api/ingest-url", json={"url": "https://example.com/portfolio"}
    ).json()
    assert body["categorization"]["category"] == stub_result.category
    assert body["categorization"]["skills"] == stub_result.skills


def test_ingested_url_appears_in_the_document_list(client, fake_page):
    """A URL document must be browsable exactly like an uploaded file."""
    doc_id = client.post(
        "/api/ingest-url", json={"url": "https://example.com/portfolio"}
    ).json()["id"]

    listing = client.get("/api/documents").json()
    assert doc_id in [d["id"] for d in listing]


def test_ingest_url_strips_page_chrome(client, fake_page):
    doc_id = client.post(
        "/api/ingest-url", json={"url": "https://example.com/portfolio"}
    ).json()["id"]
    text = database.get_document(doc_id)["raw_text"]
    assert "console.log" not in text
    assert "Home About Contact" not in text


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",
    "http://127.0.0.1:8000/api/documents",
    "file:///etc/passwd",
])
def test_ingest_url_rejects_unsafe_destinations(client, url):
    """The SSRF surface the handoff flagged, exercised through the real route.

    Validated by mutation, and the result is worth recording: removing the
    `validate_url` call from `scrape_url` alone leaves this **green**, because
    `safe_get` validates again before every request. The two layers are
    genuinely redundant for this path. Removing both makes all three cases
    return 200, with the metadata-service body in the response.

    Keep both. `scrape_url` is the guard for any future handler that reads a
    URL without going through `safe_get`; `safe_get` is the guard that covers
    redirect hops, which `scrape_url` never sees.
    """
    resp = client.post("/api/ingest-url", json={"url": url})
    assert resp.status_code == 400, resp.text
    assert database.list_documents() == [], "a blocked URL must not be stored"


def test_blocked_url_error_does_not_echo_resolved_address(client, monkeypatch):
    """The 400 must not report *where* a hostname resolved to.

    Deliberately uses a hostname, not a literal IP: when the caller supplies
    `http://10.0.0.5/` directly, echoing "10.0.0.5" back tells them nothing
    they did not already know. The leak that matters is resolution — replying
    that `internal.corp` is 10.0.0.5 turns this endpoint into a DNS oracle for
    the deployment's private network.
    """
    monkeypatch.setattr(url_guard, "_resolve", lambda host, port: ["10.0.0.5"])
    resp = client.post("/api/ingest-url", json={"url": "http://internal.corp/admin"})
    assert resp.status_code == 400
    assert "10.0.0.5" not in resp.json()["detail"]


def test_ingest_url_with_no_extractable_text_is_rejected(client, monkeypatch):
    """plan.md § Risk Mitigation: a site we cannot read degrades to 'upload it manually'."""
    monkeypatch.setattr(
        "ingestion.url_scraper.scrape_url",
        lambda url: ScrapeResult(url, "", "", "web", ["Fetch failed: 403"]),
    )
    resp = client.post("/api/ingest-url", json={"url": "https://blocked.example.com/"})
    assert resp.status_code == 422
    assert database.list_documents() == []


# --- Written responses -----------------------------------------------------

ENTRY = "Led the Data Science Club in 2024, organized 5 workshops on Python and ML."


def test_ingest_text_persists_the_document(client):
    resp = client.post("/api/ingest-text", json={"text": ENTRY})
    assert resp.status_code == 200, resp.text

    stored = database.get_document(resp.json()["id"])
    assert stored["file_type"] == "text_entry"
    assert stored["raw_text"] == ENTRY


def test_text_entry_has_no_original_file(client, isolated_storage):
    """plan.md §4 Module 1: a written response is stored with no original file.

    The preservation guarantee covers uploads; there is nothing to preserve
    here. `original_path` is empty rather than NULL so readers keep one path.
    """
    doc_id = client.post("/api/ingest-text", json={"text": ENTRY}).json()["id"]

    assert database.get_document(doc_id)["original_path"] == ""
    assert list((isolated_storage / "uploads").rglob("*")) == []


def test_text_entry_derives_a_filename_from_the_first_line(client):
    body = client.post(
        "/api/ingest-text", json={"text": "Won the 2025 university hackathon.\nDetails follow."}
    ).json()
    assert body["filename"] == "Won the 2025 university hackathon."


def test_text_entry_appears_in_the_document_list(client):
    doc_id = client.post("/api/ingest-text", json={"text": ENTRY}).json()["id"]
    assert doc_id in [d["id"] for d in client.get("/api/documents").json()]


def test_text_entry_stores_entities(client, stub_result):
    """Entity rows are what Module 3 joins on, so they must be written here too."""
    doc_id = client.post("/api/ingest-text", json={"text": ENTRY}).json()["id"]
    assert database.get_document(doc_id)["skills"] == stub_result.skills


def test_text_entry_normalizes_line_endings(client):
    doc_id = client.post(
        "/api/ingest-text", json={"text": "Led the club in 2024.\r\nRan five workshops.\r\n"}
    ).json()["id"]
    assert "\r" not in database.get_document(doc_id)["raw_text"]


# Explicit ids: pytest builds the test id from the parameter value, and the
# oversized case would produce a 50k-character id that overflows the
# PYTEST_CURRENT_TEST environment variable on Windows (32767 char limit).
@pytest.mark.parametrize("text", [
    pytest.param("", id="empty"),
    pytest.param("   \n\t  ", id="whitespace-only"),
    pytest.param("Won", id="too-short-to-categorize"),
    pytest.param("x" * 50_001, id="over-the-size-cap"),
])
def test_invalid_text_entries_are_rejected(client, text):
    resp = client.post("/api/ingest-text", json={"text": text})
    assert resp.status_code == 400, resp.text
    assert database.list_documents() == []
