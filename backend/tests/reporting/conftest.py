"""A separate database seeded with the deterministic demo dataset (02 section 6), so report numbers can be checked
against ground truth computed independently from the raw rows."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

import asyncpg
import httpx
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from app.config import Settings
from app.core.passwords import make_hasher
from app.db.base import Base  # noqa: F401
from app.domain.ist import ist_date_of
from app.main import create_app
from app.seed.demo import ORGS, SeedData, generate
from app.seed.loader import load_demo
from tests.conftest import PASSWORD, DbUrls, create_database, migrate

SEED_DB = "revops_seed_test"


@dataclass
class Seeded:
    urls: DbUrls
    data: SeedData
    today: date
    org_ids: dict[str, str]  # "a" / "b" -> uuid string

    async def connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(self.urls.owner)


@pytest_asyncio.fixture(scope="session")
async def seeded() -> Seeded:
    urls = await create_database(SEED_DB)
    await migrate(urls)
    today = ist_date_of(datetime.now(UTC))
    data = generate(42, today, make_hasher(fast_for_tests=True).hash(PASSWORD))
    await load_demo(DbUrls.sa(urls.owner), data, reset=True)
    ids = {
        spec["key"]: str(row["id"])
        for spec, row in zip(ORGS, data.tables["organizations"], strict=True)
    }
    return Seeded(urls=urls, data=data, today=today, org_ids=ids)


@pytest_asyncio.fixture
async def seeded_app(seeded: Seeded) -> AsyncIterator[FastAPI]:
    settings = Settings(_env_file=None, env="test", database_url=DbUrls.sa(seeded.urls.app))  # type: ignore[call-arg]
    application = create_app(settings)
    async with LifespanManager(application):
        yield application


@pytest_asyncio.fixture
async def seeded_login(
    seeded_app: FastAPI,
) -> AsyncIterator[Callable[[str, str], Awaitable[httpx.AsyncClient]]]:
    opened: list[httpx.AsyncClient] = []
    counter = {"n": 0}

    async def login(org: str, role: str) -> httpx.AsyncClient:
        counter["n"] += 1
        transport = httpx.ASGITransport(app=seeded_app, raise_app_exceptions=False)
        client = httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"x-forwarded-for": f"10.77.{counter['n'] // 250}.{counter['n'] % 250}"},
        )
        opened.append(client)
        r = await client.post(
            "/api/v1/auth/login", json={"email": f"{role}.{org}@example.com", "password": PASSWORD}
        )
        assert r.status_code == 200, r.text
        return client

    yield login
    for c in opened:
        await c.aclose()
