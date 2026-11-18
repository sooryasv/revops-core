"""Invoice lifecycle and derived fields (03 section 4.4). Derived values are computed, never stored."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from app.domain.errors import FieldError, InvalidState, ValidationFailed
from app.domain.money import Paise

PaymentStatus = Literal["unpaid", "partially_paid", "paid"]
AgingBucket = Literal["not_due", "1-30", "31-60", "61-90", "90+"]
MAX_TERMS_DAYS = 365
MAX_VOID_REASON = 500


def amount_due(total: Paise, paid: Paise) -> Paise:
    return max(total - paid, 0)


def overpaid(total: Paise, paid: Paise) -> Paise:
    return max(paid - total, 0)


def payment_status(total: Paise, paid: Paise) -> PaymentStatus:
    if paid <= 0:
        return "unpaid"
    return "paid" if paid >= total else "partially_paid"


def compute_due_date(issue_date: date, terms_days: int) -> date:
    if not 0 <= terms_days <= MAX_TERMS_DAYS:
        raise ValidationFailed(
            "payment terms out of range",
            errors=[FieldError("payment_terms_days", f"0..{MAX_TERMS_DAYS}")],
        )
    return issue_date + timedelta(days=terms_days)


def is_overdue(status: str, due_date: date | None, amount_due_paise: Paise, *, today: date) -> bool:
    # An invoice due today is NOT overdue; it becomes overdue after midnight IST (07 section 1.4).
    return status == "issued" and due_date is not None and due_date < today and amount_due_paise > 0


def days_overdue(
    status: str, due_date: date | None, amount_due_paise: Paise, *, today: date
) -> int:
    if due_date is not None and is_overdue(status, due_date, amount_due_paise, today=today):
        return (today - due_date).days
    return 0


def aging_bucket(
    status: str, due_date: date | None, amount_due_paise: Paise, *, today: date
) -> AgingBucket:
    days = days_overdue(status, due_date, amount_due_paise, today=today)
    if days == 0:
        return "not_due"
    if days <= 30:
        return "1-30"
    if days <= 60:
        return "31-60"
    if days <= 90:
        return "61-90"
    return "90+"


def assert_editable(status: str) -> None:
    if status != "draft":
        raise InvalidState("only draft invoices can be edited")


def assert_issuable(status: str, *, item_count: int, total_paise: Paise) -> None:
    if status != "draft":
        raise InvalidState("only draft invoices can be issued")
    if item_count < 1:
        raise ValidationFailed("an invoice needs at least one line")
    if total_paise <= 0:
        raise ValidationFailed("an invoice total must be greater than zero")


def validate_void(status: str, *, paid_paise: Paise, reason: str) -> str:
    """Void is allowed only for an issued invoice with nothing paid; the number is kept (no gaps)."""
    if status != "issued":
        raise InvalidState("only issued invoices can be voided")
    if paid_paise != 0:
        raise InvalidState("an invoice with payments cannot be voided")
    cleaned = reason.strip()
    if not cleaned:
        raise ValidationFailed(
            "a void reason is required", errors=[FieldError("reason", "required")]
        )
    if len(cleaned) > MAX_VOID_REASON:
        raise ValidationFailed(
            "void reason too long", errors=[FieldError("reason", f"max {MAX_VOID_REASON}")]
        )
    return cleaned
