from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.customer import Customer
from app.domain.errors import Conflict
from app.repositories.base import TenantRepository


def like_pattern(term: str) -> str:
    """Escape LIKE wildcards so a search for '50%' means the literal text."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class CustomerRepository(TenantRepository[Customer]):
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        super().__init__(session, Customer, org_id)

    async def search(
        self, *, q: str | None, is_active: bool | None, limit: int, offset: int
    ) -> tuple[list[Customer], int]:
        stmt = self.select()
        if q:
            pattern = like_pattern(q)
            stmt = stmt.where(
                or_(
                    Customer.name.ilike(pattern, escape="\\"),
                    Customer.email.ilike(pattern, escape="\\"),
                    Customer.gstin.ilike(pattern, escape="\\"),
                )
            )
        if is_active is not None:
            stmt = stmt.where(Customer.is_active.is_(is_active))
        return await self.page(
            stmt.order_by(Customer.name, Customer.id), limit=limit, offset=offset
        )

    async def create(self, **values: Any) -> Customer:
        customer = self.add(**values)
        await self._flush_unique()
        return customer

    async def flush_changes(self) -> None:
        await self._flush_unique()

    async def _flush_unique(self) -> None:
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError as exc:
            if "uq_customers_org_gstin" in str(exc.orig):
                raise Conflict("A customer with this GSTIN already exists.") from exc
            raise
