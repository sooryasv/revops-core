"""Manual payments and payment listing for authenticated users (03 section 8.4)."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.clock import Clock
from app.core.context import AuthContext, RequestMeta
from app.core.ids import new_id
from app.db.models.payment import Payment
from app.db.uow import UnitOfWorkFactory
from app.domain import invoice_rules, payment_rules
from app.domain.errors import FieldError, ValidationFailed
from app.repositories.audit import record_audit
from app.repositories.invoices import InvoiceRepository
from app.repositories.payments import PaymentOrderRepository, PaymentRepository
from app.schemas.payments import ManualPaymentIn


class PaymentService:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def list_for_invoice(self, ctx: AuthContext, invoice_id: uuid.UUID) -> list[Payment]:
        async with self._uow_factory() as uow:
            await InvoiceRepository(uow.session, ctx.org_id).get(
                invoice_id
            )  # 404 for foreign or missing
            return await PaymentRepository(uow.session, ctx.org_id).for_invoice(invoice_id)

    async def record_manual(
        self, ctx: AuthContext, invoice_id: uuid.UUID, data: ManualPaymentIn, meta: RequestMeta
    ) -> Payment:
        async with self._uow_factory() as uow:
            invoices = InvoiceRepository(uow.session, ctx.org_id)
            payments = PaymentRepository(uow.session, ctx.org_id)
            invoice = await invoices.get(
                invoice_id, for_update=True
            )  # serialises with webhooks on this invoice
            due = invoice_rules.amount_due(invoice.total_paise, invoice.amount_paid_paise)
            payment_rules.validate_manual_payment(
                invoice.status, amount_paise=data.amount_paise, amount_due_paise=due
            )
            today = self._clock.today_ist()
            if data.paid_on > today or (
                invoice.issue_date is not None and data.paid_on < invoice.issue_date
            ):
                raise ValidationFailed(
                    "paid_on must be between the issue date and today",
                    errors=[FieldError("paid_on", "outside the issue date to today range")],
                )
            payment = payments.add(
                invoice_id=invoice.id,
                provider="manual",
                provider_payment_id=f"manual_{new_id().hex}",
                amount_paise=data.amount_paise,
                status="captured",
                method=data.method,
                captured_at=self._clock.now_utc(),
                paid_on=data.paid_on,
                recorded_by_user_id=ctx.user_id,
                reference=data.reference,
            )
            await uow.session.flush()
            invoice.amount_paid_paise = await payments.captured_total(
                invoice.id
            )  # derived, not incremented
            # An open checkout order was priced before this payment; it is stale now.
            await PaymentOrderRepository(uow.session, ctx.org_id).supersede_open(invoice.id)
            await uow.session.flush()
            await uow.session.refresh(payment)
            audit_after: dict[str, Any] = {
                "amount_paise": payment.amount_paise,
                "method": payment.method,
                "paid_on": data.paid_on.isoformat(),
                "amount_paid_paise": invoice.amount_paid_paise,
            }
            record_audit(
                uow.session, org_id=ctx.org_id, action="payment.manual", entity_type="payment",
                entity_id=payment.id, actor=ctx, after=audit_after, meta=meta,
            )  # fmt: skip
            return payment
