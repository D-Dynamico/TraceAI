"""Pydantic models for categorization output and API responses.

`Categorization` mirrors the JSON contract in plan.md §4 Module 2. Its validators
are deliberately forgiving: a free-tier LLM will occasionally return a plausible
synonym ("Certification" for "Certifications"), a string where a list belongs, or
a confidence as a percentage. Coercing these is better than failing an upload —
an unknown value falls back to "other"/"Uncategorized" rather than raising.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

# The closed vocabularies from plan.md §4 Module 2.
DOCUMENT_TYPES = {
    "certificate", "resume", "project_report",
    "internship_letter", "portfolio", "other",
}
CATEGORIES = {
    "Projects", "Skills", "Certifications",
    "Internships", "Achievements", "Academics",
}

UNKNOWN_CATEGORY = "Uncategorized"


def _coerce_list(value: Any) -> list[str]:
    """Accept a list, a comma-separated string, or null — always return a list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(v).strip() for v in value if v is not None and str(v).strip()]
    return []


class Categorization(BaseModel):
    """Structured metadata extracted from a document by Gemini."""

    document_type: str = "other"
    category: str = UNKNOWN_CATEGORY
    title: str = ""
    date: str | None = None
    summary: str = ""
    skills: list[str] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    confidence: float = 0.0

    @field_validator("document_type", mode="before")
    @classmethod
    def _normalize_document_type(cls, value: Any) -> str:
        if not isinstance(value, str):
            return "other"
        normalized = value.strip().lower().replace(" ", "_").replace("-", "_")
        return normalized if normalized in DOCUMENT_TYPES else "other"

    @field_validator("category", mode="before")
    @classmethod
    def _normalize_category(cls, value: Any) -> str:
        if not isinstance(value, str):
            return UNKNOWN_CATEGORY
        candidate = value.strip()
        # Match case-insensitively, then tolerate a missing plural ("Project").
        by_lower = {c.lower(): c for c in CATEGORIES}
        if candidate.lower() in by_lower:
            return by_lower[candidate.lower()]
        if f"{candidate.lower()}s" in by_lower:
            return by_lower[f"{candidate.lower()}s"]
        return UNKNOWN_CATEGORY

    @field_validator("date", mode="before")
    @classmethod
    def _normalize_date(cls, value: Any) -> str | None:
        """Keep YYYY or YYYY-MM; reject anything else rather than storing junk.

        The timeline sorts on this column, so a malformed value is worse than
        a null (plan.md §10 falls back to upload date when no date is found).
        """
        if value is None:
            return None
        text = str(value).strip()
        if not text or text.lower() in {"null", "none", "n/a", "unknown", ""}:
            return None
        parts = text.split("-")
        if len(parts) >= 2 and parts[0].isdigit() and len(parts[0]) == 4 and parts[1].isdigit():
            return f"{parts[0]}-{int(parts[1]):02d}"
        if parts[0].isdigit() and len(parts[0]) == 4:
            return parts[0]
        return None

    @field_validator("skills", "organizations", "people", "tags", mode="before")
    @classmethod
    def _normalize_lists(cls, value: Any) -> list[str]:
        return _coerce_list(value)

    @field_validator("title", "summary", mode="before")
    @classmethod
    def _normalize_text(cls, value: Any) -> str:
        return str(value).strip() if value is not None else ""

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, value: Any) -> float:
        try:
            score = float(value)
        except (TypeError, ValueError):
            return 0.0
        # Models sometimes answer 85 instead of 0.85.
        if score > 1.0:
            score = score / 100.0
        return min(max(score, 0.0), 1.0)


class DocumentSummary(BaseModel):
    """A document as returned in listings — no raw text."""

    id: str
    filename: str
    file_type: str | None = None
    source_url: str | None = None
    document_type: str | None = None
    category: str | None = None
    title: str | None = None
    summary: str | None = None
    extracted_date: str | None = None
    upload_date: str | None = None
    # The date a consumer should actually display or sort on, plus where it came
    # from. Resolved server-side in database._resolve_date so the timeline and
    # the graph cannot apply plan.md §10's upload-date fallback while forgetting
    # its "flag for user review" half. "assumed" means we guessed — show it as a
    # guess.
    effective_date: str | None = None
    date_source: str = "assumed"  # "extracted" | "assumed"
    confidence: float | None = None
    checksum: str | None = None


class DocumentDetail(DocumentSummary):
    """A single document with its entities, tags, and extracted text."""

    original_path: str | None = None
    raw_text: str | None = None
    skills: list[str] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
