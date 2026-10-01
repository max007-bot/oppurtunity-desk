"""Injectable clock.

Freshness, expiry and staleness rules are central commercial behaviour, so tests
must be able to freeze time rather than sleep. Every service takes a ``Clock``
instead of calling ``datetime.now`` directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone


class Clock(ABC):
    """Source of the current instant, always timezone-aware UTC."""

    @abstractmethod
    def now(self) -> datetime:  # pragma: no cover - interface
        ...

    def now_iso(self) -> str:
        return to_iso(self.now())


class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FrozenClock(Clock):
    """Test clock. ``advance`` moves it forward explicitly."""

    def __init__(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware datetime")
        self._at = at.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._at

    def advance(self, **kwargs: float) -> datetime:
        self._at = self._at + timedelta(**kwargs)
        return self._at

    def set(self, at: datetime) -> datetime:
        self._at = at.astimezone(timezone.utc)
        return self._at


def to_iso(value: datetime) -> str:
    """Serialise to a sortable UTC ISO-8601 string for SQLite storage."""
    if value.tzinfo is None:
        raise ValueError("refusing to store a naive datetime; attach a timezone")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_iso(value: str | None) -> datetime | None:
    """Parse a stored timestamp back into an aware UTC datetime."""
    if value is None or value == "":
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age(now: datetime, then: datetime | None) -> timedelta | None:
    if then is None:
        return None
    return now - then


def is_stale(now: datetime, then: datetime | None, *, hours: float = 0, days: float = 0) -> bool:
    """True when ``then`` is missing or older than the given window.

    A missing timestamp counts as stale: the prototype fails closed rather than
    treating "never confirmed" as "confirmed recently".
    """
    if then is None:
        return True
    return (now - then) > timedelta(hours=hours, days=days)
