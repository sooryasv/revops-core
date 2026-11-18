"""The unauthenticated pay page (03 section 8.2). The pay token is the only credential; unknown, expired, void and
malformed tokens all produce the same NotFound."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.clock import Clock
from app.core.paytoken import hash_pay_token, looks_like_pay_token
from app.core.rate_limit import RateLimiter
from app.db.models.invoice import Invoice, InvoiceItem
from app.db.uow import UnitOfWork, UnitOfWorkFactory
from app.domain import invoice_rules
from app.domain.errors import InvalidState, NotFound, ValidationFailed
from app.payments.provider import PROVIDER_NAME, PaymentProvider
from app.repositories.audit import record_audit
from app.repositories.invoices import InvoiceRepository
from app.repositories.organizations import OrganizationRepository
from app.repositories.payments import PaymentOrderRepository
from app.repositories.public_pay import find_invoice_by_token_hash

PUBLIC_LIMIT = "30/minute"
_NOT_FOUND = "Not found."


@dataclass(frozen=True, slots=True)
class PublicInvoice:
    supplier_name: str
    invoice: Invoice
    items: list[InvoiceItem]
    amount_due_paise: int
    payment_status: str
    can_pay: bool


@dataclass(frozen=True, slots=True)
class OrderResult:
    order_id: str
    amount_paise: int
    currency: str
    key_id: str


class PublicPayService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        provider: PaymentProvider,
        limiter: RateLimiter,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._provider = provider
        self._limiter = limiter

    async def _resolve(self, uow: UnitOfWork, token: str, ip: str | None) -> Invoice:
        self._limiter.check(PUBLIC_LIMIT, "public-pay", ip or "unknown")
        if not looks_like_pay_token(token):
            raise NotFound(_NOT_FOUND)
        invoice = await find_invoice_by_token_hash(
            uow.session, hash_pay_token(token), now=self._clock.now_utc()
        )
        if invoice is None:
            raise NotFound(_NOT_FOUND)
        return invoice

    async def view(self, token: str, ip: str | None) -> PublicInvoice:
        async with self._uow_factory() as uow:
            invoice = await self._resolve(uow, token, ip)
            org = await OrganizationRepository(uow.session, invoice.org_id).get()
            items = await InvoiceRepository(uow.session, invoice.org_id).items(invoice.id)
            due = invoice_rules.amount_due(invoice.total_paise, invoice.amount_paid_paise)
            return PublicInvoice(
                supplier_name=org.name,
                invoice=invoice,
                items=items,
                amount_due_paise=due,
                payment_status=invoice_rules.payment_status(
                    invoice.total_paise, invoice.amount_paid_paise
                ),
                can_pay=invoice.status == "issued" and due > 0,
            )

    async def create_order(self, token: str, ip: str | None) -> OrderResult:
        """Reuse the open order when the amount still matches; otherwise supersede it and create a fresh one.
        The provider call happens OUTSIDE any open database transaction (a slow provider must not hold row locks)."""
        # Transaction 1: decide what is needed.
        async with self._uow_factory() as uow:
            invoice = await self._resolve(uow, token, ip)
            orders = PaymentOrderRepository(uow.session, invoice.org_id)
            due = invoice_rules.amount_due(invoice.total_paise, invoice.amount_paid_paise)
            if invoice.status != "issued" or due <= 0:
                raise InvalidState("This invoice has nothing left to pay.")
            open_order = await orders.open_for_invoice(invoice.id)
            if open_order is not None and open_order.amount_paise == due:
                return OrderResult(
                    open_order.provider_order_id,
                    open_order.amount_paise,
                    "INR",
                    self._provider.key_id,
                )
            if open_order is not None:
                await orders.supersede_open(invoice.id)  # stale amount
            sequence = await orders.count_for_invoice(invoice.id) + 1
            org_id, invoice_id, number = invoice.org_id, invoice.id, invoice.number or ""
        # Provider call, no transaction open.
        # The receipt column is unique across ALL tenants, and every tenant numbers invoices from 1, so the tenant id leads.
        receipt = f"{org_id.hex[-8:]}-{number}-{sequence}"[:40]
        provider_order = await self._provider.create_order(
            due, receipt, {"invoice_id": str(invoice_id), "org_id": str(org_id)}
        )
        # Transaction 2: record it. If a concurrent request won the race, return the winner's order.
        async with self._uow_factory() as uow:
            orders = PaymentOrderRepository(uow.session, org_id)
            created = await orders.try_create(
                invoice_id=invoice_id, provider=PROVIDER_NAME, provider_order_id=provider_order.id,
                receipt=receipt, amount_paise=provider_order.amount_paise, status="created",
            )  # fmt: skip
            if created is None:
                winner = await orders.open_for_invoice(invoice_id)
                if winner is None:
                    raise InvalidState("Could not prepare the payment. Please try again.")
                return OrderResult(
                    winner.provider_order_id, winner.amount_paise, "INR", self._provider.key_id
                )
            record_audit(
                uow.session, org_id=org_id, action="payment.order_created", entity_type="invoice",
                entity_id=invoice_id, actor_type="public",
                after={"amount_paise": created.amount_paise, "receipt": receipt},
            )  # fmt: skip
            return OrderResult(
                created.provider_order_id, created.amount_paise, "INR", self._provider.key_id
            )

    async def confirm(
        self, token: str, ip: str | None, order_id: str, payment_id: str, signature: str
    ) -> None:
        """Checks the Checkout signature against OUR stored order id. It never marks anything paid: only a signed
        webhook can do that."""
        async with self._uow_factory() as uow:
            invoice = await self._resolve(uow, token, ip)
            order = await PaymentOrderRepository(uow.session, invoice.org_id).by_provider_order_id(
                PROVIDER_NAME, order_id
            )
            if order is None or order.invoice_id != invoice.id:
                raise ValidationFailed("The payment could not be verified.")
            if not self._provider.verify_checkout_signature(
                order.provider_order_id, payment_id, signature
            ):
                raise ValidationFailed("The payment could not be verified.")
            record_audit(
                uow.session, org_id=invoice.org_id, action="payment.confirm_submitted", entity_type="invoice",
                entity_id=invoice.id, actor_type="public", after={"payment_id": payment_id[:100]},
            )  # fmt: skip
