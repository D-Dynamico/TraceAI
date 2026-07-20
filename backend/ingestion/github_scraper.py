"""GitHub repository and profile metadata extraction.

Uses the public REST API unauthenticated (60 req/hr — plan.md §10 notes a free
token raises this to 5000/hr if it ever becomes a constraint). Budget per
ingest:

  repo     3-4 requests  (repo + languages + README on main, then master)
  profile  2 requests    (user + one page of repos)

`PyGithub` is listed in plan.md §2 but deliberately not used: it issues its own
HTTP and would bypass `url_guard`, making "every outbound fetch goes through
safe_get" no longer true by inspection. The only thing it offered that raw
requests does not is pagination, and one `per_page=100` page plus the
`public_repos` count covers what a card displays.

Every network failure degrades into a warning rather than an exception: a repo
whose README 404s should still be ingested with whatever metadata we did get.

Two fields here are attacker-controlled free text rather than GitHub-generated:
a repo's `homepage` and a profile's `blog`. Both are rendered as links by the
UI, so both go through `url_guard.safe_display_url` before leaving this module.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import requests

from ingestion import url_guard
from ingestion.scrape_result import REQUEST_TIMEOUT, ScrapeResult, headers

logger = logging.getLogger(__name__)

# Matches /owner/repo — the repo root only. Deeper paths (/owner/repo/issues)
# fall through to the generic web scraper, which is the better handler for them.
REPO_PATH_RE = re.compile(r"^/([^/]+)/([^/]+)/?$")

# Matches /login — a bare profile. GitHub usernames are alphanumeric with
# single hyphens, 1-39 chars; anchoring to that keeps `/favicon.ico` and
# `/foo.php` out without needing them in the denylist below.
PROFILE_PATH_RE = re.compile(r"^/([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)/?$")

# First-path-segment routes github.com owns. Without this every one of them
# looks exactly like a username to the regex above, and pasting
# `github.com/pricing` would spend an API call asking for a user named
# "pricing", get a 404, and produce an empty document — where falling through
# to the web scraper actually returns the page's text.
#
# Not exhaustive and cannot be: GitHub adds routes without telling anyone. The
# 404-degrades-to-web-scraper fallback in `scrape_profile` is what makes an
# omission here a missed opportunity rather than a broken ingest.
RESERVED_PATHS = frozenset(
    {
        "about", "account", "admin", "apps", "blog", "business", "careers",
        "collections", "contact", "customer-stories", "dashboard", "developer",
        "discussions", "enterprise", "events", "explore", "features", "gist",
        "git-lfs", "github", "home", "issues", "join", "login", "logout",
        "marketplace", "mobile", "new", "news", "notifications", "orgs",
        "organizations", "personal", "premium", "pricing", "pulls", "readme",
        "search", "security", "sessions", "settings", "showcases", "signup",
        "site", "sponsors", "stars", "status", "team", "topics", "trending",
        "watching", "wiki",
    }
)

API_ROOT = "https://api.github.com"
RAW_ROOT = "https://raw.githubusercontent.com"
README_BRANCHES = ("main", "master")

# How many repos to describe. The text limit feeds Gemini (more repos means
# better skill extraction, up to the point of drowning the bio); the display
# limit is what the card lists before it becomes a wall.
PROFILE_REPOS_IN_TEXT = 30
PROFILE_REPOS_IN_DETAILS = 10
# Languages past this are rounding error and would only crowd the card.
MAX_LANGUAGES = 6


def month_from_iso(value: str | None) -> str | None:
    """"2011-02-13T18:38:17Z" -> "2011-02". None for anything unparseable.

    Trimmed to the month because that is the granularity the rest of the system
    stores (`Categorization.date` accepts "YYYY" or "YYYY-MM"), and a
    day-precision repo creation date implies more than it knows about when the
    work actually happened.
    """
    if not isinstance(value, str) or len(value) < 7:
        return None
    year, _, month = value[:4], value[4:5], value[5:7]
    if not (year.isdigit() and month.isdigit()):
        return None
    if not 1 <= int(month) <= 12:
        return None
    return f"{year}-{month}"


def _clean(value: object) -> str:
    """Free-text API fields, trimmed. '' for anything not a string.

    GitHub returns bios with trailing CRLF (observed on a real account), which
    would otherwise reach the card as a stray blank line.
    """
    return value.strip() if isinstance(value, str) else ""


def _get_json(url: str, warnings: list[str], label: str) -> Any | None:
    """GET JSON through the SSRF guard, degrading to a warning on any failure.

    Returns None when anything went wrong, having appended a warning. Callers
    are expected to continue with whatever else they retrieved.
    """
    try:
        response = url_guard.safe_get(url, headers=headers(), timeout=REQUEST_TIMEOUT)
    except (requests.RequestException, url_guard.BlockedUrlError, ValueError) as exc:
        warnings.append(f"GitHub {label} request failed: {exc}")
        logger.warning("GitHub %s failed for %s: %s", label, url, exc)
        return None

    if not response.ok:
        warnings.append(f"GitHub API returned {response.status_code} for {url}")
        return None

    try:
        return response.json()
    except ValueError as exc:
        warnings.append(f"GitHub {label} returned unreadable JSON.")
        logger.warning("Bad JSON from %s: %s", url, exc)
        return None


def _language_shares(raw: dict[str, Any] | None) -> list[dict[str, Any]]:
    """`{"Python": 45000, "Makefile": 300}` -> ranked percentage shares.

    The /languages endpoint reports bytes of source, not files or lines. That
    is the number plan.md §4 asks to surface, and it is worth knowing it
    overstates verbose languages — an HTML-heavy repo reads as an HTML project.
    """
    if not isinstance(raw, dict):
        return []
    sizes = {
        name: value
        for name, value in raw.items()
        if isinstance(name, str) and isinstance(value, int) and value > 0
    }
    total = sum(sizes.values())
    if total <= 0:
        return []
    ranked = sorted(sizes.items(), key=lambda item: item[1], reverse=True)
    return [
        {"name": name, "percent": round(size * 100 / total, 1)}
        for name, size in ranked[:MAX_LANGUAGES]
    ]


def scrape(owner: str, repo: str, url: str) -> ScrapeResult:
    """Fetch repo metadata + README for `owner/repo`."""
    repo = repo.removesuffix(".git")
    warnings: list[str] = []
    parts: list[str] = []
    title = f"{owner}/{repo}"
    source_date: str | None = None

    # Every key of the "repo" shape is present from the start, empty. The API
    # call below fails routinely — a 404 repo, or a 403 once the 60 req/hr
    # unauthenticated budget runs out mid-session — and a `details` whose keys
    # depend on whether the network cooperated forces the card to guard each
    # field separately. `kind` promises a shape; this keeps that promise.
    details: dict[str, Any] = {
        "kind": "repo",
        "owner": owner,
        "repo": repo,
        "full_name": f"{owner}/{repo}",
        "description": "",
        "stars": None,
        "forks": None,
        "open_issues": None,
        "license": None,
        "created": None,
        "pushed": None,
        "topics": [],
        "homepage": None,
        "archived": False,
        "languages": [],
    }

    api = f"{API_ROOT}/repos/{owner}/{repo}"
    data = _get_json(api, warnings, "repo")
    if isinstance(data, dict):
        title = data.get("full_name") or title
        topics = [t for t in (data.get("topics") or []) if isinstance(t, str)]

        # `parts` is raw_text — the Gemini input and the future embedding
        # source. It is intentionally unchanged from before this commit: the
        # new fields below are *display* data and go to `details`, not here.
        # Star counts do not improve categorization, and rewriting the text
        # that feeds classification is a change to make on its own evidence.
        if data.get("description"):
            parts.append(f"Description: {data['description']}")
        if data.get("language"):
            parts.append(f"Primary language: {data['language']}")
        if topics:
            parts.append(f"Topics: {', '.join(topics)}")
        # Free — it is in the response we already made. Without it the repo
        # has no date and the timeline invents one.
        source_date = month_from_iso(data.get("created_at"))
        if source_date:
            parts.append(f"Repository created: {source_date}")

        license_info = data.get("license")
        details.update(
            {
                "full_name": title,
                "description": _clean(data.get("description")),
                "stars": data.get("stargazers_count"),
                "forks": data.get("forks_count"),
                "open_issues": data.get("open_issues_count"),
                "license": (license_info or {}).get("spdx_id")
                if isinstance(license_info, dict)
                else None,
                "created": source_date,
                "pushed": month_from_iso(data.get("pushed_at")),
                "topics": topics,
                # Owner-controlled free text, rendered as a link by the card.
                "homepage": url_guard.safe_display_url(data.get("homepage")),
                "archived": bool(data.get("archived")),
            }
        )

        languages = _get_json(f"{api}/languages", warnings, "languages")
        details["languages"] = _language_shares(languages)

    readme = _fetch_readme(owner, repo)
    if readme:
        parts.append("README:\n" + readme)

    if not parts:
        warnings.append("No repo metadata or README could be retrieved.")

    return ScrapeResult(
        url, "\n\n".join(parts).strip(), title, "github", warnings, source_date, details
    )


def scrape_profile(login: str, url: str) -> ScrapeResult | None:
    """Fetch a user's profile and public repos. None if there is no such user.

    Returning None rather than an empty result is what lets the router fall
    back to the generic web scraper: a path that looked like a username but
    404s is far more likely to be a GitHub route we do not know about than a
    genuinely missing user, and the HTML page for such a route has real content.

    plan.md §4 does not describe profile URLs — this treats one profile as one
    document, the same contract as every other input (one paste, one doc),
    rather than fanning out into a document per repo.
    """
    warnings: list[str] = []

    user = _get_json(f"{API_ROOT}/users/{login}", warnings, "user")
    if not isinstance(user, dict) or not user.get("login"):
        return None
    # An organization is a different kind of thing from a person's profile and
    # its "repos" are not the owner's portfolio. Let the web scraper have it.
    if user.get("type") and user["type"] != "User":
        return None

    login = _clean(user.get("login")) or login
    display_name = _clean(user.get("name")) or login
    bio = _clean(user.get("bio"))
    company = _clean(user.get("company"))
    location = _clean(user.get("location"))
    created = month_from_iso(user.get("created_at"))

    # One page of 100, newest activity first. A profile card shows the top few
    # and the true total comes from `public_repos`, so paginating further would
    # cost requests to display nothing extra.
    repos = _get_json(
        f"{API_ROOT}/users/{login}/repos?per_page=100&sort=updated",
        warnings,
        "user repos",
    )
    repo_list: list[dict[str, Any]] = []
    if isinstance(repos, list):
        for entry in repos:
            if not isinstance(entry, dict) or entry.get("fork"):
                continue  # forks are not the user's own work
            repo_list.append(
                {
                    "name": _clean(entry.get("name")),
                    "description": _clean(entry.get("description")),
                    "language": entry.get("language"),
                    "stars": entry.get("stargazers_count") or 0,
                    "url": url_guard.safe_display_url(entry.get("html_url")),
                    "updated": month_from_iso(entry.get("pushed_at")),
                }
            )

    # Most-starred first for display: it is the closest thing to "which of
    # these is the portfolio piece", where `sort=updated` only says which was
    # touched last.
    by_stars = sorted(repo_list, key=lambda r: r["stars"], reverse=True)

    parts: list[str] = [f"GitHub profile: {display_name} ({login})"]
    if bio:
        parts.append(f"Bio: {bio}")
    if company:
        parts.append(f"Company: {company}")
    if location:
        parts.append(f"Location: {location}")
    if created:
        parts.append(f"Joined GitHub: {created}")
    parts.append(f"Public repositories: {user.get('public_repos', len(repo_list))}")

    if by_stars:
        lines = []
        for entry in by_stars[:PROFILE_REPOS_IN_TEXT]:
            line = entry["name"]
            if entry["language"]:
                line += f" [{entry['language']}]"
            if entry["description"]:
                line += f" — {entry['description']}"
            lines.append(line)
        parts.append("Repositories:\n" + "\n".join(lines))
    else:
        warnings.append("This profile has no public non-forked repositories.")

    details: dict[str, Any] = {
        "kind": "profile",
        "login": login,
        "name": display_name,
        "bio": bio,
        "company": company,
        "location": location,
        # The true total, which `repos` below is capped below. The card needs
        # both to say "10 of 15" rather than implying the user has only 10.
        "public_repos": user.get("public_repos"),
        "followers": user.get("followers"),
        "created": created,
        # Owner-controlled free text, rendered as a link by the card.
        "homepage": url_guard.safe_display_url(user.get("blog")),
        "avatar": url_guard.safe_display_url(user.get("avatar_url")),
        "repos": by_stars[:PROFILE_REPOS_IN_DETAILS],
        "languages": _profile_languages(repo_list),
    }

    return ScrapeResult(
        url,
        "\n\n".join(parts).strip(),
        display_name,
        "github",
        warnings,
        created,
        details,
    )


def _profile_languages(repos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank languages by how many of the user's repos use each as the primary.

    Repo counts, not bytes — the per-repo /languages call that gives byte
    shares would cost one request per repo, which blows the 60/hr budget on a
    single profile.
    """
    counts: dict[str, int] = {}
    for repo in repos:
        language = repo.get("language")
        if isinstance(language, str) and language:
            counts[language] = counts.get(language, 0) + 1
    total = sum(counts.values())
    if total <= 0:
        return []
    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    return [
        {"name": name, "count": count, "percent": round(count * 100 / total, 1)}
        for name, count in ranked[:MAX_LANGUAGES]
    ]


def _fetch_readme(owner: str, repo: str) -> str:
    """Try the default-branch README, main before master. '' if neither exists."""
    for branch in README_BRANCHES:
        raw = f"{RAW_ROOT}/{owner}/{repo}/{branch}/README.md"
        try:
            resp = url_guard.safe_get(raw, headers=headers(), timeout=REQUEST_TIMEOUT)
            if resp.ok and resp.text.strip():
                return resp.text.strip()
        except (requests.RequestException, url_guard.BlockedUrlError) as exc:
            logger.debug("README fetch failed (%s): %s", raw, exc)
    return ""
