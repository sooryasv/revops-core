from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.customer import Customer
from app.db.models.invoice import Invoice, InvoiceItem, InvoiceSequence
from app.domain.errors import InvalidState
from app.repositories.base import TenantRepository
from app.repositories.customers import like_pattern

# Sort keys the API may request; anything else is a validation error. The values are the real columns.
SORT_COLUMNS = {
    "created_at": Invoice.created_at,
    "issue_date": Invoice.issue_date,
    "due_date": Invoice.due_date,
    "total_paise": Invoice.total_paise,
    "number": Invoice.number,
}


@dataclass(frozen=True, slots=True)
class InvoiceRow:
    invoice: Invoice
    customer_name: str
    has_written_agreement: bool


class InvoiceRepository(TenantRepository[Invoice]):
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        super().__init__(session, Invoice, org_id)

    # ------------------------------------------------------------------ items
    async def items(self, invoice_id: uuid.UUID) -> list[InvoiceItem]:
        stmt = (
            select(InvoiceItem)
            .where(InvoiceItem.org_id == self._org_id, InvoiceItem.invoice_id == invoice_id)
            .order_by(InvoiceItem.line_no)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def replace_items(
        self, invoice_id: uuid.UUID, rows: list[dict[str, Any]]
    ) -> list[InvoiceItem]:
        await self.session.execute(
            delete(InvoiceItem).where(
                InvoiceItem.org_id == self._org_id, InvoiceItem.invoice_id == invoice_id
            )
        )
        items = [InvoiceItem(org_id=self._org_id, invoice_id=invoice_id, **row) for row in rows]
        self.session.add_all(items)
        await self.session.flush()
        return items

    # ------------------------------------------------------------------ numbering
    async def next_number_value(self, fy_label: str) -> int:
        """Atomic per (org, FY) counter. Runs in the caller's transaction, so a rollback gives the number back."""
        table = InvoiceSequence.__table__
        stmt = (
            pg_insert(InvoiceSequence)
            .values(org_id=self._org_id, fy_label=fy_label, last_value=1)
            .on_conflict_do_update(
                index_elements=["org_id", "fy_label"],
                set_={"last_value": table.c.last_value + 1, "updated_at": func.now()},
            )
            .returning(InvoiceSequence.last_value)
        )
        try:
            return int((await self.session.execute(stmt)).scalar_one())
        except IntegrityError as exc:
            if "last_value_range" in str(exc.orig):
                raise InvalidState(
                    "The invoice counter for this financial year is exhausted."
                ) from exc
            raise

    # ------------------------------------------------------------------ queries
    def _row_select(self) -> Any:
        return (
            select(Invoice, Customer.name, Customer.has_written_agreement)
            .join(
                Customer,
                and_(Customer.org_id == Invoice.org_id, Customer.id == Invoice.customer_id),
            )
            .where(Invoice.org_id == self._org_id)
        )

    async def find_row(self, invoice_id: uuid.UUID) -> InvoiceRow | None:
        row = (
            await self.session.execute(self._row_select().where(Invoice.id == invoice_id))
        ).one_or_none()
        return InvoiceRow(row[0], row[1], row[2]) if row else None

    async def active_for_deal(self, deal_id: uuid.UUID) -> Invoice | None:
        stmt = self.select().where(Invoice.deal_id == deal_id, Invoice.status != "void").limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()  # type: ignore[no-any-return]

    async def search(
        self,
        *,
        status: str | None,
        payment_status: str | None,
        overdue: bool | None,
        customer_id: uuid.UUID | None,
        q: str | None,
        issue_from: date | None,
        issue_to: date | None,
        sort: str,
        today: date,
        limit: int,
        offset: int,
    ) -> tuple[list[InvoiceRow], int]:
        stmt = self._row_select()
        if status:
            stmt = stmt.where(Invoice.status == status)
        if payment_status == "unpaid":
            stmt = stmt.where(Invoice.amount_paid_paise == 0)
        elif payment_status == "partially_paid":
            stmt = stmt.where(
                Invoice.amount_paid_paise > 0, Invoice.amount_paid_paise < Invoice.total_paise
            )
        elif payment_status == "paid":
            stmt = stmt.where(
                Invoice.amount_paid_paise > 0, Invoice.amount_paid_paise >= Invoice.total_paise
            )
        if overdue is not None:
            is_overdue = and_(
                Invoice.status == "issued",
                Invoice.due_date < today,
                Invoice.total_paise - Invoice.amount_paid_paise > 0,
            )
            stmt = stmt.where(is_overdue if overdue else ~is_overdue)
        if customer_id:
            stmt = stmt.where(Invoice.customer_id == customer_id)
        if q:
            pattern = like_pattern(q)
            stmt = stmt.where(
                or_(
                    Invoice.number.ilike(pattern, escape="\\"),
                    Customer.name.ilike(pattern, escape="\\"),
                )
            )
        if issue_from:
            stmt = stmt.where(Invoice.issue_date >= issue_from)
        if issue_to:
            stmt = stmt.where(Invoice.issue_date <= issue_to)

        total = (
            await self.session.execute(
                select(func.count()).select_from(stmt.order_by(None).subquery())
            )
        ).scalar_one()
        descending = sort.startswith("-")
        column = SORT_COLUMNS[sort.lstrip("-")]
        ordered = stmt.order_by(
            column.desc().nulls_last() if descending else column.asc().nulls_last(),
            Invoice.id.desc() if descending else Invoice.id.asc(),
        )
        rows = (await self.session.execute(ordered.limit(limit).offset(offset))).all()
        return [InvoiceRow(r[0], r[1], r[2]) for r in rows], int(total)
