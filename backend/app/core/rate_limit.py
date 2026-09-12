"""
What: a hand-rolled, in-memory, fixed-window rate limiter for login attempts.
Why not a library or Redis: CLAUDE.md rules out a second database, and this
      app runs single-process on the host (docker-compose.yml: "The FastAPI
      app runs on the host, not in Compose"), so there is nothing a
      dependency like slowapi would give us over a ~20-line dict-backed
      counter — and this way there's one less dependency to explain at the
      defense. The tradeoff, stated plainly: restarting the process or
      running more than one worker resets/splits the counters. Acceptable
      for a thesis prototype; noted as a Limitation.
Why `now` is a parameter, not datetime.now() inside the function: tests
      must not sleep to cross a rate-limit window.
Why only failures are recorded: a legitimate user who mistypes a password
      twice then gets it right should not be one step closer to being locked
      out on their next visit — only wrong attempts count against the limit,
      and a success clears the slate.
"""

from __future__ import annotations

from datetime import datetime

# key -> list of failed-attempt timestamps within the current window.
_failures: dict[str, list[datetime]] = {}


def _recent(key: str, *, window_seconds: int, now: datetime) -> list[datetime]:
    cutoff = now.timestamp() - window_seconds
    return [t for t in _failures.get(key, []) if t.timestamp() > cutoff]


def is_blocked(key: str, *, limit: int, window_seconds: int, now: datetime) -> bool:
    return len(_recent(key, window_seconds=window_seconds, now=now)) >= limit


def record_failure(key: str, *, window_seconds: int, now: datetime) -> None:
    recent = _recent(key, window_seconds=window_seconds, now=now)
    recent.append(now)
    _failures[key] = recent


def clear(key: str) -> None:
    _failures.pop(key, None)


def reset_all() -> None:
    """Test-only: wipes every key's history so route tests don't bleed
    rate-limit state into each other. Production code never calls this."""
    _failures.clear()
