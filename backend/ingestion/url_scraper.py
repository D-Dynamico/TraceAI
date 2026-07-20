"""URL type detection and routing (plan.md §4 Module 1).

This module decides *which* scraper handles a URL. The scrapers themselves live
in `github_scraper` and `web_scraper`; the destination safety checks live in
`url_guard`. Keeping routing separate means adding a handler (Coursera, Medium)
is a change to the table below rather than to fetching logic.

  github.com/owner/repo  -> github_scraper  (API + README)
  github.com/login       -> github_scraper  (profile + public repos)
  anything else          -> web_scraper     (HTML -> visible text)
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from ingestion import github_scraper, url_guard, web_scraper
from ingestion.scrape_result import ScrapeResult

logger = logging.getLogger(__name__)

# Re-exported so `from ingestion.url_scraper import ScrapeResult` keeps working
# for callers that predate the split.
__all__ = ["ScrapeResult", "scrape_url"]

GITHUB_HOSTS = ("github.com", "www.github.com")


def scrape_url(url: str) -> ScrapeResult:
    """Fetch a URL and return readable text, dispatching by URL type.

    Raises `url_guard.BlockedUrlError` (a ValueError) for an unsupported scheme
    or a destination that is not publicly routable.
    """
    url = url_guard.normalize_url(url)
    # Validate before dispatching, so an unsafe URL is rejected even on a code
    # path that would otherwise not fetch it.
    url_guard.validate_url(url)

    parsed = urlparse(url)
    if parsed.netloc.lower() in GITHUB_HOSTS:
        repo = github_scraper.REPO_PATH_RE.match(parsed.path)
        if repo:
            return github_scraper.scrape(repo.group(1), repo.group(2), url)

        profile = github_scraper.PROFILE_PATH_RE.match(parsed.path)
        # A single path segment is *shaped* like a username but is far more
        # often one of github.com's own routes (/pricing, /explore). Those are
        # excluded by name; for anything else that merely looks like a user,
        # `scrape_profile` returns None when the API says no such user, and we
        # fall through to the web scraper — which handles an unknown
        # github.com route better than an empty profile document would.
        if profile and profile.group(1).lower() not in github_scraper.RESERVED_PATHS:
            result = github_scraper.scrape_profile(profile.group(1), url)
            if result is not None:
                return result

    return web_scraper.scrape(url)
