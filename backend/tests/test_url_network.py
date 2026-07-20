"""Real-network tests for the URL ingestion path. Deselected by default.

Run with `pytest -m network`. These cost no API quota — they hit GitHub's
public API and example.com — but they need the internet and will fail offline,
which is why they are opt-in.

They exist for the same reason the `live` Gemini tests do: every other URL test
in this suite stubs `safe_get`, so nothing else would notice if the GitHub API
response shape changed, if the manual redirect loop stopped following hops, or
if `stream=True` + the size cap broke real body reading. That whole layer is
invisible to the offline suite.
"""

from __future__ import annotations

import pytest

from ingestion import url_guard, url_scraper

pytestmark = pytest.mark.network

# A stable, long-lived public repo. If this 404s, the test is wrong, not the code.
REPO_URL = "https://github.com/psf/requests"


def test_github_repo_returns_metadata_and_readme():
    result = url_scraper.scrape_url(REPO_URL)

    assert result.source_type == "github"
    assert result.title == "psf/requests"
    assert result.warnings == []
    assert "Description:" in result.text
    assert "Primary language: Python" in result.text
    # The README is the bulk of the content; metadata alone is a few hundred chars.
    assert "README:" in result.text and len(result.text) > 1000


def test_generic_page_returns_visible_text():
    result = url_scraper.scrape_url("https://example.com")

    assert result.source_type == "web"
    assert result.title == "Example Domain"
    # Deliberately a short, stable substring — example.com has reworded its
    # body copy before, and this test is about extraction, not about wording.
    assert "This domain is for use in documentation" in result.text


def test_real_redirect_chain_is_followed_and_validated():
    """github.com redirects http -> https; each hop is re-validated.

    Exercises the manual redirect loop against a real server, which the stubbed
    tests cannot do.
    """
    resp = url_guard.safe_get("http://github.com/psf/requests")

    assert resp.status_code == 200
    assert resp.url.startswith("https://")
    assert len(resp.content) > 0


def test_scheme_less_input_is_normalized_and_routed():
    """Users paste `github.com/owner/repo` without a scheme."""
    result = url_scraper.scrape_url("github.com/psf/requests")
    assert result.source_type == "github"
    assert result.title == "psf/requests"


def test_language_breakdown_comes_back_from_the_real_endpoint():
    """The /languages shape (a name -> bytes map) is only assumed offline."""
    result = url_scraper.scrape_url(REPO_URL)
    languages = result.details["languages"]

    assert languages, "psf/requests has languages"
    assert languages[0]["name"] == "Python"
    assert 0 < languages[0]["percent"] <= 100
    # Shares are of the truncated top-N, so they need not total 100 — but no
    # single share may exceed it, which is what a bad divisor would produce.
    assert all(0 < entry["percent"] <= 100 for entry in languages)


def test_repo_details_come_back_from_the_real_api():
    result = url_scraper.scrape_url(REPO_URL)
    details = result.details

    assert details["kind"] == "repo"
    assert details["created"] == "2011-02"
    assert isinstance(details["stars"], int) and details["stars"] > 1000
    assert details["license"] == "Apache-2.0"


def test_real_profile_url_routes_to_the_profile_scraper():
    """A bare github.com/<user> used to fall through to the HTML scraper.

    `torvalds` is chosen for longevity, not relevance — the test needs an
    account that will still be a personal (non-org) profile years from now.
    """
    result = url_scraper.scrape_url("https://github.com/torvalds")

    assert result.source_type == "github"
    assert result.details["kind"] == "profile"
    assert result.details["login"] == "torvalds"
    assert result.details["repos"], "a profile should list public repos"
    assert result.source_date, "the account creation date is the profile's date"


def test_github_own_route_is_not_mistaken_for_a_profile():
    """github.com/pricing is a page. It must reach the web scraper."""
    result = url_scraper.scrape_url("https://github.com/pricing")

    assert result.source_type == "web"
    assert result.text.strip(), "the HTML scraper should get real page text"


def test_nonexistent_repo_degrades_to_warnings_not_an_exception():
    """plan.md §10: a failed scrape must not raise."""
    result = url_scraper.scrape_url(
        "https://github.com/psf/this-repo-does-not-exist-9f3a2b"
    )
    assert result.warnings, "a 404 repo should surface a warning"
    assert result.text == ""
