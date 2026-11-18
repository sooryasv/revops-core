"""Engine and session factory. The only module that knows how connections are built."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(
    url: str, *, statement_timeout_ms: int = 15_000, application_name: str = "revops-api"
) -> AsyncEngine:
    # server_settings go in the startup packet, so no `SET` statement (which cannot take bind parameters) is needed.
    return create_async_engine(
        url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
        connect_args={
            "server_settings": {
                "application_name": application_name,
                "statement_timeout": str(statement_timeout_ms),
                "timezone": "UTC",
            }
        },
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: objects stay readable after commit, which services rely on when building responses.
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def set_local_config(session: AsyncSession, name: str, value: str) -> None:
    """Set a transaction-local setting with `set_config(name, value, true)`.

    `SET LOCAL x = :param` is rejected by PostgreSQL because SET is a utility statement and cannot take bind
    parameters (07 section 1.1). `set_config` is an ordinary function call, so the value stays a bound parameter.
    Must run inside a transaction; the setting disappears at commit or rollback.
    """
    await session.execute(
        text("SELECT set_config(:name, :value, true)"), {"name": name, "value": value}
    )
