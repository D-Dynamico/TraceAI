"""The structured contract for a degraded AI result (deferred item B).

When a Gemini call fails, the failure is recorded as a *reason code* plus a
`retryable` flag, not only as English prose. A client can then behave: offer a
retry for a quota exhaustion (which clears itself) but not for a missing API key
(which will not), badge the two differently, or auto-retry after a delay —
instead of pattern-matching a sentence that exists to be read, not parsed.

This was deferred until a second Gemini caller existed to design against (see
`docs/deferred-ui-and-degradation.md`). There are now four — `ai/categorizer.py`,
`ai/career_path.py`, `ai/rag.py`, and `ai/vision.py` — and all four degrade
through this one table, so the mapping from cause to "does retrying help?" is
defined in a single place.
"""

from __future__ import annotations

from typing import Literal, NamedTuple

DegradedReason = Literal[
    "quota",  # free-tier RPM/RPD used up — clears itself
    "timeout",  # the model did not respond in time
    "unreachable",  # network / service could not be reached
    "no_api_key",  # no key configured — will not fix itself
    "unreadable_response",  # a response came back but was not usable JSON
    "no_text",  # nothing to send — not the model's fault, not retryable
    "vision_disabled",  # Vision OCR turned off by config — a choice, not a fault
    "too_large",  # the file exceeds the inline request cap — will not fix itself
]


class Degradation(NamedTuple):
    reason: DegradedReason
    retryable: bool
    message: str  # human prose for a card; the reason code is for behaviour


# reason -> (retryable, prose). The prose is phrased for the person who sees a
# card, and the substrings here are relied on by test_categorizer's readability
# check, so keep "quota", "respond in time", and "could not be reached" intact.
_TABLE: dict[str, tuple[bool, str]] = {
    "quota": (True, "the free AI quota is used up for now, so try again shortly"),
    "timeout": (True, "the AI service did not respond in time"),
    "unreachable": (True, "the AI service could not be reached"),
    "no_api_key": (False, "no API key is configured"),
    "unreadable_response": (True, "the AI response could not be read"),
    "no_text": (False, "the document had no extractable text"),
    "vision_disabled": (False, "AI image reading is turned off"),
    "too_large": (False, "the file is too large to send to the AI"),
}


def from_reason(reason: DegradedReason) -> Degradation:
    """Look a reason up. Never raises, for the same reason its callers never do.

    `_TABLE[reason]` was a KeyError waiting inside the one code path whose whole
    job is degrading gracefully: an unknown reason — a typo, a new code added in
    one place — would have turned a handled failure into an unhandled one, in
    the exception handler. An unmapped reason degrades to the most conservative
    reading instead: something went wrong, retrying may help.
    """
    retryable, message = _TABLE.get(reason, (True, "the AI step did not complete"))
    return Degradation(reason=reason, retryable=retryable, message=message)


def classify_exception(exc: Exception) -> DegradedReason:
    """Map an SDK exception to a reason code.

    Matched on the exception's name and message rather than by importing
    google.api_core: the SDK has relocated these classes before, and a caller
    that crashes while classifying a failure would defeat the never-raises
    guarantee its degradation exists to provide. Only the three transport-level
    causes are inferred here — the others are decided by the call site, which
    knows them without an exception (no key, no text, empty parse).
    """
    name = type(exc).__name__
    text = f"{name} {exc}".lower()
    if "resourceexhausted" in name.lower() or "429" in text or "quota" in text:
        return "quota"
    # "unavailable" deliberately does NOT map to timeout — it is gRPC's
    # service-unavailable (503), a refusal, not slowness.
    if "deadline" in text or "timeout" in text or "timed out" in text:
        return "timeout"
    return "unreachable"
