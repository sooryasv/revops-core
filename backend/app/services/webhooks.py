"""Razorpay webhook handling (03 section 8.3). One transaction per delivery; idempotent; failure leaves no half-state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.core.clock import Clock
from app.core.logging import get_logger
from app.db.uow import UnitOfWork, UnitOfWorkFactory
from app.domain import payment_rules
from app.domain.errors import FieldError, ValidationFailed, WebhookSignatureInvalid
from app.payments.events import ParsedEvent, parse_event
from app.payments.provider import PROVIDER_NAME, PaymentProvider
from app.repositories.audit import record_audit
from app.repositories.invoices import InvoiceRepository
from app.repositories.payments import PaymentRepository
from app.repositories.webhooks import (
    claim_event,
    find_order_any_tenant,
    record_failure,
    set_outcome,
)

_log = get_logger("webhooks")


@dataclass(frozen=True, slots=True)
class WebhookResult:
    status: str  # processed | ignored | quarantined | duplicate


@dataclass(frozen=True, slots=True)
class _Outcome:
    status: str
    org_id: Any = None
    reason: str | None = None


class WebhookService:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, clock: Clock, provider: PaymentProvider
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._provider = provider

    async def handle(
        self, raw_body: bytes, signature: str | None, event_id: str | None
    ) -> WebhookResult:
        # 1. Verify the signature over the raw bytes before parsing anything. Nothing is stored for a bad signature.
        if not signature or not self._provider.verify_webhook_signature(raw_body, signature):
            _log.warning("webhook_signature_invalid")  # never the body or the signature
            raise WebhookSignatureInvalid("Invalid webhook signature.")
        # 2. Parse.
        try:
            payload = json.loads(raw_body)
            text_body = raw_body.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValidationFailed("Webhook body is not valid JSON.") from exc
        if not isinstance(payload, dict) or not event_id or len(event_id) > 200:
            raise ValidationFailed(
                "Webhook is missing its event id.",
                errors=[FieldError("x-razorpay-event-id", "required")],
            )
        parsed = parse_event(payload)
        # 3. One transaction. Any unexpected exception rolls everything back, then the failure is recorded separately.
        try:
            async with self._uow_factory() as uow:
                claimed = await claim_event(
                    uow.session, provider=PROVIDER_NAME, provider_event_id=event_id,
                    event_type=parsed.event_type, payload=payload, raw_body=text_body,
                )  # fmt: skip
                if claimed is None:
                    return WebhookResult("duplicate")
                outcome = await self._apply(uow, parsed)
                await set_outcome(
                    uow.session,
                    claimed.id,
                    status=outcome.status,
                    org_id=outcome.org_id,
                    reason=outcome.reason,
                )
                return WebhookResult(outcome.status)
        except Exception as exc:
            await self._record_failure(event_id, parsed, payload, text_body, exc)
            raise  # 500: the provider retries and the failed event is picked up again

    async def _record_failure(
        self,
        event_id: str,
        parsed: ParsedEvent,
        payload: dict[str, Any],
        raw_body: str,
        exc: Exception,
    ) -> None:
        try:
            async with self._uow_factory() as uow:
                await record_failure(
                    uow.session, provider=PROVIDER_NAME, provider_event_id=event_id, event_type=parsed.event_type,
                    payload=payload, raw_body=raw_body, error=f"{type(exc).__name__}: {exc}"[:500],
                )  # fmt: skip
        except Exception as inner:
            _log.error("webhook_failure_not_recorded", exc_type=type(inner).__name__)

    @staticmethod
    def _quarantine_conflict(
        uow: UnitOfWork, org_id: Any, invoice_id: Any, event: ParsedEvent
    ) -> _Outcome:
        record_audit(
            uow.session, org_id=org_id, action="payment.quarantined", entity_type="invoice",
            entity_id=invoice_id, actor_type="webhook",
            after={"event": event.event_type, "reason": "provider_payment_id_conflict"},
        )  # fmt: skip
        return _Outcome("quarantined", org_id=org_id, reason="provider_payment_id_conflict")

    async def _apply(self, uow: UnitOfWork, event: ParsedEvent) -> _Outcome:
        if not event.handled:
            return _Outcome("ignored", reason="unhandled_event_type")
        if not event.complete or event.payment_status is None:
            return _Outcome("ignored", reason="malformed_payload")
        assert event.order_id
        assert event.payment_id
        assert event.amount_paise is not None
        order = await find_order_any_tenant(uow.session, PROVIDER_NAME, event.order_id)
        if order is None:
            return _Outcome("ignored", reason="unknown_order")
        org_id = order.org_id
        invoices = InvoiceRepository(uow.session, org_id)
        payments = PaymentRepository(uow.session, org_id)
        invoice = await invoices.get(
            order.invoice_id, for_update=True
        )  # serialises everything for this invoice

        if not payment_rules.amount_matches(order.amount_paise, event.amount_paise, event.currency):
            record_audit(
                uow.session, org_id=org_id, action="payment.quarantined", entity_type="invoice",
                entity_id=invoice.id, actor_type="webhook",
                after={"event": event.event_type, "expected_paise": order.amount_paise,
                       "received_paise": event.amount_paise, "currency": event.currency},
            )  # fmt: skip
            return _Outcome("quarantined", org_id=org_id, reason="amount_or_currency_mismatch")

        existing = await payments.by_provider_id(PROVIDER_NAME, event.payment_id)
        if existing is not None and existing.invoice_id != invoice.id:
            return self._quarantine_conflict(uow, org_id, invoice.id, event)
        new_status = payment_rules.merge_payment_status(
            existing.status if existing else None, event.payment_status
        )
        now = self._clock.now_utc()
        captured_fields: dict[str, Any] = (
            {"captured_at": now, "paid_on": self._clock.today_ist()}
            if new_status == "captured"
            else {}
        )
        if existing is None:
            created = await payments.try_add(
                invoice_id=invoice.id, payment_order_id=order.id, provider=PROVIDER_NAME,
                provider_payment_id=event.payment_id, amount_paise=event.amount_paise, status=new_status,
                method=event.method, failure_code=event.error_code if new_status == "failed" else None,
                **captured_fields,
            )  # fmt: skip
            if created is None:
                # The provider payment id already exists under another tenant (the unique key is global).
                # Retrying cannot fix that, so quarantine instead of failing forever.
                return self._quarantine_conflict(uow, org_id, invoice.id, event)
            payment = created
        else:
            payment = existing
            payment.status = new_status
            payment.method = payment.method or event.method
            if new_status == "failed" and event.error_code:
                payment.failure_code = event.error_code
        if new_status == "captured" and payment.captured_at is None:
            payment.captured_at = now
            payment.paid_on = self._clock.today_ist()
        await uow.session.flush()

        invoice.amount_paid_paise = await payments.captured_total(
            invoice.id
        )  # derived, never incremented
        if await payments.captured_for_order(order.id) >= order.amount_paise:
            order.status = "paid"
        await uow.session.flush()

        record_audit(
            uow.session, org_id=org_id, action=f"payment.{new_status}", entity_type="payment",
            entity_id=payment.id, actor_type="webhook",
            after={"event": event.event_type, "amount_paise": payment.amount_paise,
                   "amount_paid_paise": invoice.amount_paid_paise},
        )  # fmt: skip
        if payment_rules.is_overpaid(invoice.total_paise, invoice.amount_paid_paise):
            record_audit(
                uow.session, org_id=org_id, action="payment.overpaid", entity_type="invoice",
                entity_id=invoice.id, actor_type="webhook",
                after={"total_paise": invoice.total_paise, "amount_paid_paise": invoice.amount_paid_paise},
            )  # fmt: skip
        if invoice.status == "void":
            record_audit(
                uow.session, org_id=org_id, action="payment.on_void_invoice", entity_type="invoice",
                entity_id=invoice.id, actor_type="webhook", after={"amount_paid_paise": invoice.amount_paid_paise},
            )  # fmt: skip
        return _Outcome("processed", org_id=org_id)
