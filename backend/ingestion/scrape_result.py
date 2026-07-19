"""The shared return type for every scraper.

Lives in its own module rather than in `url_scraper` because `url_scraper`
imports the scrapers to dispatch to them, and they in turn need this type —
defining it there would be an import cycle. `url_scraper` re-exports it, so
`from ingestion.url_scraper import ScrapeResult` keeps working.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The shared User-Agent. Sites that block unknown agents block all of our
# scrapers identically, so this belongs with the shared type.
USER_AGENT = "TraceAI/0.1 (+https://github.com/) ingestion-bot"
REQUEST_TIMEOUT = 15  # seconds


def headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT, "Accept": "*/*"}


@dataclass
class ScrapeResult:
    url: str
    text: str
    title: str = ""
    source_type: str = "web"        # "web" | "github"
    warnings: list[str] = field(default_factory=list)
