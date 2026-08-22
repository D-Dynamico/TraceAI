"""The shared Gemini plumbing: the retry, the limiter, and redaction.

`ai/gemini.py` had no test file of its own — it was covered only sideways,
through the four callers. The retry added here is exactly the kind of thing that
needs a direct test: it decides whether a 429 costs one call or two, out of a
budget of twenty per day.
"""

from __future__ import annotations

import pytest

from ai import gemini


class _Quota(Exception):
    """Shaped like the 429 the free tier actually returns."""


def _quota_exc(quota_id: str, delay: int | None = 14) -> Exception:
    body = (
        "429 Quota exceeded for metric: "
        "generativelanguage.googleapis.com/generate_content_free_tier_requests, "
        f"limit: 5, model: gemini-3-flash quota_id: \"{quota_id}\""
    )
    if delay is not None:
        body += f" retry_delay {{ seconds: {delay} }}"
    return _Quota(body)


class _Model:
    """A model that fails `fail_times` times, then returns a response."""

    def __init__(self, exc: Exception, fail_times: int = 1):
        self.exc = exc
        self.remaining = fail_times
        self.calls = 0

    def generate_content(self, payload):
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise self.exc
        return "ok"


class _Limiter:
    def __init__(self):
        self.waits = 0

    def wait(self):
        self.waits += 1


@pytest.fixture
def no_sleep(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(gemini.time, "sleep", slept.append)
    return slept


# --- retry_after: which 429s are worth waiting out -------------------------


def test_per_minute_quota_uses_the_delay_the_api_returned():
    delay = gemini.retry_after(_quota_exc("GenerateRequestsPerMinutePerProjectPerModel-FreeTier"))
    assert delay == 14.0


def test_per_day_quota_is_not_retried():
    """20/day does not clear in seconds. Retrying spends the wait *and* a second
    request out of a budget that is already gone."""
    assert gemini.retry_after(_quota_exc("GenerateRequestsPerDayPerProjectPerModel-FreeTier")) is None


def test_a_non_quota_error_is_not_retried():
    assert gemini.retry_after(ConnectionError("connection reset by peer")) is None


def test_a_quota_error_without_a_delay_falls_back_to_the_limiter_interval():
    exc = _quota_exc("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", delay=None)
    assert gemini.retry_after(exc) == gemini.rate_limiter._min_interval


def test_an_absurd_delay_is_capped():
    exc = _quota_exc("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", delay=9999)
    assert gemini.retry_after(exc) == gemini.MAX_RETRY_DELAY_SECONDS


# --- generate: one retry, never two ---------------------------------------


def test_a_rate_limited_call_succeeds_on_the_retry(no_sleep):
    model = _Model(_quota_exc("GenerateRequestsPerMinutePerProjectPerModel-FreeTier"))
    limiter = _Limiter()

    assert gemini.generate(model, "prompt", limiter=limiter) == "ok"
    assert model.calls == 2
    assert no_sleep == [14.0]
    assert limiter.waits == 2  # spacing re-applied before the second attempt


def test_a_second_failure_is_raised_for_the_caller_to_degrade(no_sleep):
    """One retry, never two — the caller's degradation path owns it from here."""
    model = _Model(_quota_exc("GenerateRequestsPerMinutePerProjectPerModel-FreeTier"), fail_times=2)

    with pytest.raises(_Quota):
        gemini.generate(model, "prompt", limiter=_Limiter())
    assert model.calls == 2


def test_a_daily_quota_failure_is_raised_without_sleeping(no_sleep):
    model = _Model(_quota_exc("GenerateRequestsPerDayPerProjectPerModel-FreeTier"), fail_times=2)

    with pytest.raises(_Quota):
        gemini.generate(model, "prompt", limiter=_Limiter())
    assert model.calls == 1
    assert no_sleep == []


def test_the_limiter_runs_before_the_call(no_sleep):
    model = _Model(RuntimeError("x"), fail_times=0)
    limiter = _Limiter()

    gemini.generate(model, "prompt", limiter=limiter)

    assert limiter.waits == 1


# --- redaction -------------------------------------------------------------


def test_redact_strips_a_key_from_a_url(monkeypatch):
    monkeypatch.setattr(gemini.settings, "gemini_api_key", "AIzaSyTOPSECRETVALUE1234")
    text = gemini.redact("POST https://host/v1/models?key=AIzaSyTOPSECRETVALUE1234 failed")
    assert "AIzaSyTOPSECRETVALUE1234" not in text
    assert "REDACTED" in text


def test_redact_strips_a_key_that_is_not_ours():
    """The regex half — an SDK error can echo a key from somewhere else."""
    assert "OTHERKEY" not in gemini.redact("?key=OTHERKEY9999 rejected")
