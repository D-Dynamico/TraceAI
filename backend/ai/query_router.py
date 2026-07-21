"""Deterministic query routing for search (plan.md §4 Module 5, Path 3).

The plan's Path 3 uses Gemini to parse a query into intent + filters. That is the
wrong tool on the one screen that must feel instant: a Gemini parse shares the
6.5s-spaced rate-limiter lane with categorization, so a search issued right after
an upload can stall behind the whole ingest queue — and it spends daily quota on
"show all my certificates". So query *understanding* is done here, deterministically
and offline; Gemini is reserved for answer *synthesis* (RAG, Phase 7).

The routing is binary:
  - a confident structured filter (a category word, optionally "latest") -> SQL
  - everything else -> semantic search

Ambiguity has a safe floor: anything not confidently a filter falls to semantic
search, never to a guess. Question-shaped queries ("how does X relate to Y")
route to semantic even when they mention a category, because they want an answer,
not a list.

The category vocabulary is derived from `models.document.CATEGORIES` so it cannot
drift from what the model actually stores. The only hand-maintained piece is
`_ALIASES` — the document-type words a user types that are not spelled like their
category ("certificate" -> Certifications, "resume" -> Academics) — kept explicit
and small on purpose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from models.document import CATEGORIES

# Document-type words people actually type that differ from their category name.
# These are the known category/type mismatches; everything else is derived below.
_ALIASES = {
    "certificate": "Certifications",
    "certificates": "Certifications",
    "certification": "Certifications",
    "cert": "Certifications",
    "certs": "Certifications",
    "resume": "Academics",
    "cv": "Academics",
    "marksheet": "Academics",
    "transcript": "Academics",
    "grade": "Academics",
    "grades": "Academics",
    "intern": "Internships",
    "award": "Achievements",
    "awards": "Achievements",
    "hackathon": "Achievements",
}


def _canonical_keywords() -> dict[str, str]:
    """Map every recognised keyword to its canonical category.

    Built from CATEGORIES (so "Projects" matches "project"/"projects") plus the
    explicit aliases. Rebuilt at import; if CATEGORIES changes, the canonical
    half updates automatically.
    """
    mapping: dict[str, str] = {}
    for category in CATEGORIES:
        lowered = category.lower()
        mapping[lowered] = category  # "certifications"
        if lowered.endswith("s"):
            mapping[lowered[:-1]] = category  # "certification"
    mapping.update(_ALIASES)
    return mapping


_KEYWORDS = _canonical_keywords()

# A query that opens with one of these, or is punctuated as one, wants an answer
# rather than a list — send it to semantic search even if it names a category.
_QUESTION_STARTERS = {
    "how", "what", "why", "which", "who", "where", "when",
}
_RELATIONAL = {
    "relate", "related", "relates", "connect", "connects", "connection",
    "compare", "versus", "vs", "difference", "between",
}
_LATEST = {"latest", "recent", "newest", "last", "most recent"}

_WORD_RE = re.compile(r"[a-z]+")


@dataclass(frozen=True)
class Route:
    """The decision: how to serve a query.

    `mode` is "filter" (structured SQL) or "semantic" (vector search). For a
    filter, `category` names the SQL filter (None means "all documents") and
    `sort` is "latest" when the query asked for the most recent.
    """

    mode: str
    category: str | None = None
    sort: str | None = None


def _looks_like_question(query: str, words: list[str]) -> bool:
    if "?" in query:
        return True
    if words and words[0] in _QUESTION_STARTERS:
        return True
    return any(word in _RELATIONAL for word in words)


def route(query: str) -> Route:
    """Classify a query into a structured filter or a semantic search."""
    text = (query or "").strip().lower()
    words = _WORD_RE.findall(text)

    # Questions want a synthesized answer, not a filtered list — even when they
    # mention a category ("how does my cert relate to my internship?").
    if _looks_like_question(text, words):
        return Route(mode="semantic")

    category = next((_KEYWORDS[w] for w in words if w in _KEYWORDS), None)
    wants_latest = any(w in _LATEST for w in words)

    # A category word is a confident filter signal. "latest" alone (no category)
    # is still a filter — "show my latest documents" wants the newest rows.
    if category is not None or wants_latest:
        return Route(
            mode="filter",
            category=category,
            sort="latest" if wants_latest else None,
        )

    return Route(mode="semantic")
