"""The structured degradation contract (deferred item B, ai/degradation.py)."""

from __future__ import annotations

import pytest

from ai import degradation


def test_retryable_split_matches_whether_the_failure_clears_itself():
    # The whole point of the contract: quota clears, a missing key does not.
    assert degradation.from_reason("quota").retryable is True
    assert degradation.from_reason("timeout").retryable is True
    assert degradation.from_reason("unreachable").retryable is True
    assert degradation.from_reason("no_api_key").retryable is False
    assert degradation.from_reason("no_text").retryable is False


def test_every_reason_carries_prose():
    for reason in ("quota", "timeout", "unreachable", "no_api_key",
                   "unreadable_response", "no_text"):
        deg = degradation.from_reason(reason)
        assert deg.reason == reason
        assert deg.message


@pytest.mark.parametrize(
    "exc, expected",
    [
        (RuntimeError("429 Resource has been exhausted (quota)"), "quota"),
        (type("ResourceExhausted", (Exception,), {})("slow"), "quota"),
        (RuntimeError("504 Deadline Exceeded"), "timeout"),
        (RuntimeError("503 backend unavailable"), "unreachable"),
        (RuntimeError("something odd"), "unreachable"),
    ],
)
def test_classify_exception_buckets_transport_failures(exc, expected):
    assert degradation.classify_exception(exc) == expected
