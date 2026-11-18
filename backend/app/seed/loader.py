"""Loads generated demo data with the OWNER connection (the runtime role cannot delete audit_logs or truncate, and
seeding is an operator action, not an application one). Refuses to run in production."""

from __future__ import annotations

from sqlalchemy import delete, insert, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.base import Base
from app.db.models import (  # noqa: F401
    audit,
    customer,
    deal,
    invoice,
    organization,
    payment,
    user,
    webhook,
)
from app.seed.demo import SeedData

# Parents before children. Deletion walks this list backwards.
LOAD_ORDER = (
    "organizations", "users", "customers", "invoice_sequences", "deals", "invoices", "invoice_items",
    "payment_orders", "payments", "webhook_events", "audit_logs",
)  # fmt: skip


async def load_demo(owner_url: str, data: SeedData, *, reset: bool) -> dict[str, int]:
    engine = create_async_engine(owner_url)
    counts: dict[str, int] = {}
    try:
        async with engine.begin() as conn:
            if reset:
                for name in reversed(LOAD_ORDER):
                    await conn.execute(delete(Base.metadata.tables[name]))
            for name in LOAD_ORDER:
                rows = data.tables.get(name, [])
                if rows:
                    await conn.execute(insert(Base.metadata.tables[name]), rows)
                counts[name] = len(rows)
            await conn.execute(text("SELECT public.refresh_collections_mv()"))
    finally:
        await engine.dispose()
    return counts
