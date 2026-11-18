"""Payment rules used by the webhook and manual-payment paths (03 sections 8.3 and 8.4)."""

from __future__ import annotations

from collections.abc import Iterable

from app.domain.errors import FieldError, InvalidState, ValidationFailed
from app.domain.money import Paise

# Status only moves up: a late "authorized" must never undo "failed" or "captured", and nothing undoes "captured".
_RANK = {"authorized": 1, "failed": 2, "captured": 3}


def merge_payment_status(existing: str | None, incoming: str) -> str:
    if existing is None:
        return incoming
    return incoming if _RANK[incoming] > _RANK[existing] else existing


def captured_total(rows: Iterable[tuple[str, Paise]]) -> Paise:
    """Balance is DERIVED from captured rows, never incremented (03 section 8.3 step g)."""
    return sum(amount for status, amount in rows if status == "captured")


def amount_matches(order_amount_paise: Paise, payment_amount_paise: Paise, currency: str) -> bool:
    # An empty currency appears in some Razorpay doc samples (SPEC_ISSUES S6); amount is always enforced.
    return order_amount_paise == payment_amount_paise and currency in ("", "INR")


def is_overpaid(total_paise: Paise, paid_paise: Paise) -> bool:
    return paid_paise > total_paise


def validate_manual_payment(status: str, *, amount_paise: Paise, amount_due_paise: Paise) -> None:
    if status != "issued":
        raise InvalidState("payments can only be recorded on issued invoices")
    if amount_paise < 1:
        raise ValidationFailed(
            "amount must be positive", errors=[FieldError("amount_paise", "must be at least 1")]
        )
    if amount_paise > amount_due_paise:
        raise ValidationFailed(
            "amount exceeds the amount due",
            errors=[FieldError("amount_paise", "exceeds amount due")],
        )
