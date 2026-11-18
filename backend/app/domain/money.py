"""Money is an integer number of paise. There is no float and no Decimal anywhere (02 section 1)."""

from __future__ import annotations

from app.domain.errors import ValidationFailed

Paise = int


def require_int(name: str, value: object) -> int:
    """Reject floats, strings and bools: `True` is an int in Python and must never become a quantity."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationFailed(f"{name} must be a whole number")
    return value


def round_half_up_div(n: int, d: int) -> int:
    """Integer division rounding halves up, for non-negative n and positive d.

    (2n + d) // 2d equals floor(n/d + 1/2) without ever leaving integer arithmetic.
    """
    if n < 0 or d <= 0:
        raise ValueError("round_half_up_div needs non-negative n and positive d")
    return (2 * n + d) // (2 * d)
