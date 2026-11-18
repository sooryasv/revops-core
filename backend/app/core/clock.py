"""The single source of "now". Services take a Clock so tests can inject time (02 section 1)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from app.domain.ist import ist_date_of

__all__ = ["Clock", "FixedClock", "SystemClock", "ist_date_of"]


class Clock(Protocol):
    def now_utc(self) -> datetime: ...

    def today_ist(self) -> date: ...


class SystemClock:
    def now_utc(self) -> datetime:
        return datetime.now(UTC)

    def today_ist(self) -> date:
        return ist_date_of(self.now_utc())


class FixedClock:
    """A clock tests control. Starts at a fixed aware instant and only moves when told to."""

    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("FixedClock needs a timezone-aware datetime")
        self._now = instant.astimezone(UTC)

    def now_utc(self) -> datetime:
        return self._now

    def today_ist(self) -> date:
        return ist_date_of(self._now)

    def set(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("FixedClock needs a timezone-aware datetime")
        self._now = instant.astimezone(UTC)

    def advance(self, delta: timedelta) -> None:
        self._now += delta
