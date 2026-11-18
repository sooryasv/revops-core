"""Shared fixtures. Tests run against a real PostgreSQL (never SQLite): see 00 B10.

Environment:
  TEST_ADMIN_URL  superuser DSN used to create the throwaway database (default: local sandbox cluster)

The database is bootstrapped exactly like production: contract/roles.sql creates the three roles, migrations run
as revops_owner, and the application connects as revops_app.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import httpx
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from app.config import Settings
from app.core.clock import FixedClock
from app.main import create_app

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROLES_SQL = BACKEND_DIR.parent / "contract" / "roles.sql"
ADMIN_URL = os.environ.get("TEST_ADMIN_URL", "postgresql://postgres@127.0.0.1:5433/postgres")
TEST_DB = os.environ.get("TEST_DB_NAME", "revops_test")

# Obviously fake, test-only passwords.
OWNER_PW, APP_PW, AI_PW = "test-owner-pw", "test-app-pw", "test-ai-pw"


@dataclass(frozen=True)
class DbUrls:
    """Plain DSNs (asyncpg) and SQLAlchemy URLs for each role."""

    admin: str
    owner: str
    app: str
    ai: str

    @staticmethod
    def sa(dsn: str) -> str:
        return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)


def _dsn(user: str, password: str | None, db: str) -> str:
    host = ADMIN_URL.split("@", 1)[1].rsplit("/", 1)[0]
    auth = f"{user}:{password}" if password else user
    return f"postgresql://{auth}@{host}/{db}"


async def _psql_roles(dbname: str) -> None:
    host_port = ADMIN_URL.split("@", 1)[1].rsplit("/", 1)[0]
    host, port = host_port.split(":")
    proc = await asyncio.create_subprocess_exec(
        "psql", "-X", "-q", "-h", host, "-p", port, "-U", "postgres", "-d", dbname,
        "-v", "ON_ERROR_STOP=1", "-v", f"dbname={dbname}",
        "-v", f"owner_password={OWNER_PW}", "-v", f"app_password={APP_PW}", "-v", f"ai_password={AI_PW}",
        "-f", str(ROLES_SQL),
    )  # fmt: skip
    if await proc.wait() != 0:
        raise RuntimeError("roles.sql failed")


async def _alembic(owner_sa_url: str, *args: str) -> None:
    env = {**os.environ, "MIGRATION_DATABASE_URL": owner_sa_url}
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "alembic", *args, cwd=BACKEND_DIR, env=env
    )
    if await proc.wait() != 0:
        raise RuntimeError(f"alembic {' '.join(args)} failed")


async def create_database(name: str) -> DbUrls:
    conn = await asyncpg.connect(ADMIN_URL)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()
    await _psql_roles(name)
    return DbUrls(
        admin=_dsn("postgres", None, name),
        owner=_dsn("revops_owner", OWNER_PW, name),
        app=_dsn("revops_app", APP_PW, name),
        ai=_dsn("ai_reader", AI_PW, name),
    )


async def migrate(urls: DbUrls, *args: str) -> None:
    await _alembic(DbUrls.sa(urls.owner), *(args or ("upgrade", "head")))


@pytest_asyncio.fixture(scope="session")
async def db_urls() -> DbUrls:
    """A fresh, role-bootstrapped database migrated to head, created once per test session."""
    urls = await create_database(TEST_DB)
    await migrate(urls)
    return urls


@pytest.fixture(scope="session")
def migrated_database_url(db_urls: DbUrls) -> str:
    """SQLAlchemy URL for the runtime role (revops_app)."""
    return DbUrls.sa(db_urls.app)


@pytest.fixture
def settings(migrated_database_url: str) -> Settings:
    return Settings(_env_file=None, env="test", database_url=migrated_database_url)  # type: ignore[call-arg]


@pytest.fixture
def clock() -> FixedClock:
    """Business clock for the app under test. Starts at the real time; tests move it with `clock.set(...)`."""
    return FixedClock(datetime.now(UTC))


@pytest_asyncio.fixture
async def app(settings: Settings, clock: FixedClock) -> AsyncIterator[FastAPI]:
    application = create_app(settings, clock=clock)
    async with LifespanManager(application):
        yield application


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


# ----------------------------------------------------------------------------- multi-tenant world (M3+)

import uuid  # noqa: E402
from collections.abc import Callable  # noqa: E402

from app.core.context import (  # noqa: E402
    ROLES,
    AuthContext,
    RequestMeta,
)
from app.domain.gstin import make_test_gstin  # noqa: E402
from app.main import build_password_service  # noqa: E402
from app.schemas.users import UserCreate  # noqa: E402
from app.services.users import OnboardingService, UserService  # noqa: E402

PASSWORD = "correct-horse-battery"  # test-only credential


@dataclass
class Actor:
    id: uuid.UUID
    org_id: uuid.UUID
    email: str
    role: str


@dataclass
class Tenant:
    org_id: uuid.UUID
    name: str
    users: dict[str, Actor]  # keyed by role

    def __getitem__(self, role: str) -> Actor:
        return self.users[role]


@dataclass
class World:
    a: Tenant
    b: Tenant


async def _make_tenant(app: FastAPI, settings: Settings, label: str, state: str) -> Tenant:
    passwords = build_password_service(settings)
    uow_factory = app.state.uow_factory
    suffix = uuid.uuid4().hex[:10]
    onboarding = OnboardingService(uow_factory, passwords)
    org, admin = await onboarding.create_org(
        name=f"Org {label} {suffix}",
        legal_name=f"Org {label} {suffix} Pvt Ltd",
        state_code=state,
        gstin=make_test_gstin(state, int(suffix, 16) % 10_000),
        admin_email=f"admin.{label}.{suffix}@example.com",
        admin_name=f"Admin {label}",
        admin_password=PASSWORD,
    )
    users = {"admin": Actor(admin.id, org.id, admin.email, "admin")}
    ctx = AuthContext(admin.id, org.id, "admin")
    service = UserService(uow_factory, passwords)
    for role in ROLES:
        if role == "admin":
            continue
        created = await service.create(
            ctx,
            UserCreate(
                email=f"{role}.{label}.{suffix}@example.com",
                full_name=f"{role.title()} {label}",
                password=PASSWORD,
                role=role,
            ),
            RequestMeta(),
        )
        users[role] = Actor(created.id, org.id, created.email, role)
    return Tenant(org_id=org.id, name=org.name, users=users)


@pytest_asyncio.fixture
async def world(app: FastAPI, settings: Settings) -> World:
    """Two tenants, each with an admin, a finance and a sales user."""
    return World(
        a=await _make_tenant(app, settings, "a", "33"),
        b=await _make_tenant(app, settings, "b", "27"),
    )


ClientFactory = Callable[..., httpx.AsyncClient]


@pytest_asyncio.fixture
async def make_client(app: FastAPI) -> AsyncIterator[ClientFactory]:
    """Independent HTTP clients (each with its own cookie jar) against the same app."""
    opened: list[httpx.AsyncClient] = []

    def factory(**kwargs: object) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        c = httpx.AsyncClient(transport=transport, base_url="http://testserver", **kwargs)  # type: ignore[arg-type]
        opened.append(c)
        return c

    yield factory
    for c in opened:
        await c.aclose()


async def login_as(
    make_client: ClientFactory, actor: Actor, *, forwarded_for: str | None = None
) -> httpx.AsyncClient:
    headers = {"x-forwarded-for": forwarded_for} if forwarded_for else {}
    c = make_client(headers=headers)
    r = await c.post("/api/v1/auth/login", json={"email": actor.email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return c


@pytest_asyncio.fixture
async def sql(db_urls: DbUrls):  # type: ignore[no-untyped-def]
    """Run SQL as the database owner, to arrange states the API cannot reach yet (for example payments)."""
    conn = await asyncpg.connect(db_urls.owner)

    class Runner:
        async def execute(self, query: str, *args: object) -> None:
            await conn.execute(query, *args)

        async def fetch(self, query: str, *args: object) -> list[asyncpg.Record]:
            return await conn.fetch(query, *args)

        async def val(self, query: str, *args: object) -> object:
            return await conn.fetchval(query, *args)

    yield Runner()
    await conn.close()
