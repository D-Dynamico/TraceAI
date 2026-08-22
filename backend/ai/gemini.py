"""Shared Gemini plumbing for every module that calls the model.

Four callers exist today — `ai/categorizer.py` (Module 2), `ai/career_path.py`
(Module 3 Layer C), `ai/rag.py` (Phase 7), and `ai/vision.py` (OCR fallback) —
and the free tier's limits are **global**, not per-caller: **5 RPM** (measured;
see the constant below) across the whole key. So the rate limiter lives here, as
one instance every caller shares, rather than one limiter each (which would let
four callers issue ~4x the intended rate). API-key configuration and log
redaction are shared for the same reason: there is exactly one right way to do
each, and duplicating them invites drift.

Each caller still builds its *own* `GenerativeModel` (different generation
configs — the categorizer wants determinism, inference wants a little latitude),
but through `build_model` so key configuration happens once.
"""

from __future__ import annotations

import logging
import re
import threading
import time

from config import settings

logger = logging.getLogger(__name__)


class RateLimiter:
    """Enforce a minimum interval between calls across threads.

    FastAPI runs sync endpoints in a thread pool, so concurrent callers can hit
    this at once. The lock is held across the sleep, which serializes callers —
    correct for a fixed RPM budget, where parallelism buys nothing.
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


# 5 RPM -> 12s spacing, plus headroom for clock skew. ONE instance: the budget is
# per-key, so every Gemini caller must queue through the same gate.
#
# **5, not the 10 this project assumed until 2026-07-25.** The API said so itself,
# in the 429 that Phase 9's Vision work provoked:
#
#     Quota exceeded for metric:
#     generativelanguage.googleapis.com/generate_content_free_tier_requests,
#     limit: 5, model: gemini-3-flash
#     quota_id: "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
#     retry_delay { seconds: 14 }
#
# 6.5s spacing is ~9 RPM — nearly double the real ceiling, so any two callers in
# quick succession were already gambling; the second Gemini call per scanned
# upload just made the loss reliable. The docs no longer publish a per-model
# free-tier RPM (they defer to AI Studio), so the enforced quota in a live 429 is
# the best evidence available — and better than a doc table, being this key's
# actual limit. Verify with `pytest -m live` after changing it.
#
# The cost is real: ~13s per call, so a scanned upload (Vision + categorization)
# takes ~26s. Correct beats fast here — the alternative is a document that lands
# with no text. plan.md §11's cache/batch/queue mitigations remain unbuilt.
rate_limiter = RateLimiter(min_interval_seconds=13.0)


def is_configured() -> bool:
    return bool(settings.gemini_api_key)


def redact(message: object) -> str:
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


_configured = False
_config_lock = threading.Lock()


def _ensure_configured() -> None:
    """Configure the SDK with the API key exactly once, process-wide."""
    global _configured
    if _configured:
        return
    with _config_lock:
        if _configured:
            return
        if not is_configured():
            raise RuntimeError("GEMINI_API_KEY is not set.")
        import google.generativeai as genai

        genai.configure(api_key=settings.gemini_api_key)
        _configured = True
        logger.info("Gemini SDK configured (model=%s)", settings.gemini_model)


def build_model(generation_config: dict):
    """Build a GenerativeModel with the given config, after ensuring the key.

    Deferred import so that merely importing a caller does not require the SDK
    or a valid key at startup.
    """
    _ensure_configured()
    import google.generativeai as genai

    return genai.GenerativeModel(settings.gemini_model, generation_config=generation_config)


# A 429 the API asked us to wait out is worth waiting out, but not forever: past
# this the caller degrades and the user gets a card they can retry by hand,
# which beats an upload that hangs.
MAX_RETRY_DELAY_SECONDS = 30.0

_RETRY_DELAY_RE = re.compile(r"retry_delay\s*{\s*seconds:\s*(\d+)", re.IGNORECASE)


def retry_after(exc: Exception) -> float | None:
    """How long to wait before retrying `exc`, or None if retrying cannot help.

    **Only the per-minute quota is retried.** A 429 carries a `quota_id` naming
    which limit was hit, and the free tier has two: 5 requests per minute, which
    clears in seconds, and 20 per *day*, which does not. Retrying the daily one
    spends the wait and then a second doomed request out of a budget that is
    already gone — so a payload naming the per-day quota returns None and the
    caller degrades immediately, as it did before.

    The delay comes from the API's own `retry_delay { seconds: N }` when present
    (it is, on the 429s this free tier issues); otherwise the limiter's interval
    is a safe stand-in, since that is the spacing the minute quota wants anyway.
    """
    text = f"{type(exc).__name__} {exc}".lower()
    if "429" not in text and "resourceexhausted" not in text and "quota" not in text:
        return None
    if "perday" in text or "per day" in text:
        return None

    match = _RETRY_DELAY_RE.search(str(exc))
    delay = float(match.group(1)) if match else rate_limiter._min_interval
    return min(delay, MAX_RETRY_DELAY_SECONDS)


def generate(model, payload, *, limiter: RateLimiter | None = None):
    """Rate-limited `generate_content`, retried once on a per-minute 429.

    Every Gemini call in this app goes through here. The retry exists because
    the API tells us exactly how long to wait — `retry_delay { seconds: 14 }` —
    and the four callers used to throw that away and degrade on the spot. At 5
    RPM a burst of two calls (a scanned upload is Vision *then* categorization)
    could trip the limit despite the spacing; one honest wait turns that into a
    success instead of a document filed by filename guess.

    One retry, never two: a second failure means the minute quota is genuinely
    saturated or the daily one is gone, and the caller's degradation path is the
    right answer then.

    `limiter` is injectable because each caller aliases the shared limiter as a
    module attribute their tests monkeypatch — passing it keeps that seam.
    """
    limiter = limiter or rate_limiter
    limiter.wait()
    try:
        return model.generate_content(payload)
    except Exception as exc:
        delay = retry_after(exc)
        if delay is None:
            raise
        logger.warning(
            "Gemini rate-limited; retrying once in %.0fs: %s", delay, redact(exc)
        )
        time.sleep(delay)
        limiter.wait()
        return model.generate_content(payload)
