"""Razorpay webhook payload parsing (field paths verified against Razorpay's webhook docs on 2026-11-01, V4 and V5)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

HANDLED_EVENTS = {
    "payment.authorized": "authorized",
    "payment.captured": "captured",
    "payment.failed": "failed",
    "order.paid": "captured",
}


@dataclass(frozen=True, slots=True)
class ParsedEvent:
    event_type: str
    handled: bool
    payment_status: str | None = (
        None  # the status this EVENT TYPE implies (not the payload's possibly later status)
    )
    order_id: str | None = None
    payment_id: str | None = None
    amount_paise: int | None = None
    currency: str = ""
    method: str | None = None
    error_code: str | None = None

    @property
    def complete(self) -> bool:
        return None not in (self.order_id, self.payment_id, self.amount_paise)


def _dig(data: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def parse_event(payload: Any) -> ParsedEvent:
    event_type = payload.get("event") if isinstance(payload, dict) else None
    if not isinstance(event_type, str) or not event_type:
        return ParsedEvent(event_type="unknown", handled=False)
    if event_type not in HANDLED_EVENTS:
        return ParsedEvent(event_type=event_type, handled=False)
    payment = _dig(payload, "payload", "payment", "entity")
    order_id = _dig(payment, "order_id") or _dig(payload, "payload", "order", "entity", "id")
    amount = _dig(payment, "amount")
    return ParsedEvent(
        event_type=event_type,
        handled=True,
        payment_status=HANDLED_EVENTS[event_type],
        order_id=order_id if isinstance(order_id, str) else None,
        payment_id=_dig(payment, "id") if isinstance(_dig(payment, "id"), str) else None,
        amount_paise=amount if isinstance(amount, int) and not isinstance(amount, bool) else None,
        currency=str(_dig(payment, "currency") or ""),
        method=_dig(payment, "method") if isinstance(_dig(payment, "method"), str) else None,
        error_code=_dig(payment, "error_code")
        if isinstance(_dig(payment, "error_code"), str)
        else None,
    )
