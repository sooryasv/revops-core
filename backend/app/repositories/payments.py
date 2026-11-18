from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.payment import Payment, PaymentOrder
from app.repositories.base import TenantRepository


class PaymentRepository(TenantRepository[Payment]):
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        super().__init__(session, Payment, org_id)

    async def by_provider_id(self, provider: str, provider_payment_id: str) -> Payment | None:
        stmt = self.select().where(
            Payment.provider == provider, Payment.provider_payment_id == provider_payment_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()  # type: ignore[no-any-return]

    async def try_add(self, **values: Any) -> Payment | None:
        """Insert a payment; None when its (provider, provider_payment_id) already exists (it is globally unique)."""
        return await self.add_unless_conflict(**values)

    async def for_invoice(self, invoice_id: uuid.UUID) -> list[Payment]:
        stmt = (
            self.select()
            .where(Payment.invoice_id == invoice_id)
            .order_by(Payment.created_at, Payment.id)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def captured_total(self, invoice_id: uuid.UUID) -> int:
        """The balance is DERIVED from captured rows, never incremented (03 section 8.3 step g)."""
        stmt = select(func.coalesce(func.sum(Payment.amount_paise), 0)).where(
            Payment.org_id == self._org_id,
            Payment.invoice_id == invoice_id,
            Payment.status == "captured",
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def captured_for_order(self, order_id: uuid.UUID) -> int:
        stmt = select(func.coalesce(func.sum(Payment.amount_paise), 0)).where(
            Payment.org_id == self._org_id,
            Payment.payment_order_id == order_id,
            Payment.status == "captured",
        )
        return int((await self.session.execute(stmt)).scalar_one())


class PaymentOrderRepository(TenantRepository[PaymentOrder]):
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        super().__init__(session, PaymentOrder, org_id)

    async def open_for_invoice(self, invoice_id: uuid.UUID) -> PaymentOrder | None:
        stmt = self.select().where(
            PaymentOrder.invoice_id == invoice_id, PaymentOrder.status == "created"
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()  # type: ignore[no-any-return]

    async def by_provider_order_id(
        self, provider: str, provider_order_id: str
    ) -> PaymentOrder | None:
        stmt = self.select().where(
            PaymentOrder.provider == provider, PaymentOrder.provider_order_id == provider_order_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()  # type: ignore[no-any-return]

    async def count_for_invoice(self, invoice_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(PaymentOrder)
            .where(PaymentOrder.org_id == self._org_id, PaymentOrder.invoice_id == invoice_id)
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def supersede_open(self, invoice_id: uuid.UUID) -> None:
        order = await self.open_for_invoice(invoice_id)
        if order is not None:
            order.status = "superseded"
            await self.session.flush()

    async def try_create(self, **values: Any) -> PaymentOrder | None:
        """Insert an order; None when a concurrent request already created the open one (the partial unique index
        or the receipt uniqueness decided the race)."""
        return await self.add_unless_conflict(**values)
