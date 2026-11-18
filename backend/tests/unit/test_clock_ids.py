from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.clock import FixedClock, SystemClock, ist_date_of
from app.core.ids import new_id


def test_ist_date_of_uses_ist_calendar_not_utc() -> None:
    # 18:30 UTC is exactly midnight IST: the calendar day flips.
    assert ist_date_of(datetime(2026, 3, 31, 18, 29, 59, tzinfo=UTC)) == date(2026, 3, 31)
    assert ist_date_of(datetime(2026, 3, 31, 18, 30, 0, tzinfo=UTC)) == date(2026, 4, 1)


def test_ist_date_of_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="timezone"):
        ist_date_of(datetime(2026, 1, 1))


def test_fixed_clock_returns_injected_time() -> None:
    clock = FixedClock(datetime(2026, 11, 1, 20, 0, tzinfo=UTC))
    assert clock.now_utc() == datetime(2026, 11, 1, 20, 0, tzinfo=UTC)
    assert clock.today_ist() == date(2026, 11, 2)


def test_fixed_clock_can_advance() -> None:
    clock = FixedClock(datetime(2026, 11, 1, 0, 0, tzinfo=UTC))
    clock.advance(timedelta(days=2))
    assert clock.today_ist() == date(2026, 11, 3)


def test_fixed_clock_requires_aware_datetime() -> None:
    with pytest.raises(ValueError, match="timezone"):
        FixedClock(datetime(2026, 1, 1))


def test_system_clock_is_utc_aware() -> None:
    now = SystemClock().now_utc()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_new_id_is_unique_and_time_ordered() -> None:
    ids = [new_id() for _ in range(50)]
    assert len(set(ids)) == 50
    assert all(i.version == 7 for i in ids)
    assert [i.int >> 80 for i in ids] == sorted(
        i.int >> 80 for i in ids
    )  # 48-bit ms timestamp prefix
