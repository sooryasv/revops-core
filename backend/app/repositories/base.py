"""Tenant isolation primitives (03 section 6.4). Lives in repositories/: the only layer that queries tenant tables.

`TenantRepository` is the only way application code reads or writes tenant tables: every query carries
`org_id = ctx.org_id`, and inserts take `org_id` from the verified context, never from a request body.
`system_scope` is the explicit, grep-able escape hatch; an architecture test pins the files allowed to call it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.core.logging import get_logger
from app.db.models.mixins import TenantMixin
from app.domain.errors import NotFound

_log = get_logger("tenancy")


class TenantRepository[T: TenantMixin]:
    def __init__(self, session: AsyncSession, model: type[T], org_id: uuid.UUID) -> None:
        self._session = session
        self._model = model
        self._org_id = org_id

    @property
    def session(self) -> AsyncSession:
        return self._session

    def select(self) -> Select[Any]:
        """Base query already filtered to this tenant. Callers add filters, ordering and paging."""
        return select(self._model).where(self._model.org_id == self._org_id)

    async def page(self, stmt: Select[Any], *, limit: int, offset: int) -> tuple[list[T], int]:
        """Run a tenant-filtered query with a total count. `stmt` must already have its ORDER BY."""
        total = (
            await self._session.execute(
                select(func.count()).select_from(stmt.order_by(None).subquery())
            )
        ).scalar_one()
        rows = await self._session.execute(stmt.limit(limit).offset(offset))
        return list(cast("list[T]", rows.scalars())), int(total)

    async def find(self, entity_id: uuid.UUID, *, for_update: bool = False) -> T | None:
        stmt = self.select().where(self._model.id == entity_id)
        if for_update:
            stmt = stmt.with_for_update()
        return cast("T | None", (await self._session.execute(stmt)).scalar_one_or_none())

    async def get(self, entity_id: uuid.UUID, *, for_update: bool = False) -> T:
        """Not found and belongs-to-another-tenant are the same answer on purpose."""
        entity = await self.find(entity_id, for_update=for_update)
        if entity is None:
            raise NotFound("Not found.")
        return entity

    def add(self, **values: Any) -> T:
        if "org_id" in values:
            raise ValueError("org_id is taken from the authenticated context, never passed in")
        model: Any = self._model
        entity: T = model(org_id=self._org_id, **values)
        self._session.add(entity)
        return entity

    async def add_unless_conflict(self, **values: Any) -> T | None:
        """INSERT ... ON CONFLICT DO NOTHING. Returns the new row, or None when ANY unique constraint already holds
        the values (a concurrent request or a duplicate). Unlike catching IntegrityError around a flush, a conflict
        here is an ordinary empty result: the surrounding transaction stays fully usable."""
        if "org_id" in values:
            raise ValueError("org_id is taken from the authenticated context, never passed in")
        model: Any = self._model
        stmt = (
            pg_insert(model)
            .values(id=values.pop("id", None) or new_id(), org_id=self._org_id, **values)
            .on_conflict_do_nothing()
            .returning(model.id)
        )
        new_row_id = (await self._session.execute(stmt)).scalar_one_or_none()
        if new_row_id is None:
            return None
        entity: T = await self._session.get(model, new_row_id)  # type: ignore[assignment]
        return entity

    async def delete(self, entity: T) -> None:
        if entity.org_id != self._org_id:
            raise NotFound("Not found.")
        await self._session.delete(entity)


@asynccontextmanager
async def system_scope(session: AsyncSession, *, reason: str) -> AsyncIterator[AsyncSession]:
    """Cross-tenant access for webhook handling, public pay-token resolution, login lookup, jobs and the CLI only.

    Yields the session unchanged; the point is that every use is named, justified and enumerable. The
    architecture test `test_system_scope_call_sites` lists the permitted files.
    """
    _log.info("system_scope", reason=reason)
    yield session


def cast_rows(rows: Any) -> list[dict[str, Any]]:
    """Turn SQLAlchemy mapping rows into plain dicts."""
    return [dict(row) for row in rows]
