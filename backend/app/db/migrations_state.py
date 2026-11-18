"""Compare the database's Alembic revision with the code's head revision (used by /readyz)."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def expected_head() -> str | None:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return ScriptDirectory.from_config(config).get_current_head()


async def current_revision(engine: AsyncEngine) -> str | None:
    async with engine.connect() as conn:
        # to_regclass returns NULL (not an error) when the table is missing, so the failed-statement
        # transaction state of PostgreSQL never comes into play.
        exists = (
            await conn.execute(text("SELECT to_regclass('public.alembic_version')"))
        ).scalar_one()
        if exists is None:
            return None
        row = (await conn.execute(text("SELECT version_num FROM public.alembic_version"))).first()
        return None if row is None else str(row[0])
