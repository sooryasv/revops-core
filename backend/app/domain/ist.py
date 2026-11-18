"""India Standard Time helpers. IST is a fixed UTC+05:30 offset with no daylight saving, so no tz database is needed."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), "IST")


def ist_date_of(instant: datetime) -> date:
    """The IST calendar date of an aware instant. Business dates (issue, due, FY) always use this."""
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("ist_date_of needs a timezone-aware datetime")
    return instant.astimezone(IST).date()
