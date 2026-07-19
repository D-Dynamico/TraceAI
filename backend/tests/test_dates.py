"""Date resolution: known dates, source dates, and the assumed-date flag.

plan.md §10 has two halves — fall back to the upload date when no date is found,
*and* flag it for review. Only the first half existed, which is how a repo
created in 2011 lands on the timeline at the moment it was ingested.
"""

from __future__ import annotations

import pytest

from db import database
from ingestion import github_scraper, url_guard, web_scraper
from ingestion.scrape_result import ScrapeResult
from models.document import DocumentSummary

@pytest.fixture
def no_date_found(monkeypatch, stub_result):
    """Make the stubbed categorizer report that it found no date.

    The autouse stub in conftest returns date="2024-03" for *everything*, so a
    test about missing dates would otherwise assert against the stub's date and
    pass for the wrong reason — it would never exercise the fallback at all.
    Four tests here failed on exactly that before this fixture existed.
    """
    from ai import categorizer
    import routes.upload as upload_route

    def _fake(text: str, filename: str = ""):
        return stub_result.model_copy(deep=True, update={"date": None})

    monkeypatch.setattr(categorizer, "categorize", _fake)
    monkeypatch.setattr(upload_route.categorizer, "categorize", _fake)


REPO_JSON = {
    "full_name": "psf/requests",
    "description": "A simple, yet elegant, HTTP library.",
    "language": "Python",
    "topics": ["http", "python"],
    "created_at": "2011-02-13T18:38:17Z",
}


# --- created_at parsing ----------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    ("2011-02-13T18:38:17Z", "2011-02"),
    ("2024-12-01T00:00:00Z", "2024-12"),
    ("2024-01-31", "2024-01"),
    (None, None),
    ("", None),
    ("not-a-date", None),
    ("2011-13-01T00:00:00Z", None),   # month 13
    ("2011-00-01T00:00:00Z", None),   # month 0
    (12345, None),
])
def test_month_from_iso(value, expected):
    assert github_scraper.month_from_iso(value) == expected


# --- The scraper carries the date -------------------------------------------


@pytest.fixture
def fake_github(monkeypatch):
    """Serve the repo JSON and no README, without touching the network."""
    def _get(url, **kwargs):
        if "api.github.com" in url:
            import json
            return url_guard.SafeResponse(
                url=url, status_code=200, content=json.dumps(REPO_JSON).encode()
            )
        return url_guard.SafeResponse(url=url, status_code=404, content=b"")

    monkeypatch.setattr(github_scraper.url_guard, "safe_get", _get)


def test_github_scrape_extracts_creation_date(fake_github):
    result = github_scraper.scrape("psf", "requests", "https://github.com/psf/requests")
    assert result.source_date == "2011-02"
    assert "Repository created: 2011-02" in result.text


# --- The date reaches the database ------------------------------------------


@pytest.fixture
def fake_repo_url(monkeypatch):
    """/api/ingest-url returns a GitHub result carrying a source_date."""
    monkeypatch.setattr(
        "ingestion.url_scraper.scrape_url",
        lambda url: ScrapeResult(
            url, "Description: a library.", "psf/requests", "github", [], "2011-02"
        ),
    )


def test_repo_creation_date_is_stored_as_the_extracted_date(client, fake_repo_url, no_date_found):
    """Without this the row has no date and the timeline invents one.

    Mutation: drop `or date_fallback` in _categorize_and_store -> extracted_date
    is None and date_source flips to "assumed".
    """
    doc_id = client.post(
        "/api/ingest-url", json={"url": "https://github.com/psf/requests"}
    ).json()["id"]

    stored = database.get_document(doc_id)
    assert stored["extracted_date"] == "2011-02"
    assert stored["effective_date"] == "2011-02"
    assert stored["date_source"] == "extracted"


def test_repo_date_is_reflected_in_the_response(client, fake_repo_url, no_date_found):
    """The card reads categorization.date, which Gemini returned as null."""
    body = client.post(
        "/api/ingest-url", json={"url": "https://github.com/psf/requests"}
    ).json()
    assert body["categorization"]["date"] == "2011-02"


