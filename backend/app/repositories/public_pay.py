"""Pay-token resolution: the one path where an unauthenticated caller reaches a tenant row, by hash of a 256-bit secret."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.invoice import Invoice
from app.repositories.base import system_scope


async def find_invoice_by_token_hash(
    session: AsyncSession, token_hash: str, *, now: datetime
) -> Invoice | None:
    """Only an issued, unexpired invoice can be found; void, draft and expired look exactly like unknown."""
    stmt = select(Invoice).where(
        Invoice.pay_token_hash == token_hash,
        Invoice.pay_token_expires_at > now,
        Invoice.status == "issued",
    )
    async with system_scope(session, reason="public pay-token resolution") as scoped:
        return (await scoped.execute(stmt)).scalar_one_or_none()
