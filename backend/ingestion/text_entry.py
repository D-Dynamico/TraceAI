"""Direct written-response handling (plan.md §4 Module 1).

The user types an achievement straight into a text box — "Led the Data Science
Club in 2024, organized 5 workshops". There is no file and nothing to parse, so
this module only validates and normalizes; the text goes to the same Gemini
categorizer as every other input.

Why it exists at all: club leadership, hackathon wins, and volunteer work often
have no certificate. Without this path they cannot enter the system.
"""

from __future__ import annotations

from dataclasses import dataclass

# Generous, but bounded — this text goes into SQLite and is truncated to 20k
# before Gemini sees it anyway. A megabyte paste is a mistake, not an
# achievement.
MAX_ENTRY_CHARS = 50_000
MIN_ENTRY_CHARS = 10


class InvalidTextEntry(ValueError):
    """The submitted text cannot be ingested."""


@dataclass
class TextEntry:
    text: str
    char_count: int


def prepare(text: str) -> TextEntry:
    """Validate and normalize a written response.

    Raises InvalidTextEntry for empty or oversized input, which the route turns
    into a 400. Too-short entries are rejected because a one-word entry gives
    the categorizer nothing to work with and produces a junk timeline row.
    """
    if text is None:
        raise InvalidTextEntry("No text provided.")

    # Normalize newlines and strip trailing whitespace per line, so the same
    # entry pasted from different editors hashes and reads identically.
    cleaned = "\n".join(
        line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ).strip()

    if not cleaned:
        raise InvalidTextEntry("Text entry is empty.")
    if len(cleaned) < MIN_ENTRY_CHARS:
        raise InvalidTextEntry(
            f"Text entry is too short (minimum {MIN_ENTRY_CHARS} characters)."
        )
    if len(cleaned) > MAX_ENTRY_CHARS:
        raise InvalidTextEntry(
            f"Text entry exceeds the {MAX_ENTRY_CHARS} character limit."
        )

    return TextEntry(text=cleaned, char_count=len(cleaned))


def derive_filename(text: str, limit: int = 60) -> str:
    """A human-readable stand-in for `filename`, which is NOT NULL in the schema.

    Text entries have no file, but the column is required and the value is what
    listings show before Gemini supplies a title. The first line, truncated.
    """
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if len(first_line) > limit:
        first_line = first_line[:limit].rstrip() + "…"
    return first_line or "Written entry"