def test_a_date_in_the_content_wins_over_the_source_date(client, monkeypatch, stub_result):
    """The stub categorizer returns 2024-03; the repo claims 2011-02.

    A date read out of the document describes the achievement; a repo's creation
    date is only a proxy. Content wins.
    """
    monkeypatch.setattr(
        "ingestion.url_scraper.scrape_url",
        lambda url: ScrapeResult(url, "text", "t", "github", [], "2011-02"),
    )
    doc_id = client.post("/api/ingest-url", json={"url": "https://github.com/a/b"}).json()["id"]
    assert database.get_document(doc_id)["extracted_date"] == stub_result.date == "2024-03"


def test_generic_web_page_has_no_source_date(client, monkeypatch, no_date_found):
    monkeypatch.setattr(
        "ingestion.url_scraper.scrape_url",
        lambda url: ScrapeResult(url, "some text", "A Page", "web", []),
    )
    doc_id = client.post("/api/ingest-url", json={"url": "https://example.com"}).json()["id"]
    assert database.get_document(doc_id)["date_source"] == "assumed"


# --- The assumed-date flag ---------------------------------------------------


def test_missing_date_is_flagged_not_silently_filled(client, monkeypatch, no_date_found):
    """The half of plan.md §10 that was never implemented.

    Mutation: have _resolve_date always report "extracted" -> this fails. The
    fallback alone is not enough; a consumer must be able to tell the two apart.
    """
    monkeypatch.setattr(
        "ingestion.url_scraper.scrape_url",
        lambda url: ScrapeResult(url, "some text", "A Page", "web", []),
    )
    doc_id = client.post("/api/ingest-url", json={"url": "https://example.com"}).json()["id"]

    doc = database.get_document(doc_id)
    assert doc["extracted_date"] is None, "an unknown date must stay NULL in the column"
    assert doc["date_source"] == "assumed"
    assert doc["effective_date"], "but a usable date is still offered"


def test_known_date_is_marked_extracted(client, stored_doc, stub_result):
    doc_id, _, _ = stored_doc
    doc = database.get_document(doc_id)
    assert doc["date_source"] == "extracted"
    assert doc["effective_date"] == stub_result.date


def test_effective_date_is_month_precision_when_assumed(client, monkeypatch, no_date_found):
    """Assumed dates match extracted-date granularity so they sort together."""
    monkeypatch.setattr(
        "ingestion.url_scraper.scrape_url",
        lambda url: ScrapeResult(url, "some text", "A Page", "web", []),
    )
    doc_id = client.post("/api/ingest-url", json={"url": "https://example.com"}).json()["id"]
    effective = database.get_document(doc_id)["effective_date"]
    assert len(effective) == 7 and effective[4] == "-", effective


def test_listings_carry_the_flag_too(client, monkeypatch, no_date_found):
    """A listing is what the timeline will read; the flag must survive it.

    Mutation: resolve dates in get_document only, not in _row_to_dict -> the
    listing loses date_source and the timeline silently re-acquires the bug.
    """
    monkeypatch.setattr(
        "ingestion.url_scraper.scrape_url",
        lambda url: ScrapeResult(url, "some text", "A Page", "web", []),
    )
    client.post("/api/ingest-url", json={"url": "https://example.com"})

    rows = client.get("/api/documents").json()
    assert rows and all("date_source" in r and r["effective_date"] for r in rows)


def test_summary_model_exposes_the_flag():
    """The API contract, not just the dict — the timeline consumes this model."""
    doc = DocumentSummary.model_validate(
        {"id": "x", "filename": "f", "extracted_date": None, "upload_date": "2026-07-19T10:00:00+00:00",
         "effective_date": "2026-07", "date_source": "assumed"}
    )
    assert doc.date_source == "assumed"
    assert doc.effective_date == "2026-07"
