"""Unit of Work semantics and the set_config helper, against real PostgreSQL under asyncpg (07 sections 1.1 and 8)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.config import Settings
from app.db.session import make_engine, make_sessionmaker, set_local_config
from app.db.uow import UnitOfWork

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine(db_urls):  # type: ignore[no-untyped-def]
    # Owner role: these tests create scratch tables, which the runtime role may not do.
    eng = make_engine(db_urls.sa(db_urls.owner))
    yield eng
    await eng.dispose()


@pytest.fixture
def factory(engine: AsyncEngine) -> async_sessionmaker:  # type: ignore[type-arg]
    return make_sessionmaker(engine)


async def test_set_local_config_works_with_bound_parameter(factory: async_sessionmaker) -> None:  # type: ignore[type-arg]
    org = str(uuid.uuid4())
    async with UnitOfWork(factory) as uow:
        await set_local_config(uow.session, "app.current_org", org)
        got = (
            await uow.session.execute(text("SELECT current_setting('app.current_org', true)"))
        ).scalar_one()
    assert got == org


async def test_set_local_config_is_transaction_local(factory: async_sessionmaker) -> None:  # type: ignore[type-arg]
    async with UnitOfWork(factory) as uow:
        await set_local_config(uow.session, "app.current_org", str(uuid.uuid4()))
    async with UnitOfWork(factory) as uow:
        got = (
            await uow.session.execute(
                text("SELECT nullif(current_setting('app.current_org', true), '')")
            )
        ).scalar_one()
    assert got is None


async def test_plain_set_local_with_bind_parameter_is_rejected(factory: async_sessionmaker) -> None:  # type: ignore[type-arg]
    """Documents WHY the helper exists: the naive form fails under asyncpg."""

    async def naive() -> None:
        async with UnitOfWork(factory) as uow:
            await uow.session.execute(
                text("SET LOCAL app.current_org = :org"), {"org": str(uuid.uuid4())}
            )

    with pytest.raises(Exception, match=r"syntax error|\$1"):
        await naive()


async def test_uow_commits_on_clean_exit(factory: async_sessionmaker) -> None:  # type: ignore[type-arg]
    name = f"t_commit_{uuid.uuid4().hex[:8]}"
    async with UnitOfWork(factory) as uow:
        await uow.session.execute(text(f"CREATE TABLE {name} (id int)"))
        await uow.session.execute(text(f"INSERT INTO {name} VALUES (1)"))
    async with UnitOfWork(factory) as uow:
        count = (await uow.session.execute(text(f"SELECT count(*) FROM {name}"))).scalar_one()
        await uow.session.execute(text(f"DROP TABLE {name}"))
    assert count == 1


async def test_uow_rolls_back_on_exception(factory: async_sessionmaker) -> None:  # type: ignore[type-arg]
    name = f"t_rollback_{uuid.uuid4().hex[:8]}"

    async def failing() -> None:
        async with UnitOfWork(factory) as uow:
            await uow.session.execute(text(f"CREATE TABLE {name} (id int)"))
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await failing()
    async with UnitOfWork(factory) as uow:
        exists = (
            await uow.session.execute(text("SELECT to_regclass(:n)"), {"n": name})
        ).scalar_one()
    assert exists is None


async def test_uow_session_unavailable_outside_context(factory: async_sessionmaker) -> None:  # type: ignore[type-arg]
    with pytest.raises(RuntimeError, match="outside"):
        _ = UnitOfWork(factory).session


async def test_statement_timeout_comes_from_engine_settings(settings: Settings) -> None:
    eng = make_engine(settings.database_url, statement_timeout_ms=1234)
    try:
        async with make_sessionmaker(eng)() as s:
            got = (await s.execute(text("SHOW statement_timeout"))).scalar_one()
    finally:
        await eng.dispose()
    assert got == "1234ms"
