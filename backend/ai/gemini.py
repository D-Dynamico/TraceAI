"""Shared Gemini plumbing for every module that calls the model.

Two callers exist today — `ai/categorizer.py` (Module 2) and `ai/career_path.py`
(Module 3 Layer C) — and the free tier's limits are **global**, not per-caller:
10 RPM / 1500 RPD across the whole key. So the rate limiter lives here, as one
instance both callers share, rather than one limiter each (which would let two
callers issue ~2x the intended rate). API-key configuration and log redaction
are shared for the same reason: there is exactly one right way to do each, and
duplicating them invites drift.

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


# 10 RPM free tier -> 6s spacing, plus headroom for clock skew. ONE instance:
# the budget is per-key, so every Gemini caller must queue through the same gate.
rate_limiter = RateLimiter(min_interval_seconds=6.5)


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
