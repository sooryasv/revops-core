"""Unit of Work: one database transaction around one use case.

    async with uow_factory() as uow:
        ... use uow.session ...
    # commit happens on clean exit, rollback on any exception

Services own the transaction boundary (not FastAPI dependencies) so the commit is guaranteed to finish
before the HTTP response is produced.
"""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker


class UnitOfWork:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory
        self._session: AsyncSession | None = None
        self._tx: AsyncSessionTransaction | None = None

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("UnitOfWork used outside 'async with'")
        return self._session

    async def __aenter__(self) -> Self:
        self._session = self._factory()
        self._tx = await self._session.begin()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._tx is not None
        assert self._session is not None
        try:
            if exc_type is None:
                await self._tx.commit()
            else:
                await self._tx.rollback()
        finally:
            await self._session.close()


UnitOfWorkFactory = Callable[[], UnitOfWork]


def make_uow_factory(factory: async_sessionmaker[AsyncSession]) -> UnitOfWorkFactory:
    def build() -> UnitOfWork:
        return UnitOfWork(factory)

    return build
