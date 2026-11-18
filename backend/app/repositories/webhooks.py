"""Webhook intake. These queries are not tenant-scoped: the event arrives before we know whose it is, and a duplicate
delivery must be detected globally. Hence the `system_scope` call sites (architecture test pins this file)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.payment import PaymentOrder
from app.db.models.webhook import WebhookEvent
from app.repositories.base import system_scope

MAX_ERROR_LENGTH = 500


@dataclass(frozen=True, slots=True)
class ClaimedEvent:
    id: uuid.UUID
    attempts: int


async def claim_event(
    session: AsyncSession,
    *,
    provider: str,
    provider_event_id: str,
    event_type: str,
    payload: dict[str, Any],
    raw_body: str,
) -> ClaimedEvent | None:
    """03 section 8.3 step 3a. A new event is inserted. A previously FAILED event is re-claimed (attempts + 1).
    Anything already processed, ignored, quarantined or in flight yields no row: the caller treats it as a duplicate."""
    table = WebhookEvent
    stmt = (
        pg_insert(table)
        .values(
            provider=provider,
            provider_event_id=provider_event_id,
            event_type=event_type,
            payload=payload,
            raw_body=raw_body,
            processing_status="received",
            attempts=1,
        )
        .on_conflict_do_update(
            index_elements=["provider", "provider_event_id"],
            set_={
                "attempts": table.attempts + 1,
                "processing_status": "received",
                "last_error": None,
            },
            where=table.processing_status == "failed",
        )
        .returning(table.id, table.attempts)
    )
    async with system_scope(session, reason="webhook event intake") as scoped:
        row = (await scoped.execute(stmt)).one_or_none()
    return ClaimedEvent(row[0], row[1]) if row else None


async def set_outcome(
    session: AsyncSession,
    event_id: uuid.UUID,
    *,
    status: str,
    org_id: uuid.UUID | None,
    reason: str | None = None,
) -> None:
    async with system_scope(session, reason="webhook event outcome") as scoped:
        await scoped.execute(
            update(WebhookEvent)
            .where(WebhookEvent.id == event_id)
            .values(
                processing_status=status,
                org_id=org_id,
                processed_at=datetime.now(UTC),
                last_error=reason[:MAX_ERROR_LENGTH] if reason else None,
            )
        )


async def record_failure(
    session: AsyncSession,
    *,
    provider: str,
    provider_event_id: str,
    event_type: str,
    payload: dict[str, Any],
    raw_body: str,
    error: str,
) -> None:
    """03 section 8.3 step 4, in its own short transaction after the main one rolled back: remember that this event
    failed so the provider's retry is processed again."""
    table = WebhookEvent
    stmt = (
        pg_insert(table)
        .values(
            provider=provider,
            provider_event_id=provider_event_id,
            event_type=event_type,
            payload=payload,
            raw_body=raw_body,
            processing_status="failed",
            attempts=1,
            last_error=error[:MAX_ERROR_LENGTH],
        )
        .on_conflict_do_update(
            index_elements=["provider", "provider_event_id"],
            set_={
                "attempts": table.attempts + 1,
                "processing_status": "failed",
                "last_error": error[:MAX_ERROR_LENGTH],
            },
            where=table.processing_status.in_(["failed", "received"]),
        )
    )
    async with system_scope(session, reason="webhook failure record") as scoped:
        await scoped.execute(stmt)


async def find_order_any_tenant(
    session: AsyncSession, provider: str, provider_order_id: str
) -> PaymentOrder | None:
    """Resolve the provider's order id to our order (and therefore to its tenant)."""
    stmt = select(PaymentOrder).where(
        PaymentOrder.provider == provider, PaymentOrder.provider_order_id == provider_order_id
    )
    async with system_scope(session, reason="webhook order resolution") as scoped:
        return (await scoped.execute(stmt)).scalar_one_or_none()
