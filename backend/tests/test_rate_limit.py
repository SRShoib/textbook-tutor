"""Tests for core/rate_limit.py's in-memory fixed-window limiter. `now` is
always passed in explicitly so no test ever sleeps to cross a window."""

from datetime import datetime, timedelta, timezone

from app.core import rate_limit


def _t(offset_seconds: float = 0) -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=offset_seconds)


def test_allows_up_to_limit_then_blocks():
    key = "alice@example.com:127.0.0.1"
    for _ in range(5):
        assert not rate_limit.is_blocked(key, limit=5, window_seconds=300, now=_t())
        rate_limit.record_failure(key, window_seconds=300, now=_t())
    assert rate_limit.is_blocked(key, limit=5, window_seconds=300, now=_t())


def test_window_expiry_allows_again():
    key = "bob@example.com:127.0.0.1"
    for _ in range(5):
        rate_limit.record_failure(key, window_seconds=300, now=_t())
    assert rate_limit.is_blocked(key, limit=5, window_seconds=300, now=_t())
    # 301 seconds later, every earlier failure has aged out of the window.
    assert not rate_limit.is_blocked(key, limit=5, window_seconds=300, now=_t(301))


def test_distinct_keys_are_independent():
    for _ in range(5):
        rate_limit.record_failure("carol@example.com:127.0.0.1", window_seconds=300, now=_t())
    assert rate_limit.is_blocked("carol@example.com:127.0.0.1", limit=5, window_seconds=300, now=_t())
    assert not rate_limit.is_blocked("dave@example.com:127.0.0.1", limit=5, window_seconds=300, now=_t())


def test_clear_resets_the_counter():
    key = "erin@example.com:127.0.0.1"
    for _ in range(5):
        rate_limit.record_failure(key, window_seconds=300, now=_t())
    assert rate_limit.is_blocked(key, limit=5, window_seconds=300, now=_t())
    rate_limit.clear(key)
    assert not rate_limit.is_blocked(key, limit=5, window_seconds=300, now=_t())
