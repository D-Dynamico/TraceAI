"""GitHub repo enrichment, profile ingestion, and routing.

Covers the three things that changed when repos and profiles started carrying
structured `details`:

  - repo scrapes keep fields the API response already contained (stars,
    license, languages) without altering `raw_text`,
  - `github.com/<login>` reaches the user API instead of the HTML scraper,
    while github.com's own routes still do not,
  - `details` and `date_source` reach the client.

Everything here stubs `url_guard.safe_get`; `test_url_network.py` is the only
cover for the real API.
"""

from __future__ import annotations

import json

import pytest

from ingestion import github_scraper, url_guard, url_scraper
from ingestion.scrape_result import ScrapeResult

REPO_JSON = {
    "full_name": "psf/requests",
    "description": "A simple, yet elegant, HTTP library.",
    "language": "Python",
    "topics": ["http", "python"],
    "created_at": "2011-02-13T18:38:17Z",
    "pushed_at": "2026-07-09T12:00:00Z",
    "stargazers_count": 54135,
    "forks_count": 10018,
    "open_issues_count": 220,
    "license": {"spdx_id": "Apache-2.0"},
    "homepage": "https://requests.readthedocs.io",
    "archived": False,
}

LANGUAGES_JSON = {"Python": 497000, "Makefile": 3000}

USER_JSON = {
    "login": "D-Dynamico",
    "type": "User",
    "name": "Dayanand",
    "bio": "Student building things.",
    "company": "@acme",
    "location": "India",
    "public_repos": 12,
    "followers": 7,
    "created_at": "2021-05-02T09:00:00Z",
    "blog": "https://dayanand.example",
    "avatar_url": "https://avatars.githubusercontent.com/u/1?v=4",
}

USER_REPOS_JSON = [
    {
        "name": "traceai",
        "description": "Digital identity system.",
        "language": "Python",
        "stargazers_count": 9,
        "html_url": "https://github.com/D-Dynamico/traceai",
        "pushed_at": "2026-07-01T00:00:00Z",
        "fork": False,
    },
    {
        "name": "dotfiles",
        "description": "",
        "language": "Shell",
        "stargazers_count": 1,
        "html_url": "https://github.com/D-Dynamico/dotfiles",
        "pushed_at": "2025-02-01T00:00:00Z",
        "fork": False,
    },
    {
        "name": "somebody-elses-repo",
        "description": "Not my work.",
        "language": "Rust",
        "stargazers_count": 900,
        "html_url": "https://github.com/D-Dynamico/somebody-elses-repo",
        "pushed_at": "2026-01-01T00:00:00Z",
        "fork": True,
    },
]


def _json_response(url: str, payload) -> url_guard.SafeResponse:
    return url_guard.SafeResponse(
        url=url, status_code=200, content=json.dumps(payload).encode()
    )


@pytest.fixture
def fake_github(monkeypatch):
    """Serve repo, languages, user, and user-repos JSON. No README, no network."""

    def _get(url, **kwargs):
        if url.endswith("/languages"):
            return _json_response(url, LANGUAGES_JSON)
        if "/users/D-Dynamico/repos" in url:
            return _json_response(url, USER_REPOS_JSON)
        if url.endswith("/users/D-Dynamico"):
            return _json_response(url, USER_JSON)
        if "api.github.com/repos/" in url:
            return _json_response(url, REPO_JSON)
        # Any user we did not stub does not exist, and no README exists.
        return url_guard.SafeResponse(url=url, status_code=404, content=b"")

    monkeypatch.setattr(github_scraper.url_guard, "safe_get", _get)


# --- Repo enrichment ---------------------------------------------------------


def test_repo_details_carry_fields_the_api_already_returned(fake_github):
    result = github_scraper.scrape("psf", "requests", "https://github.com/psf/requests")
    details = result.details

    assert details["kind"] == "repo"
    assert details["stars"] == 54135
    assert details["forks"] == 10018
    assert details["license"] == "Apache-2.0"
    assert details["created"] == "2011-02"
    assert details["pushed"] == "2026-07"
    assert details["homepage"] == "https://requests.readthedocs.io"


def test_language_breakdown_is_ranked_percentages(fake_github):
    result = github_scraper.scrape("psf", "requests", "https://github.com/psf/requests")

    assert result.details["languages"] == [
        {"name": "Python", "percent": 99.4},
        {"name": "Makefile", "percent": 0.6},
    ]


