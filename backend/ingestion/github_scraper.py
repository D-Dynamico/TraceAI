"""GitHub repository metadata extraction.

Uses the public REST API unauthenticated (60 req/hr — plan.md §10 notes a free
token raises this to 5000/hr if it ever becomes a constraint). Fetches the repo
description, primary language, topics, and README.

Every network failure degrades into a warning rather than an exception: a repo
whose README 404s should still be ingested with whatever metadata we did get.
"""

from __future__ import annotations

import logging
import re

import requests

from ingestion import url_guard
from ingestion.scrape_result import REQUEST_TIMEOUT, ScrapeResult, headers

logger = logging.getLogger(__name__)

# Matches /owner/repo — the repo root only. Deeper paths (/owner/repo/issues)
# fall through to the generic web scraper, which is the better handler for them.
REPO_PATH_RE = re.compile(r"^/([^/]+)/([^/]+)/?$")

API_ROOT = "https://api.github.com/repos"
RAW_ROOT = "https://raw.githubusercontent.com"
README_BRANCHES = ("main", "master")


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


def scrape(owner: str, repo: str, url: str) -> ScrapeResult:
    """Fetch repo metadata + README for `owner/repo`."""
    repo = repo.removesuffix(".git")
    warnings: list[str] = []
    parts: list[str] = []
    title = f"{owner}/{repo}"
    source_date: str | None = None

    api = f"{API_ROOT}/{owner}/{repo}"
    try:
        meta = url_guard.safe_get(api, headers=headers(), timeout=REQUEST_TIMEOUT)
        if meta.ok:
            data = meta.json()
            title = data.get("full_name") or title
            if data.get("description"):
                parts.append(f"Description: {data['description']}")
            if data.get("language"):
                parts.append(f"Primary language: {data['language']}")
            topics = data.get("topics") or []
            if topics:
                parts.append(f"Topics: {', '.join(topics)}")
            # Free — it is in the response we already made. Without it the repo
            # has no date and the timeline invents one.
            source_date = month_from_iso(data.get("created_at"))
            if source_date:
                parts.append(f"Repository created: {source_date}")
        else:
            warnings.append(f"GitHub API returned {meta.status_code} for {api}")
    except (requests.RequestException, url_guard.BlockedUrlError, ValueError) as exc:
        warnings.append(f"GitHub API request failed: {exc}")
        logger.warning("GitHub API failed for %s: %s", url, exc)

    readme = _fetch_readme(owner, repo)
    if readme:
        parts.append("README:\n" + readme)

    if not parts:
        warnings.append("No repo metadata or README could be retrieved.")

    return ScrapeResult(
        url, "\n\n".join(parts).strip(), title, "github", warnings, source_date
    )


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
