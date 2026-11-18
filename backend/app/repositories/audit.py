"""Audit trail writer. Rows are append-only (the runtime role has no UPDATE or DELETE on audit_logs)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import AuthContext, RequestMeta
from app.db.models.audit import AuditLog

# Never copied into audit rows, whatever the caller passes.
_SENSITIVE_KEYS = frozenset({"password", "password_hash", "token", "secret"})


def _scrub(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if data is None:
        return None
    return {k: v for k, v in data.items() if k not in _SENSITIVE_KEYS}


def record_audit(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    actor: AuthContext | None = None,
    actor_type: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    meta: RequestMeta | None = None,
) -> None:
    """Add one audit row to the caller's transaction (so it commits or rolls back with the change it describes)."""
    session.add(
        AuditLog(
            org_id=org_id,
            actor_type=actor_type or ("user" if actor is not None else "system"),
            actor_user_id=actor.user_id if actor is not None else None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before=_scrub(before),
            after=_scrub(after),
            request_id=meta.request_id if meta else None,
            ip=meta.ip if meta else None,
        )
    )