def test_enrichment_does_not_alter_raw_text(fake_github):
    """`text` is the Gemini input and the future embedding source.

    The new fields are display data and belong in `details`. If a star count
    ever shows up in the categorization input, it should be because someone
    decided that on its own evidence — not as a side effect of a card redesign.

    Mutation: append f"Stars: {stars}" to `parts` -> this fails.
    """
    result = github_scraper.scrape("psf", "requests", "https://github.com/psf/requests")

    assert result.text == (
        "Description: A simple, yet elegant, HTTP library.\n\n"
        "Primary language: Python\n\n"
        "Topics: http, python\n\n"
        "Repository created: 2011-02"
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ({}, []),
        (None, []),
        ({"Python": 0}, []),                    # zero bytes -> no total
        ({"Python": "lots"}, []),               # non-int value
        ({"Python": 1, "Go": 1}, [             # exact tie, still ranked
            {"name": "Python", "percent": 50.0},
            {"name": "Go", "percent": 50.0},
        ]),
    ],
)
def test_language_shares_degrade_on_bad_input(raw, expected):
    assert github_scraper._language_shares(raw) == expected


def test_language_shares_are_capped(fake_github):
    many = {f"Lang{i}": 100 - i for i in range(20)}
    assert len(github_scraper._language_shares(many)) == github_scraper.MAX_LANGUAGES


def test_repo_details_shape_is_the_same_when_the_api_fails(monkeypatch):
    """`kind` promises a shape; a failed fetch must not change which keys exist.

    This path is routine, not exotic: 60 req/hr unauthenticated means a busy
    session starts getting 403s, and a 404 repo hits it too. If the keys come
    and go, the card has to guard every field separately and the unhappy path
    is the one nobody renders during development.

    Mutation: build `details` incrementally inside the `if isinstance(data,
    dict)` block -> this fails on every key.
    """

    def _get(url, **kwargs):
        return url_guard.SafeResponse(url=url, status_code=403, content=b"")

    monkeypatch.setattr(github_scraper.url_guard, "safe_get", _get)
    failed = github_scraper.scrape("a", "b", "https://github.com/a/b")

    assert failed.details["kind"] == "repo"
    assert failed.details["languages"] == []
    assert failed.details["topics"] == []
    assert failed.details["stars"] is None
    assert failed.warnings, "the failure is still surfaced to the user"


def test_repo_details_keys_match_between_success_and_failure(monkeypatch, fake_github):
    """The two shapes must be key-for-key identical."""
    ok = github_scraper.scrape("psf", "requests", "https://github.com/psf/requests")

    def _get(url, **kwargs):
        return url_guard.SafeResponse(url=url, status_code=404, content=b"")

    monkeypatch.setattr(github_scraper.url_guard, "safe_get", _get)
    failed = github_scraper.scrape("psf", "requests", "https://github.com/psf/requests")

    assert set(ok.details) == set(failed.details)


def test_repo_scrape_survives_a_languages_failure(monkeypatch):
    """A repo whose /languages call 500s should still ingest."""

    def _get(url, **kwargs):
        if url.endswith("/languages"):
            return url_guard.SafeResponse(url=url, status_code=500, content=b"")
        if "api.github.com/repos/" in url:
            return _json_response(url, REPO_JSON)
        return url_guard.SafeResponse(url=url, status_code=404, content=b"")

    monkeypatch.setattr(github_scraper.url_guard, "safe_get", _get)
    result = github_scraper.scrape("psf", "requests", "https://github.com/psf/requests")

    assert result.details["stars"] == 54135
    assert result.details["languages"] == []
    assert any("500" in w for w in result.warnings)


# --- The homepage link guard (security) --------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "javascript:alert(1)",
        "JaVaScript:alert(document.domain)",
        "java\tscript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "//evil.example/path",
    ],
)
def test_hostile_homepage_is_not_passed_to_the_card(monkeypatch, hostile):
    """A repo's `homepage` is free text its owner controls, and the card renders
    it as a link. A `javascript:` value there is stored XSS against anyone who
    ingests the repo — React does not block such an href, it only warns.

    Mutation: return `candidate` unconditionally from safe_display_url ->
    every parameter case here fails.
    """

    def _get(url, **kwargs):
        if "api.github.com/repos/" in url:
            return _json_response(url, {**REPO_JSON, "homepage": hostile})
        return url_guard.SafeResponse(url=url, status_code=404, content=b"")

    monkeypatch.setattr(github_scraper.url_guard, "safe_get", _get)
    result = github_scraper.scrape("a", "b", "https://github.com/a/b")

    assert result.details["homepage"] is None


