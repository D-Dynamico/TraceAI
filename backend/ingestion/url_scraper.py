"""URL ingestion for GitHub repos and portfolio/personal links.

Phase 1 scope: fetch a URL and return readable text.
  - GitHub repo URLs -> pull the repo description + README via the public API.
  - Any other URL     -> fetch HTML and strip tags to visible text.

Network failures degrade gracefully into a warning rather than an exception so
the pipeline stays robust.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15  # seconds
USER_AGENT = "TraceAI/0.1 (+https://github.com/) ingestion-bot"

_GITHUB_REPO_RE = re.compile(r"^/([^/]+)/([^/]+)/?$")


@dataclass
class ScrapeResult:
    url: str
    text: str
    title: str = ""
    source_type: str = "web"        # "web" | "github"
    warnings: list[str] = field(default_factory=list)


def _headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT, "Accept": "*/*"}


def _scrape_github(owner: str, repo: str, url: str) -> ScrapeResult:
    warnings: list[str] = []
    parts: list[str] = []
    title = f"{owner}/{repo}"

    repo = repo.removesuffix(".git")
    api = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        meta = requests.get(api, headers=_headers(), timeout=REQUEST_TIMEOUT)
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
        else:
            warnings.append(f"GitHub API returned {meta.status_code} for {api}")
    except requests.RequestException as exc:
        warnings.append(f"GitHub API request failed: {exc}")
        logger.warning("GitHub API failed for %s: %s", url, exc)

    # README (raw) — try main then master.
    for branch in ("main", "master"):
        raw = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/README.md"
        try:
            resp = requests.get(raw, headers=_headers(), timeout=REQUEST_TIMEOUT)
            if resp.ok and resp.text.strip():
                parts.append("README:\n" + resp.text.strip())
                break
        except requests.RequestException as exc:
            logger.debug("README fetch failed (%s): %s", raw, exc)

    if not parts:
        warnings.append("No repo metadata or README could be retrieved.")

    return ScrapeResult(url, "\n\n".join(parts).strip(), title, "github", warnings)


def _scrape_web(url: str) -> ScrapeResult:
    warnings: list[str] = []
    try:
        resp = requests.get(url, headers=_headers(), timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Web fetch failed for %s: %s", url, exc)
        return ScrapeResult(url, "", "", "web", [f"Fetch failed: {exc}"])

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    text = soup.get_text(separator="\n")
    # Collapse excessive blank lines.
    lines = [ln.strip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln)

    if not text:
        warnings.append("Page had no extractable visible text.")

    return ScrapeResult(url, text, title, "web", warnings)


def scrape_url(url: str) -> ScrapeResult:
    """Fetch a URL and return readable text. Dispatches GitHub repos specially."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r} (expected http/https)")

    if parsed.netloc.lower() in ("github.com", "www.github.com"):
        m = _GITHUB_REPO_RE.match(parsed.path)
        if m:
            return _scrape_github(m.group(1), m.group(2), url)

    return _scrape_web(url)
