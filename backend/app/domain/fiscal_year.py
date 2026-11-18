"""Indian financial year: 1 April to 31 March, labelled "26-27" (CGST Rule 46(b) numbering restarts each FY)."""

from __future__ import annotations

import re
from datetime import date, datetime

from app.domain.errors import ValidationFailed
from app.domain.ist import ist_date_of

_LABEL = re.compile(r"^(\d{2})-(\d{2})$")


def fy_start_year(d: date) -> int:
    return d.year if d.month >= 4 else d.year - 1


def fy_label_for(d: date) -> str:
    start = fy_start_year(d)
    return f"{start % 100:02d}-{(start + 1) % 100:02d}"


def fy_label_for_instant(instant: datetime) -> str:
    """FY of an aware instant, judged by the IST calendar date (not the UTC date)."""
    return fy_label_for(ist_date_of(instant))


def fiscal_year_bounds(label: str) -> tuple[date, date]:
    """First and last day of a label like "26-27" (assumes the 2000s)."""
    match = _LABEL.match(label)
    if match is None or (int(match.group(1)) + 1) % 100 != int(match.group(2)):
        raise ValidationFailed("invalid fiscal year label")
    start = 2000 + int(match.group(1))
    return date(start, 4, 1), date(start + 1, 3, 31)