def test_hostile_profile_blog_is_not_passed_to_the_card(monkeypatch):
    """Same field, same risk, on the profile path."""

    def _get(url, **kwargs):
        if url.endswith("/users/D-Dynamico"):
            return _json_response(url, {**USER_JSON, "blog": "javascript:alert(1)"})
        if "/users/D-Dynamico/repos" in url:
            return _json_response(url, [])
        return url_guard.SafeResponse(url=url, status_code=404, content=b"")

    monkeypatch.setattr(github_scraper.url_guard, "safe_get", _get)
    result = github_scraper.scrape_profile("D-Dynamico", "https://github.com/D-Dynamico")

    assert result.details["homepage"] is None


def test_legitimate_homepage_survives(fake_github):
    """The guard must not be so broad it drops the field it exists to carry."""
    result = github_scraper.scrape("psf", "requests", "https://github.com/psf/requests")
    assert result.details["homepage"] == "https://requests.readthedocs.io"


# --- Profile scraping --------------------------------------------------------


def test_profile_scrape_returns_bio_and_repos(fake_github):
    result = github_scraper.scrape_profile(
        "D-Dynamico", "https://github.com/D-Dynamico"
    )

    assert result.source_type == "github"
    assert result.title == "Dayanand"
    assert result.source_date == "2021-05"
    assert result.details["kind"] == "profile"
    assert result.details["public_repos"] == 12
    assert "Bio: Student building things." in result.text


def test_profile_excludes_forks(fake_github):
    """A fork is somebody else's work sitting in your account."""
    result = github_scraper.scrape_profile(
        "D-Dynamico", "https://github.com/D-Dynamico"
    )

    names = [r["name"] for r in result.details["repos"]]
    assert names == ["traceai", "dotfiles"]
    assert "somebody-elses-repo" not in result.text


def test_profile_repos_are_ranked_by_stars(fake_github):
    result = github_scraper.scrape_profile(
        "D-Dynamico", "https://github.com/D-Dynamico"
    )
    stars = [r["stars"] for r in result.details["repos"]]
    assert stars == sorted(stars, reverse=True)


def test_profile_languages_count_repos(fake_github):
    result = github_scraper.scrape_profile(
        "D-Dynamico", "https://github.com/D-Dynamico"
    )
    assert result.details["languages"] == [
        {"name": "Python", "count": 1, "percent": 50.0},
        {"name": "Shell", "count": 1, "percent": 50.0},
    ]


def test_profile_text_fields_are_trimmed(monkeypatch):
    """GitHub returns bios with a trailing CRLF — observed on a real account."""

    def _get(url, **kwargs):
        if url.endswith("/users/D-Dynamico"):
            return _json_response(url, {**USER_JSON, "bio": "Builds things.\r\n"})
        if "/users/D-Dynamico/repos" in url:
            return _json_response(url, [])
        return url_guard.SafeResponse(url=url, status_code=404, content=b"")

    monkeypatch.setattr(github_scraper.url_guard, "safe_get", _get)
    result = github_scraper.scrape_profile("D-Dynamico", "https://github.com/D-Dynamico")

    assert result.details["bio"] == "Builds things."
    assert "\r" not in result.text


def test_profile_reports_the_true_repo_total(fake_github):
    """`repos` is capped for display; `public_repos` is not.

    Without the real total a card listing 10 of someone's 15 repos silently
    implies they have 10.
    """
    result = github_scraper.scrape_profile(
        "D-Dynamico", "https://github.com/D-Dynamico"
    )
    assert result.details["public_repos"] == 12
    assert len(result.details["repos"]) <= github_scraper.PROFILE_REPOS_IN_DETAILS


def test_unknown_user_returns_none(fake_github):
    """None is the signal that lets the router fall back to the web scraper."""
    assert github_scraper.scrape_profile("nobody", "https://github.com/nobody") is None


