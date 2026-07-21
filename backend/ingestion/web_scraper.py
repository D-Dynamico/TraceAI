"""Generic webpage scraping — portfolios, blogs, certificate verification pages.

Strips chrome (script/style/nav/header/footer) and returns the visible text.
Per plan.md § Risk Mitigation, a site that blocks us degrades to a warning so the user can be
told to upload the content manually instead.
"""

from __future__ import annotations

import logging

import requests
from bs4 import BeautifulSoup

from ingestion import url_guard
from ingestion.scrape_result import REQUEST_TIMEOUT, ScrapeResult, headers

logger = logging.getLogger(__name__)

# Tags whose text is navigation or styling, never document content.
CHROME_TAGS = ("script", "style", "noscript", "header", "footer", "nav")


def scrape(url: str) -> ScrapeResult:
    """Fetch a page and return its visible text."""
    try:
        resp = url_guard.safe_get(url, headers=headers(), timeout=REQUEST_TIMEOUT)
    except url_guard.BlockedUrlError:
        # A blocked destination is a caller error, not a flaky site — let it
        # propagate so the route can answer 400 rather than reporting a
        # successful scrape of nothing.
        raise
    except requests.RequestException as exc:
        logger.warning("Web fetch failed for %s: %s", url, exc)
        return ScrapeResult(url, "", "", "web", [f"Fetch failed: {exc}"])

    if not resp.ok:
        return ScrapeResult(
            url, "", "", "web", [f"Fetch failed: HTTP {resp.status_code}"]
        )

    warnings: list[str] = []
    # Hand BeautifulSoup the raw bytes — its encoding detection reads the
    # <meta charset> declaration, which a Content-Type header can contradict.
    soup = BeautifulSoup(resp.content, "html.parser")
    for tag in soup(CHROME_TAGS):
        tag.decompose()

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln)

    if not text:
        warnings.append("Page had no extractable visible text.")

    return ScrapeResult(url, text, title, "web", warnings)
