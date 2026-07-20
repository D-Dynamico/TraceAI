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
   (plan.md §10, "ambiguous categorization").

3. **The free tier is 10 RPM.** `_RateLimiter` serializes calls with a minimum
   interval so a batch upload cannot trip the quota.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

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


class _RateLimiter:
    """Enforce a minimum interval between calls across threads.

    FastAPI runs sync endpoints in a thread pool, so a multi-file upload can
    hit this concurrently. The lock is held across the sleep, which serializes
    callers — correct for a 10 RPM budget, where parallelism has no value.
    """

    def __init__(self, min_interval_seconds: float) -> None:
        self._min_interval = min_interval_seconds
        self._lock = threading.Lock()
        self._last_call = 0.0

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            delay = self._min_interval - elapsed
            if delay > 0:
                logger.debug("Rate limiter: sleeping %.2fs", delay)
                time.sleep(delay)
            self._last_call = time.monotonic()


# 10 RPM free tier -> 6s spacing, plus headroom for clock skew.
_rate_limiter = _RateLimiter(min_interval_seconds=6.5)

_model = None
_model_lock = threading.Lock()


class CategorizationError(RuntimeError):
    """Raised when Gemini cannot be reached or returns unusable output."""


def is_configured() -> bool:
    return bool(settings.gemini_api_key)


def _redact(message: object) -> str:
    """Strip the API key out of text before it reaches a log.

    SDK errors can carry the failing request URL, and on the REST transport that
    URL contains `?key=<api key>`. Logs get copied into issues and CI output, so
    the key is removed here rather than trusting every error path not to include
    it.
    """
    text = str(message)
    key = settings.gemini_api_key
    if key and key in text:
        text = text.replace(key, "***REDACTED***")
    # Also catch a key embedded in a query string that differs from ours.
    return re.sub(r"(key=)[A-Za-z0-9_\-]{8,}", r"\1***REDACTED***", text)


def _get_model():
    """Lazily build and cache the Gemini client.

    Deferred so that importing this module (and thus starting the app) does not
    require a valid API key.
    """
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        if not is_configured():
            raise CategorizationError("GEMINI_API_KEY is not set.")
        import google.generativeai as genai

        genai.configure(api_key=settings.gemini_api_key)
        _model = genai.GenerativeModel(
            settings.gemini_model,
            generation_config={
                "response_mime_type": "application/json",
                # Classification wants consistency, not creativity.
                "temperature": 0.1,
            },
        )
        logger.info("Gemini client initialized (model=%s)", settings.gemini_model)
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


def fallback_categorization(filename: str, reason: str) -> Categorization:
    """Best-effort classification without the LLM.

    Filenames in this domain are unusually informative ("python_certificate.pdf",
    "internship_offer_letter.pdf"), so keyword matching recovers a usable
    category often enough to be worth doing. confidence stays 0.0 to mark the
    result as unverified.
    """
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
        summary=f"Not categorized yet — {reason}. Details below came from the filename.",
        confidence=0.0,
    )


def _human_reason(exc: Exception) -> str:
    """Turn an SDK exception into something worth showing on a card.

    Only one distinction matters to the reader: will this fix itself? A free
    tier of 10 RPM / 1500 RPD means quota exhaustion is the *expected* failure
    during a batch upload, and it clears on its own — saying so is the
    difference between "wait a moment" and "this site is broken".

    Matched on the exception name and message rather than by importing
    google.api_core: the SDK has moved these classes before, and a categorizer
    that crashes while explaining a failure would defeat the never-raises
    guarantee this module exists to provide.
    """
    name = type(exc).__name__
    text = f"{name} {exc}".lower()
    if "resourceexhausted" in name.lower() or "429" in text or "quota" in text:
        return "the free AI quota is used up for now, so try again shortly"
    # "unavailable" deliberately does NOT belong here — it is gRPC's
    # service-unavailable status (a 503), not a timeout, and lumping the two
    # together told a caller their request was slow when it was refused.
    if "deadline" in text or "timeout" in text or "timed out" in text:
        return "the AI service did not respond in time"
    return "the AI service could not be reached"


def categorize(text: str, filename: str = "") -> Categorization:
    """Classify a document. Always returns a Categorization — never raises.

    On any failure the result is a fallback with confidence 0.0 and the reason
    in its summary, so an upload is never lost to a transient API problem.
    """
    stripped = (text or "").strip()

    if len(stripped) < MIN_INPUT_CHARS:
        logger.info("Too little text to categorize %s (%d chars).", filename, len(stripped))
        return fallback_categorization(filename, "document had no extractable text")

    if not is_configured():
        logger.warning("GEMINI_API_KEY not set — falling back for %s.", filename)
        return fallback_categorization(filename, "no API key configured")

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
        return fallback_categorization(filename, "AI response could not be read")
    except Exception as exc:
        # Network errors, quota exhaustion, safety blocks, SDK changes.
        logger.warning(
            "Gemini call failed for %s: %s: %s",
            filename, type(exc).__name__, _redact(exc),
        )
        return fallback_categorization(filename, _human_reason(exc))

    # A model that returns nothing usable is a failure even when it parses.
    if not result.title and not result.summary:
        logger.warning("Empty categorization for %s — using fallback.", filename)
        return fallback_categorization(filename, "AI returned an empty result")

    if not result.title:
        result.title = Path(filename).stem.replace("_", " ").title() or "Untitled"

    logger.info(
        "Categorized %s as %s/%s (confidence=%.2f)",
        filename, result.category, result.document_type, result.confidence,
    )
    return result