def test_organizations_are_not_treated_as_profiles(monkeypatch):
    """An org's repos are not one person's portfolio."""

    def _get(url, **kwargs):
        if url.endswith("/users/psf"):
            return _json_response(url, {**USER_JSON, "login": "psf", "type": "Organization"})
        return url_guard.SafeResponse(url=url, status_code=404, content=b"")

    monkeypatch.setattr(github_scraper.url_guard, "safe_get", _get)
    assert github_scraper.scrape_profile("psf", "https://github.com/psf") is None


# --- Routing -----------------------------------------------------------------


@pytest.fixture
def spy_router(monkeypatch):
    """Record which scraper a URL reaches, without any of them running."""
    calls = {}

    monkeypatch.setattr(url_guard, "validate_url", lambda url: url)
    monkeypatch.setattr(
        github_scraper,
        "scrape",
        lambda owner, repo, url: calls.setdefault("repo", (owner, repo))
        or ScrapeResult(url, "t", "t", "github"),
    )
    monkeypatch.setattr(
        github_scraper,
        "scrape_profile",
        lambda login, url: calls.setdefault("profile", login)
        or ScrapeResult(url, "t", "t", "github"),
    )
    monkeypatch.setattr(
        url_scraper.web_scraper,
        "scrape",
        lambda url: calls.setdefault("web", url) or ScrapeResult(url, "t", "t", "web"),
    )
    return calls


def test_repo_url_routes_to_the_repo_scraper(spy_router):
    url_scraper.scrape_url("https://github.com/psf/requests")
    assert spy_router == {"repo": ("psf", "requests")}


def test_profile_url_routes_to_the_profile_scraper(spy_router):
    url_scraper.scrape_url("https://github.com/D-Dynamico")
    assert spy_router == {"profile": "D-Dynamico"}


def test_trailing_slash_profile_still_routes(spy_router):
    url_scraper.scrape_url("https://github.com/D-Dynamico/")
    assert spy_router == {"profile": "D-Dynamico"}


@pytest.mark.parametrize(
    "path",
    ["pricing", "features", "explore", "topics", "settings", "login", "orgs",
     "sponsors", "marketplace", "about", "security", "PRICING"],
)
def test_github_own_routes_are_not_treated_as_usernames(spy_router, path):
    """`github.com/pricing` is a page, not a user.

    Without the denylist each of these spends an API call on a user that does
    not exist and produces an empty document, where the web scraper returns the
    page's actual text.

    Mutation: empty RESERVED_PATHS -> these route to the profile scraper.
    """
    url_scraper.scrape_url(f"https://github.com/{path}")
    assert "profile" not in spy_router
    assert "web" in spy_router


def test_deeper_github_paths_still_go_to_the_web_scraper(spy_router):
    url_scraper.scrape_url("https://github.com/psf/requests/issues/123")
    assert spy_router == {"web": "https://github.com/psf/requests/issues/123"}


def test_a_profile_404_falls_through_to_the_web_scraper(monkeypatch):
    """A single segment that is not a user is likely a route we do not know.

    The HTML page for such a route has real content, so an empty profile
    document would be strictly worse than scraping it.
    """
    monkeypatch.setattr(url_guard, "validate_url", lambda url: url)
    monkeypatch.setattr(github_scraper, "scrape_profile", lambda login, url: None)
    monkeypatch.setattr(
        url_scraper.web_scraper,
        "scrape",
        lambda url: ScrapeResult(url, "page text", "A Page", "web"),
    )

    result = url_scraper.scrape_url("https://github.com/some-new-route")
    assert result.source_type == "web"
    assert result.text == "page text"


# --- The response envelope ---------------------------------------------------


def test_details_reach_the_client_and_the_database(client, monkeypatch):
    from db import database

    details = {"kind": "repo", "stars": 42, "languages": [{"name": "Python", "percent": 100.0}]}
    monkeypatch.setattr(
        "ingestion.url_scraper.scrape_url",
        lambda url: ScrapeResult(url, "text", "a/b", "github", [], "2011-02", details),
    )

    body = client.post("/api/ingest-url", json={"url": "https://github.com/a/b"}).json()
    assert body["details"] == details

    stored = database.get_document(body["id"])
    assert stored["metadata"]["details"] == details


def test_generic_web_page_has_empty_details(client, monkeypatch):
    monkeypatch.setattr(
        "ingestion.url_scraper.scrape_url",
        lambda url: ScrapeResult(url, "text", "A Page", "web"),
    )
    body = client.post("/api/ingest-url", json={"url": "https://example.com"}).json()
    assert body["details"] == {}
