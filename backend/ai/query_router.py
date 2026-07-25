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

**A keyword resolves to a category *and*, where the word names one, a
`document_type`.** Mapping a word to a category alone was a real bug: "resume"
predicted *Academics*, Gemini filed an actual resume under *Skills* (a resume is
mostly skills), and the SQL filter then excluded the very document the user asked
for. Two independent answers to "what is a resume" — the table's and the model's
— and the filter trusted only the table's. The database already stores the other
half: that document's `document_type` is literally `resume`. So a keyword carries
both, the filter matches *either*, and a category the model chose differently can
no longer hide a document from the word that names its type. The same fault hid
the seed's "Hackathon Winner Certificate" (`document_type=certificate`, category
*Achievements*) from "show all my certificates" — a plan.md §16 must-work query.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from models.document import CATEGORIES

# Words people actually type, mapped to (category, document_type). The
# document_type is None when the word names only a category ("marksheet" is
# Academics, but there is no marksheet document_type — the taxonomy in
# models.document.DOCUMENT_TYPES is closed and small).
#
# Category-spelled words are derived below; the ones repeated here
# ("certifications", "internships") are repeated *because* they also name a
# document type and would otherwise resolve to the category half only.
_ALIASES: dict[str, tuple[str, str | None]] = {
    "certificate": ("Certifications", "certificate"),
    "certificates": ("Certifications", "certificate"),
    "certification": ("Certifications", "certificate"),
    "certifications": ("Certifications", "certificate"),
    "cert": ("Certifications", "certificate"),
    "certs": ("Certifications", "certificate"),
    "resume": ("Academics", "resume"),
    "resumes": ("Academics", "resume"),
    "cv": ("Academics", "resume"),
    "marksheet": ("Academics", None),
    "transcript": ("Academics", None),
    "grade": ("Academics", None),
    "grades": ("Academics", None),
    "intern": ("Internships", "internship_letter"),
    "internship": ("Internships", "internship_letter"),
    "internships": ("Internships", "internship_letter"),
    "award": ("Achievements", None),
    "awards": ("Achievements", None),
    "hackathon": ("Achievements", None),
}


def _canonical_keywords() -> dict[str, tuple[str, str | None]]:
    """Map every recognised keyword to its (category, document_type) pair.

    Built from CATEGORIES (so "Projects" matches "project"/"projects") plus the
    explicit aliases, which are applied last and therefore win. Rebuilt at
    import; if CATEGORIES changes, the canonical half updates automatically.
    """
    mapping: dict[str, tuple[str, str | None]] = {}
    for category in CATEGORIES:
        lowered = category.lower()
        mapping[lowered] = (category, None)  # "certifications"
        if lowered.endswith("s"):
            mapping[lowered[:-1]] = (category, None)  # "certification"
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
    filter, `category` and `document_type` name what to match — a document
    satisfying *either* is a hit, because the word the user typed can name a
    category, a document type, or both, and the model's choice of category is
    not authoritative over what the document plainly is. Both None means "all
    documents". `sort` is "latest" when the query asked for the most recent.
    """

    mode: str
    category: str | None = None
    document_type: str | None = None
    sort: str | None = None


def _looks_like_question(query: str, words: list[str]) -> bool:
    if "?" in query:
        return True
    if words and words[0] in _QUESTION_STARTERS:
        return True
    return any(word in _RELATIONAL for word in words)


def is_question(query: str) -> bool:
    """Whether a query wants a synthesized answer rather than a list.

    The public form of `_looks_like_question`, used by the search endpoint to
    decide whether to offer a RAG answer card (Phase 7). A question always routes
    to semantic search; this additionally distinguishes "answer me" from a plain
    semantic lookup that only wants ranked documents.
    """
    text = (query or "").strip().lower()
    return _looks_like_question(text, _WORD_RE.findall(text))


def route(query: str) -> Route:
    """Classify a query into a structured filter or a semantic search."""
    text = (query or "").strip().lower()
    words = _WORD_RE.findall(text)

    # Questions want a synthesized answer, not a filtered list — even when they
    # mention a category ("how does my cert relate to my internship?").
    if _looks_like_question(text, words):
        return Route(mode="semantic")

    match = next((_KEYWORDS[w] for w in words if w in _KEYWORDS), None)
    category, document_type = match if match is not None else (None, None)
    wants_latest = any(w in _LATEST for w in words)

    # A category word is a confident filter signal. "latest" alone (no category)
    # is still a filter — "show my latest documents" wants the newest rows.
    if match is not None or wants_latest:
        return Route(
            mode="filter",
            category=category,
            document_type=document_type,
            sort="latest" if wants_latest else None,
        )

    return Route(mode="semantic")
