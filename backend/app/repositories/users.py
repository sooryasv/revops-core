"""User queries. Tenant-scoped through TenantRepository, except the login lookup (email is unique across tenants)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import User
from app.domain.errors import Conflict, NotFound
from app.repositories.base import TenantRepository, system_scope


class UserRepository(TenantRepository[User]):
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        super().__init__(session, User, org_id)

    async def list_all(self) -> list[User]:
        rows = await self.session.execute(self.select().order_by(User.created_at, User.id))
        return list(rows.scalars())

    async def require_active(self, user_id: uuid.UUID) -> User:
        """An active user of this tenant, or NotFound (a foreign or deactivated id looks the same)."""
        user = await self.find(user_id)
        if user is None or not user.is_active:
            raise NotFound("User not found.")
        return user

    async def count_active_admins(self, *, excluding: uuid.UUID | None = None) -> int:
        stmt = (
            select(func.count())
            .select_from(User)
            .where(User.org_id == self._org_id, User.role == "admin", User.is_active.is_(True))
        )
        if excluding is not None:
            stmt = stmt.where(User.id != excluding)
        return int((await self.session.execute(stmt)).scalar_one())

    async def create(self, **values: Any) -> User:
        """Insert, translating the unique-email violation to Conflict. The savepoint keeps the outer transaction usable."""
        user = self.add(**values)
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError as exc:
            if "uq_users_email_lower" in str(exc.orig):
                raise Conflict("That email address is already in use.") from exc
            raise
        return user


async def find_user_for_login(session: AsyncSession, email: str) -> User | None:
    """Login has no org prefix, so this is one of the few deliberate cross-tenant reads."""
    async with system_scope(session, reason="login lookup by globally unique email") as scoped:
        stmt = select(User).where(func.lower(User.email) == email.strip().lower()).with_for_update()
        return (await scoped.execute(stmt)).scalar_one_or_none()
