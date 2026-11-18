"""MSMED Act s.15 payment-period helper. Informational only; there is no tax-law computation anywhere (07 section 2)."""

from __future__ import annotations


def statutory_limit_days(udyam_category: str, has_written_agreement: bool) -> int | None:
    """45 days with a written agreement, 15 without; applies only to micro and small suppliers."""
    if udyam_category not in ("micro", "small"):
        return None
    return 45 if has_written_agreement else 15


def terms_exceed_statutory(limit_days: int | None, terms_days: int) -> bool:
    return limit_days is not None and terms_days > limit_days
