from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict

from app.config import Settings
from app.domain.errors import Conflict, FieldError, ValidationFailed
from app.main import create_app

pytestmark = pytest.mark.integration


async def test_healthz_is_ok(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_readyz_ready_when_db_up_and_at_head(client: httpx.AsyncClient) -> None:
    r = await client.get("/readyz")
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ready", "checks": {"database": "ok", "migrations": "ok"}}


async def test_readyz_reports_unreachable_database_without_details() -> None:
    bad = Settings(
        _env_file=None, env="test", database_url="postgresql+asyncpg://x:y@127.0.0.1:1/none"
    )  # type: ignore[call-arg]
    app = create_app(bad)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get("/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"] == "unreachable"
    assert "127.0.0.1" not in r.text


async def test_request_id_generated_and_echoed(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert len(r.headers["x-request-id"]) >= 8


async def test_safe_request_id_is_propagated(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz", headers={"X-Request-ID": "req-12345678"})
    assert r.headers["x-request-id"] == "req-12345678"


async def test_unsafe_request_id_is_replaced(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz", headers={"X-Request-ID": "bad id\nwith newline"})
    assert r.headers["x-request-id"] != "bad id\nwith newline"
    assert "\n" not in r.headers["x-request-id"]


async def test_unknown_route_is_problem_json(client: httpx.AsyncClient) -> None:
    r = await client.get("/nope")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["code"] == "not_found"
    assert body["status"] == 404
    assert body["errors"] == []
    assert body["request_id"] == r.headers["x-request-id"]


# ---- error mapping, exercised through a throwaway app with deliberately failing routes


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    password: str


@pytest_asyncio.fixture
async def error_client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app(settings)

    @app.post("/_t/validate")
    async def validate(body: Payload) -> dict[str, str]:
        return {"name": body.name}

    @app.get("/_t/domain-validation")
    async def domain_validation() -> None:
        raise ValidationFailed("bad", errors=[FieldError("gstin", "invalid format")])

    @app.get("/_t/conflict")
    async def conflict() -> None:
        raise Conflict("already exists")

    @app.get("/_t/boom")
    async def boom() -> None:
        raise RuntimeError("secret connection string postgresql://user:pw@host/db")

    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield c


async def test_request_validation_error_does_not_echo_input(
    error_client: httpx.AsyncClient,
) -> None:
    r = await error_client.post(
        "/_t/validate", json={"name": 5, "password": "hunter2-very-secret", "extra": 1}
    )
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    assert "hunter2-very-secret" not in r.text
    body = r.json()
    assert body["code"] == "validation_error"
    fields = {e["field"] for e in body["errors"]}
    assert {"name", "extra"} <= fields


async def test_domain_validation_error_carries_field_errors(
    error_client: httpx.AsyncClient,
) -> None:
    r = await error_client.get("/_t/domain-validation")
    assert r.status_code == 422
    assert r.json()["errors"] == [{"field": "gstin", "message": "invalid format"}]


async def test_conflict_maps_to_409(error_client: httpx.AsyncClient) -> None:
    r = await error_client.get("/_t/conflict")
    assert r.status_code == 409
    assert r.json()["code"] == "conflict"


async def test_unhandled_exception_is_generic_500_without_leaking(
    error_client: httpx.AsyncClient,
) -> None:
    r = await error_client.get("/_t/boom")
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "internal_error"
    assert "postgresql://" not in r.text
    assert "Traceback" not in r.text


async def test_method_not_allowed_is_problem_json(client: httpx.AsyncClient) -> None:
    r = await client.post("/healthz")
    assert r.status_code == 405
    assert r.json()["code"] == "method_not_allowed"
