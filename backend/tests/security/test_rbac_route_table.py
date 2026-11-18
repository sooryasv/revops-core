"""Walk the route table: every route declares a permission or is explicitly public (03 section 6.3)."""

from __future__ import annotations

import re

from fastapi import FastAPI

from app.core.permissions import ROLE_PERMISSIONS, Permission
from tests.conftest import ClientFactory, World, login_as
from tests.security.routes import application_routes, framework_route_paths


def test_every_route_declares_exactly_one_of_permission_or_public(app: FastAPI) -> None:
    undeclared = [
        f"{sorted(r.methods)} {r.path}"
        for r in application_routes(app)
        if not r.permissions and not r.is_public
    ]
    both = [
        f"{sorted(r.methods)} {r.path}"
        for r in application_routes(app)
        if r.permissions and r.is_public
    ]
    assert not undeclared, f"routes with no declared permission and not marked public: {undeclared}"
    assert not both, f"routes that are both public and permissioned: {both}"


def test_the_walker_sees_the_routes_we_know_about(app: FastAPI) -> None:
    """Guards against the walker silently matching nothing."""
    by_key = {(m, r.path): r for r in application_routes(app) for m in r.methods}
    assert by_key["GET", "/api/v1/users"].permissions == {Permission.USERS_MANAGE}
    assert by_key["PATCH", "/api/v1/org"].permissions == {Permission.ORG_WRITE}
    assert by_key["GET", "/api/v1/org"].permissions == {Permission.ORG_READ}
    assert by_key["POST", "/api/v1/auth/login"].is_public
    assert by_key["GET", "/healthz"].is_public
    assert by_key["GET", "/.well-known/jwks.json"].is_public
    assert len(by_key) >= 12


def test_public_routes_are_exactly_the_expected_set(app: FastAPI) -> None:
    """Adding a public route must be a conscious change to this list."""
    public = {r.path for r in application_routes(app) if r.is_public}
    assert public == {
        "/healthz",
        "/readyz",
        "/.well-known/jwks.json",
        "/api/v1/auth/login",
        "/api/v1/auth/token",
        "/api/v1/public/pay/{token}",
        "/api/v1/public/pay/{token}/order",
        "/api/v1/public/pay/{token}/confirm",
        "/api/v1/webhooks/razorpay",
    }


def test_only_documentation_routes_escape_the_walker(app: FastAPI) -> None:
    assert framework_route_paths(app) <= {"/openapi.json", "/docs", "/docs/oauth2-redirect"}


async def test_enforcement_matches_declaration_for_every_role(
    app: FastAPI, world: World, make_client: ClientFactory
) -> None:
    """For each permissioned route and role: 403 exactly when the matrix denies it (never a 200 for a denied role)."""
    clients = {
        role: await login_as(make_client, world.a[role], forwarded_for=f"10.4.0.{i}")
        for i, role in enumerate(("admin", "finance", "sales"))
    }
    checked = 0
    for route in application_routes(app):
        if not route.permissions or route.path.endswith(("/auth/logout", "/auth/me")):
            continue
        (permission,) = route.permissions
        path = re.sub(r"\{[a-z_]+\}", "00000000-0000-0000-0000-000000000000", route.path)
        (method,) = route.methods
        for role, client in clients.items():
            r = await client.request(method, path, json=None if method == "GET" else {})
            allowed = permission in ROLE_PERMISSIONS[role]  # type: ignore[index]
            if allowed:
                assert r.status_code != 403, f"{role} should reach {method} {path}"
            else:
                assert r.status_code == 403, (
                    f"{role} must be denied {method} {path}, got {r.status_code}"
                )
            checked += 1
    assert checked >= 15
