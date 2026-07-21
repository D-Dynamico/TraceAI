"""Gemini-powered document classification (plan.md §4 Module 2).

Sends extracted text to Gemini and gets back the structured JSON contract
defined in the plan. Three things this module takes seriously:

1. **JSON is requested, not hoped for.** `response_mime_type="application/json"`
   makes the model emit bare JSON. The markdown-fence stripping in
   `_parse_response` is a belt-and-braces fallback for when it drifts anyway.

2. **Failure never blocks an upload.** No API key, a rate limit, a timeout, or
   unparseable output all degrade to `fallback_categorization()`, which infers
   what it can from the filename. The document still lands in the database with
   `confidence = 0.0` and a reason recorded, so the UI can flag it for review
   (plan.md § Risk Mitigation, "ambiguous categorization").

3. **The free tier is 10 RPM.** `_RateLimiter` serializes calls with a minimum
   interval so a batch upload cannot trip the quota.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path

from ai import degradation
from ai import gemini
from config import settings
from models.document import Categorization

logger = logging.getLogger(__name__)

# Gemini 3 Flash has a 1M-token context, but a certificate needs nowhere near
# that. Truncating keeps latency and quota use low; the head of a document is
# where titles, dates, and issuers live.
MAX_INPUT_CHARS = 20_000

# Below this, there is not enough signal to classify — skip the API call.
MIN_INPUT_CHARS = 20

PROMPT_TEMPLATE = """You are analyzing a document from a student's personal \
portfolio of academic and professional records.

Return ONLY a JSON object with exactly these keys:
{{
  "document_type": "certificate | resume | project_report | internship_letter | portfolio | other",
  "category": "Projects | Skills | Certifications | Internships | Achievements | Academics",
  "title": "extracted or inferred title",
  "date": "YYYY-MM or YYYY if found, else null",
  "summary": "2-3 sentence summary",
  "skills": ["skill1", "skill2"],
  "organizations": ["org1", "org2"],
  "people": ["person1"],
  "tags": ["tag1", "tag2"],
  "confidence": 0.0
}}

Rules:
- Use ONLY the listed values for document_type and category.
- date must be the date the document describes (issue/completion date), NOT today.
- If no date appears in the document, use null. Do not guess.
- skills are technical or professional competencies, not job titles.
- people are named individuals, excluding the document's owner where identifiable.
- confidence is your certainty in this classification, between 0.0 and 1.0.
- Extract only what the document supports. Empty lists are correct when nothing applies.

Filename: {filename}

