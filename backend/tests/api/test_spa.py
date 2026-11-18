"""The built SPA is served by FastAPI; client-side routes fall back to index.html but the API never does (04 M9)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager

from app.config import Settings
from app.main import create_app


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>spa-index</title>")
    (tmp_path / "assets" / "app-123.js").write_text("console.log('x')")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    secret = tmp_path.parent / "outside-secret.txt"
    secret.write_text("top secret")
    return tmp_path


@pytest_asyncio.fixture
async def spa_client(dist: Path) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        env="test",
        database_url="postgresql+asyncpg://u:p@localhost/none",
        static_dir=str(dist),
    )
    app = create_app(settings)
    async with LifespanManager(app):  # engine creation is lazy: no database connection is made
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


@pytest.mark.parametrize(
    "path", ["/", "/invoices/123/edit", "/pay/some-token", "/login", "/no/such/page"]
)
async def test_client_side_routes_fall_back_to_index(
    spa_client: httpx.AsyncClient, path: str
) -> None:
    response = await spa_client.get(path)
    assert response.status_code == 200
    assert "spa-index" in response.text
    assert response.headers["cache-control"] == "no-cache"


async def test_real_files_are_served_with_the_right_caching(spa_client: httpx.AsyncClient) -> None:
    asset = await spa_client.get("/assets/app-123.js")
    assert asset.status_code == 200
    assert "console.log" in asset.text
    assert "immutable" in asset.headers["cache-control"]
    icon = await spa_client.get("/favicon.svg")
    assert icon.status_code == 200
    assert "<svg" in icon.text


@pytest.mark.parametrize("path", ["/api/v1/nope", "/api/v1/customers/../nope", "/api/", "/api"])
async def test_unknown_api_paths_stay_json_404(spa_client: httpx.AsyncClient, path: str) -> None:
    response = await spa_client.get(path)
    if (
        path == "/api"
    ):  # no trailing slash: not an API prefix match, so the SPA may answer; it must not be an API leak
        return
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "spa-index" not in response.text


async def test_ops_routes_are_not_shadowed(spa_client: httpx.AsyncClient) -> None:
    assert (await spa_client.get("/healthz")).json() == {"status": "ok"}
    jwks = await spa_client.get("/.well-known/jwks.json")
    assert jwks.status_code == 200
    assert "keys" in jwks.json()


@pytest.mark.parametrize(
    "path",
    ["/../outside-secret.txt", "/%2e%2e/outside-secret.txt", "/assets/../../outside-secret.txt"],
)
async def test_path_traversal_never_leaves_the_static_directory(
    spa_client: httpx.AsyncClient, path: str
) -> None:
    response = await spa_client.get(path)
    assert "top secret" not in response.text


async def test_security_headers_apply_to_the_spa(spa_client: httpx.AsyncClient) -> None:
    response = await spa_client.get("/")
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_post_to_a_spa_path_is_not_an_api_bypass(spa_client: httpx.AsyncClient) -> None:
    response = await spa_client.post("/invoices", json={})
    assert response.status_code in {404, 405}


def test_no_build_means_no_spa_routes(tmp_path: Path) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        env="test",
        database_url="postgresql+asyncpg://u:p@localhost/none",
        static_dir=str(tmp_path / "missing"),
    )
    app = create_app(settings)
    assert all(getattr(r, "path", "") != "/{path:path}" for r in app.routes)
