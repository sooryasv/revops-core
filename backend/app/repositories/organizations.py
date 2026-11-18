"""Organization queries. The table has no org_id column: its `id` is the tenant id."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.organization import Organization
from app.domain.errors import NotFound
from app.repositories.base import system_scope


class OrganizationRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def get(self, *, for_update: bool = False) -> Organization:
        org = await self._session.get(Organization, self._org_id, with_for_update=for_update)
        if org is None:
            raise NotFound("Not found.")
        return org


async def create_organization(session: AsyncSession, **values: Any) -> Organization:
    """Tenant creation happens before any tenant context exists (CLI only)."""
    async with system_scope(session, reason="create organization") as scoped:
        org = Organization(**values)
        scoped.add(org)
        await scoped.flush()
        return org