Document text:
---
{text}
---
"""


# The rate limiter and redaction are shared across every Gemini caller (the free
# tier's budget is per-key, not per-module) — see ai/gemini.py. Referenced under
# module-local names so tests can monkeypatch categorizer._rate_limiter and
# categorizer._redact without reaching into another module.
_rate_limiter = gemini.rate_limiter
_redact = gemini.redact

# Classification wants consistency, not creativity.
_GENERATION_CONFIG = {"response_mime_type": "application/json", "temperature": 0.1}

_model = None
_model_lock = threading.Lock()


class CategorizationError(RuntimeError):
    """Raised when Gemini cannot be reached or returns unusable output."""


def is_configured() -> bool:
    return gemini.is_configured()


def _get_model():
    """Lazily build and cache this module's Gemini client.

    Deferred so that importing this module (and thus starting the app) does not
    require a valid API key. Key configuration is shared (`gemini.build_model`);
    the model instance is this module's own, with the classification config.
    """
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        if not is_configured():
            raise CategorizationError("GEMINI_API_KEY is not set.")
        _model = gemini.build_model(_GENERATION_CONFIG)
        return _model


def _parse_response(text: str) -> dict:
    """Turn a model response into a dict, tolerating markdown fences.

    `response_mime_type` should make this unnecessary, but models drift and a
    ```json fence is the most common way they do.
    """
    cleaned = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        # Last resort: pull out the outermost {...} span.
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise CategorizationError(
                f"Response was not JSON: {cleaned[:200]!r}"
            ) from exc
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as inner:
            raise CategorizationError(
                f"Response was not JSON: {cleaned[:200]!r}"
            ) from inner

    if not isinstance(parsed, dict):
        raise CategorizationError(f"Expected a JSON object, got {type(parsed).__name__}.")
    return parsed


def fallback_categorization(
    filename: str, reason: degradation.DegradedReason
) -> Categorization:
    """Best-effort classification without the LLM.

    Filenames in this domain are unusually informative ("python_certificate.pdf",
    "internship_offer_letter.pdf"), so keyword matching recovers a usable
    category often enough to be worth doing. confidence stays 0.0 to mark the
    result as unverified.

    `reason` is a structured code (deferred item B); the human prose shown on the
    card and the `retryable` flag both come from it, so the two can never
    disagree about whether trying again will help.
    """
    degraded = degradation.from_reason(reason)
    stem = Path(filename).stem.replace("_", " ").replace("-", " ")
    lowered = stem.lower()

    keyword_map = [
        (("certificate", "certification", "certified", "cert"), "Certifications", "certificate"),
        (("internship", "intern"), "Internships", "internship_letter"),
        (("resume", "cv", "curriculum"), "Academics", "resume"),
        (("project", "report"), "Projects", "project_report"),
        (("portfolio",), "Projects", "portfolio"),
        (("award", "winner", "achievement", "hackathon"), "Achievements", "other"),
        (("marksheet", "transcript", "grade", "semester"), "Academics", "other"),
    ]
    category, document_type = "Uncategorized", "other"
    for keywords, mapped_category, mapped_type in keyword_map:
        if any(keyword in lowered for keyword in keywords):
            category, document_type = mapped_category, mapped_type
            break

    # A 4-digit year in the filename is a reasonable date hint.
    year_match = re.search(r"\b(19|20)\d{2}\b", stem)

    return Categorization(
        document_type=document_type,
        category=category,
        title=stem.strip().title() or filename,
        date=year_match.group(0) if year_match else None,
        # Phrased for the person who sees it on a card, not for a log reader.
        # "unavailable (AI service error (ResourceExhausted))" was the whole
        # sentence a free-tier rate limit produced, which reads as a broken
        # site rather than as "wait a moment" — the one failure here that
        # actually resolves itself.
        summary=f"Not categorized yet — {degraded.message}. Details below came from the filename.",
        confidence=0.0,
        degraded_reason=degraded.reason,
        retryable=degraded.retryable,
    )


def _human_reason(exc: Exception) -> str:
    """The card-facing prose for an SDK failure — the classified reason's message.

    Kept as a named helper because the reason→message mapping (does this fix
    itself?) is the thing worth testing directly; `categorize` now degrades via
    the structured code, but this preserves the readable-message guarantee.
    """
    return degradation.from_reason(degradation.classify_exception(exc)).message


def categorize(text: str, filename: str = "") -> Categorization:
    """Classify a document. Always returns a Categorization — never raises.

    On any failure the result is a fallback with confidence 0.0 and the reason
    in its summary, so an upload is never lost to a transient API problem.
    """
    stripped = (text or "").strip()

    if len(stripped) < MIN_INPUT_CHARS:
        logger.info("Too little text to categorize %s (%d chars).", filename, len(stripped))
        return fallback_categorization(filename, "no_text")

    if not is_configured():
        logger.warning("GEMINI_API_KEY not set — falling back for %s.", filename)
        return fallback_categorization(filename, "no_api_key")

    truncated = stripped[:MAX_INPUT_CHARS]
    if len(stripped) > MAX_INPUT_CHARS:
        logger.debug("Truncated %s from %d to %d chars.", filename, len(stripped), MAX_INPUT_CHARS)

    prompt = PROMPT_TEMPLATE.format(filename=filename or "(unknown)", text=truncated)

    try:
        model = _get_model()
        _rate_limiter.wait()
        response = model.generate_content(prompt)
        payload = _parse_response(response.text)
        result = Categorization.model_validate(payload)
    except CategorizationError as exc:
        logger.warning("Categorization failed for %s: %s", filename, _redact(exc))
        return fallback_categorization(filename, "unreadable_response")
    except Exception as exc:
        # Network errors, quota exhaustion, safety blocks, SDK changes.
        logger.warning(
            "Gemini call failed for %s: %s: %s",
            filename, type(exc).__name__, _redact(exc),
        )
        return fallback_categorization(filename, degradation.classify_exception(exc))

    # A model that returns nothing usable is a failure even when it parses.
    if not result.title and not result.summary:
        logger.warning("Empty categorization for %s — using fallback.", filename)
        return fallback_categorization(filename, "unreadable_response")

    if not result.title:
        result.title = Path(filename).stem.replace("_", " ").title() or "Untitled"

    logger.info(
        "Categorized %s as %s/%s (confidence=%.2f)",
        filename, result.category, result.document_type, result.confidence,
    )
    return result
