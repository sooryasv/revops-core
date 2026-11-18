from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.deal import Deal
from app.repositories.base import TenantRepository


class DealRepository(TenantRepository[Deal]):
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        super().__init__(session, Deal, org_id)

    async def search(
        self, *, stage: str | None, customer_id: uuid.UUID | None, limit: int, offset: int
    ) -> tuple[list[Deal], int]:
        stmt = self.select()
        if stage:
            stmt = stmt.where(Deal.stage == stage)
        if customer_id:
            stmt = stmt.where(Deal.customer_id == customer_id)
        return await self.page(
            stmt.order_by(Deal.created_at.desc(), Deal.id), limit=limit, offset=offset
        )
