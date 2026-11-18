"""Invoice serial numbers: INV/<FY>/<6-digit counter> (CGST Rule 46(b): consecutive, max 16 chars, [A-Za-z0-9/-])."""

from __future__ import annotations

import re

from app.domain.errors import ValidationFailed

MAX_SERIAL_LENGTH = 16
MAX_COUNTER = 999_999
_ALLOWED = re.compile(r"^[A-Za-z0-9/-]+$")


def is_valid_serial(serial: str) -> bool:
    return 0 < len(serial) <= MAX_SERIAL_LENGTH and _ALLOWED.match(serial) is not None


def format_invoice_number(fy_label: str, counter: int) -> str:
    if not 1 <= counter <= MAX_COUNTER:
        raise ValidationFailed("invoice counter out of range for this financial year")
    serial = f"INV/{fy_label}/{counter:06d}"
    if not is_valid_serial(serial):  # defensive: a bad label could break Rule 46(b)
        raise ValidationFailed("invoice number would violate the serial format rules")
    return serial
