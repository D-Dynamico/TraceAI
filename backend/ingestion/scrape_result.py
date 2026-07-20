"""The shared return type for every scraper.

Lives in its own module rather than in `url_scraper` because `url_scraper`
imports the scrapers to dispatch to them, and they in turn need this type —
defining it there would be an import cycle. `url_scraper` re-exports it, so
`from ingestion.url_scraper import ScrapeResult` keeps working.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
    # A date the *source* knows about itself, as "YYYY-MM" — a GitHub repo's
    # creation date, say. Distinct from a date Gemini reads out of the text: a
    # README rarely states when the project began, so without this a repo has
    # no date at all and the timeline falls back to the upload date (plan.md
    # §10), stamping a 2011 project with today. Still a *known* date, not an
    # assumed one, which is why it populates extracted_date.
    source_date: str | None = None
    # Structured facts the source states about itself — a repo's star count, its
    # language breakdown, a profile's repo list. Deliberately *not* folded into
    # `text`: that field is the Gemini input and the future embedding source,
    # and star counts do not help either. This is display data, carried
    # alongside so the card can show a repo as a repo without the UI having to
    # re-parse prose out of the scraped text.
    #
    # Free-form by scraper. `details["kind"]` names the shape ("repo",
    # "profile"); readers must tolerate its absence, since a scrape that
    # partially failed still returns whatever it did get.
    details: dict[str, Any] = field(default_factory=dict)
