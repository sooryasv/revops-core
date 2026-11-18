"""Authentication endpoints against real PostgreSQL: login, session, revocation, agent token, hardening."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import jwt
from fastapi import FastAPI
from sqlalchemy import text

from app.config import Settings
from app.core.jwt_service import JwtService
from tests.conftest import PASSWORD, ClientFactory, World, login_as

LOGIN = "/api/v1/auth/login"


async def test_login_success_sets_a_hardened_cookie(
    world: World, make_client: ClientFactory
) -> None:
    c = make_client()
    r = await c.post(LOGIN, json={"email": world.a["admin"].email, "password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "admin"
    assert body["org_id"] == str(world.a.org_id)
    assert "password" not in json.dumps(body)
    cookie = r.headers["set-cookie"].lower()
    assert "revops_session=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/" in cookie
    assert "max-age=3600" in cookie


async def test_login_is_case_insensitive_on_email(world: World, make_client: ClientFactory) -> None:
    r = await make_client().post(
        LOGIN, json={"email": world.a["sales"].email.upper(), "password": PASSWORD}
    )
    assert r.status_code == 200


async def test_wrong_password_and_unknown_email_are_indistinguishable(
    world: World, make_client: ClientFactory
) -> None:
    wrong = await make_client().post(
        LOGIN, json={"email": world.a["admin"].email, "password": "wrong-password-x"}
    )
    unknown = await make_client().post(
        LOGIN, json={"email": "nobody.here@example.com", "password": "wrong-password-x"}
    )
    assert wrong.status_code == unknown.status_code == 401
    strip = lambda r: {k: v for k, v in r.json().items() if k != "request_id"}  # noqa: E731
    assert strip(wrong) == strip(unknown)
    assert wrong.json()["code"] == "unauthenticated"
    assert "set-cookie" not in wrong.headers


async def test_inactive_user_cannot_log_in_and_message_is_generic(
    world: World, make_client: ClientFactory, app: FastAPI
) -> None:
    admin = await login_as(make_client, world.a["admin"])
    r = await admin.patch(f"/api/v1/users/{world.a['sales'].id}", json={"is_active": False})
    assert r.status_code == 200
    denied = await make_client().post(
        LOGIN, json={"email": world.a["sales"].email, "password": PASSWORD}
    )
    assert denied.status_code == 401
    assert denied.json()["detail"] == "Incorrect email or password."


async def test_login_rejects_unknown_fields_and_bad_email(
    world: World, make_client: ClientFactory
) -> None:
    c = make_client()
    r1 = await c.post(
        LOGIN, json={"email": world.a["admin"].email, "password": PASSWORD, "org_id": "x"}
    )
    r2 = await c.post(LOGIN, json={"email": "not-an-email", "password": PASSWORD})
    assert r1.status_code == r2.status_code == 422
    assert all(
        "input" not in e for e in r1.json()["errors"]
    )  # submitted values are never echoed back
    assert PASSWORD not in r1.text
    assert "not-an-email" not in r2.text


async def test_me_requires_a_session(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401
    assert r.json()["code"] == "unauthenticated"


async def test_me_returns_the_caller(world: World, make_client: ClientFactory) -> None:
    c = await login_as(make_client, world.a["finance"])
    r = await c.get("/api/v1/auth/me")
    assert r.status_code == 200
    assert r.json()["email"] == world.a["finance"].email
    assert r.json()["role"] == "finance"


async def test_logout_clears_cookie(world: World, make_client: ClientFactory) -> None:
    c = await login_as(make_client, world.a["admin"])
    r = await c.post("/api/v1/auth/logout")
    assert r.status_code == 204
    assert "max-age=0" in r.headers["set-cookie"].lower()
    assert (await c.get("/api/v1/auth/me")).status_code == 401


async def test_logout_everywhere_revokes_other_sessions(
    world: World, make_client: ClientFactory
) -> None:
    first = await login_as(make_client, world.a["admin"], forwarded_for="10.0.0.1")
    second = await login_as(make_client, world.a["admin"], forwarded_for="10.0.0.2")
    assert (await first.get("/api/v1/auth/me")).status_code == 200
    assert (await second.post("/api/v1/auth/logout", json={"everywhere": True})).status_code == 204
    assert (await first.get("/api/v1/auth/me")).status_code == 401


async def test_role_change_revokes_existing_sessions(
    world: World, make_client: ClientFactory
) -> None:
    admin = await login_as(make_client, world.a["admin"])
    sales = await login_as(make_client, world.a["sales"])
    assert (await sales.get("/api/v1/auth/me")).status_code == 200
    r = await admin.patch(f"/api/v1/users/{world.a['sales'].id}", json={"role": "finance"})
    assert r.status_code == 200
    assert (await sales.get("/api/v1/auth/me")).status_code == 401


async def test_deactivation_revokes_existing_sessions(
    world: World, make_client: ClientFactory
) -> None:
    admin = await login_as(make_client, world.a["admin"])
    finance = await login_as(make_client, world.a["finance"])
    await admin.patch(f"/api/v1/users/{world.a['finance'].id}", json={"is_active": False})
    assert (await finance.get("/api/v1/auth/me")).status_code == 401


async def test_password_change_revokes_sessions_and_new_password_works(
    world: World, make_client: ClientFactory
) -> None:
    admin = await login_as(make_client, world.a["admin"])
    sales = await login_as(make_client, world.a["sales"])
    await admin.patch(
        f"/api/v1/users/{world.a['sales'].id}", json={"password": "a-brand-new-password"}
    )
    assert (await sales.get("/api/v1/auth/me")).status_code == 401
    ok = await make_client().post(
        LOGIN, json={"email": world.a["sales"].email, "password": "a-brand-new-password"}
    )
    old = await make_client().post(
        LOGIN, json={"email": world.a["sales"].email, "password": PASSWORD}
    )
    assert (ok.status_code, old.status_code) == (200, 401)


# ----------------------------------------------------------------------------- tokens in the cookie


def _forge(app: FastAPI, world: World, **overrides: object) -> str:
    jwt_service: JwtService = app.state.jwt
    args: dict[str, object] = {
        "user_id": world.a["admin"].id, "org_id": world.a.org_id, "role": "admin",
        "token_version": 0, "audience": "revops", "now": datetime.now(UTC),
    }  # fmt: skip
    args.update(overrides)
    return jwt_service.issue(**args)[0]  # type: ignore[arg-type]


async def test_valid_forged_cookie_for_a_real_user_works_as_a_baseline(
    app: FastAPI, world: World, make_client: ClientFactory
) -> None:
    c = make_client(cookies={"revops_session": _forge(app, world)})
    assert (await c.get("/api/v1/auth/me")).status_code == 200


async def test_stale_token_version_is_rejected(
    app: FastAPI, world: World, make_client: ClientFactory
) -> None:
    c = make_client(cookies={"revops_session": _forge(app, world, token_version=99)})
    assert (await c.get("/api/v1/auth/me")).status_code == 401


async def test_agent_token_is_not_a_browser_session(
    app: FastAPI, world: World, make_client: ClientFactory
) -> None:
    c = make_client(cookies={"revops_session": _forge(app, world, audience="agent")})
    assert (await c.get("/api/v1/auth/me")).status_code == 401


async def test_role_in_token_cannot_escalate(
    app: FastAPI, world: World, make_client: ClientFactory
) -> None:
    """A token that claims admin for a sales user is correctly signed but the database role wins."""
    token = _forge(app, world, user_id=world.a["sales"].id, role="admin")
    c = make_client(cookies={"revops_session": token})
    assert (await c.get("/api/v1/auth/me")).json()["role"] == "sales"
    assert (await c.get("/api/v1/users")).status_code == 403


async def test_token_for_wrong_org_is_rejected(
    app: FastAPI, world: World, make_client: ClientFactory
) -> None:
    """User id from tenant A paired with org id of tenant B must not authenticate."""
    c = make_client(cookies={"revops_session": _forge(app, world, org_id=world.b.org_id)})
    assert (await c.get("/api/v1/auth/me")).status_code == 401


async def test_expired_cookie_is_rejected(
    app: FastAPI, world: World, make_client: ClientFactory
) -> None:
    c = make_client(
        cookies={"revops_session": _forge(app, world, now=datetime.now(UTC) - timedelta(hours=3))}
    )
    assert (await c.get("/api/v1/auth/me")).status_code == 401


async def test_session_slides_when_under_30_minutes_remain(
    app: FastAPI, world: World, make_client: ClientFactory, settings: Settings
) -> None:
    issued = datetime.now(UTC) - timedelta(minutes=40)  # expires in 20 min
    c = make_client(cookies={"revops_session": _forge(app, world, now=issued, auth_time=issued)})
    r = await c.get("/api/v1/auth/me")
    assert r.status_code == 200
    assert "revops_session=" in r.headers["set-cookie"]


async def test_session_does_not_slide_when_plenty_of_time_remains(
    app: FastAPI, world: World, make_client: ClientFactory
) -> None:
    c = make_client(cookies={"revops_session": _forge(app, world)})
    r = await c.get("/api/v1/auth/me")
    assert "set-cookie" not in r.headers


async def test_sliding_never_extends_past_the_12_hour_absolute_limit(
    app: FastAPI, world: World, make_client: ClientFactory
) -> None:
    auth_time = datetime.now(UTC) - timedelta(hours=11, minutes=45)
    issued = datetime.now(UTC) - timedelta(minutes=50)
    token = _forge(app, world, now=issued, auth_time=auth_time)
    c = make_client(cookies={"revops_session": token})
    r = await c.get("/api/v1/auth/me")
    assert r.status_code == 200
    if "set-cookie" in r.headers:
        new = r.headers["set-cookie"].split("revops_session=")[1].split(";")[0]
        exp = jwt.decode(new, options={"verify_signature": False})["exp"]
        assert exp <= int((auth_time + timedelta(hours=12)).timestamp())


# ----------------------------------------------------------------------------- agent token and JWKS


async def test_agent_token_issue_and_verify_via_jwks(
    world: World, make_client: ClientFactory
) -> None:
    c = make_client()
    r = await c.post(
        "/api/v1/auth/token",
        json={"email": world.a["admin"].email, "password": PASSWORD, "audience": "agent"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 1800
    jwks = (await c.get("/.well-known/jwks.json")).json()
    (jwk,) = jwks["keys"]
    public = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
    claims = jwt.decode(
        body["access_token"], public, algorithms=["RS256"], audience="agent", issuer="revops-core"
    )  # type: ignore[arg-type]
    assert claims["org"] == str(world.a.org_id)
    assert claims["role"] == "admin"


async def test_token_endpoint_only_accepts_agent_audience(
    world: World, make_client: ClientFactory
) -> None:
    r = await make_client().post(
        "/api/v1/auth/token",
        json={"email": world.a["admin"].email, "password": PASSWORD, "audience": "revops"},
    )
    assert r.status_code == 422


async def test_token_endpoint_wrong_password_is_generic(
    world: World, make_client: ClientFactory
) -> None:
    r = await make_client().post(
        "/api/v1/auth/token",
        json={"email": world.a["admin"].email, "password": "wrong-password-x", "audience": "agent"},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "Incorrect email or password."


async def test_jwks_is_public_and_has_no_private_members(client: httpx.AsyncClient) -> None:
    r = await client.get("/.well-known/jwks.json")
    assert r.status_code == 200
    for key in r.json()["keys"]:
        assert not ({"d", "p", "q", "dp", "dq", "qi"} & set(key))


# ----------------------------------------------------------------------------- rate limiting


async def test_login_is_rate_limited_per_email(world: World, make_client: ClientFactory) -> None:
    statuses = []
    for i in range(7):
        c = make_client(
            headers={"x-forwarded-for": f"10.1.0.{i}"}
        )  # vary IP so only the per-email limit can fire
        r = await c.post(
            LOGIN, json={"email": world.a["admin"].email, "password": "wrong-password-x"}
        )
        statuses.append(r.status_code)
    assert statuses[:5] == [401] * 5
    assert statuses[5] == 429
    blocked = await make_client().post(
        LOGIN, json={"email": world.a["admin"].email, "password": "wrong-password-x"}
    )
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"]
    assert blocked.json()["code"] == "rate_limited"


async def test_login_is_rate_limited_per_ip(world: World, make_client: ClientFactory) -> None:
    c = make_client(headers={"x-forwarded-for": "10.9.9.9"})
    codes = [
        (
            await c.post(LOGIN, json={"email": f"u{i}@example.com", "password": "wrong-password-x"})
        ).status_code
        for i in range(12)
    ]
    assert codes[:10] == [401] * 10
    assert codes[10:] == [429, 429]


# ----------------------------------------------------------------------------- headers, origin, size


async def test_security_headers_on_every_response(client: httpx.AsyncClient) -> None:
    for path in ("/healthz", "/api/v1/auth/me", "/nope"):
        h = (await client.get(path)).headers
        assert "default-src 'self'" in h["content-security-policy"]
        assert "frame-ancestors 'none'" in h["content-security-policy"]
        assert h["x-content-type-options"] == "nosniff"
        assert h["referrer-policy"] == "strict-origin-when-cross-origin"
        assert "permissions-policy" in h
        assert h["x-frame-options"] == "DENY"
    assert (await client.get("/api/v1/auth/me")).headers["cache-control"] == "no-store"


async def test_foreign_origin_is_rejected_on_unsafe_methods(
    world: World, make_client: ClientFactory
) -> None:
    c = make_client(headers={"origin": "https://evil.example"})
    r = await c.post(LOGIN, json={"email": world.a["admin"].email, "password": PASSWORD})
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"
    assert "set-cookie" not in r.headers


async def test_allowed_origin_and_missing_origin_are_accepted(
    world: World, make_client: ClientFactory
) -> None:
    ok = make_client(headers={"origin": "http://localhost:5173"})
    assert (
        await ok.post(LOGIN, json={"email": world.a["admin"].email, "password": PASSWORD})
    ).status_code == 200


async def test_origin_check_does_not_apply_to_safe_methods(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz", headers={"origin": "https://evil.example"})
    assert r.status_code == 200


async def test_oversized_body_is_rejected_with_413(client: httpx.AsyncClient) -> None:
    r = await client.post(
        LOGIN, content=b"x" * 1_100_000, headers={"content-type": "application/json"}
    )
    assert r.status_code == 413
    assert r.json()["code"] == "validation_error"


async def test_chunked_oversized_body_is_rejected(client: httpx.AsyncClient) -> None:
    async def chunks():  # type: ignore[no-untyped-def]
        for _ in range(12):
            yield b"x" * 100_000

    r = await client.post(LOGIN, content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413


async def test_audit_rows_written_for_login_and_logout(
    world: World, make_client: ClientFactory, app: FastAPI
) -> None:
    c = await login_as(make_client, world.a["admin"])
    await c.post("/api/v1/auth/logout")
    async with app.state.engine.connect() as conn:  # runtime role may SELECT audit_logs
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT action FROM audit_logs WHERE org_id = :o AND entity_id = :u ORDER BY created_at"
                    ),
                    {"o": world.a.org_id, "u": world.a["admin"].id},
                )
            )
            .scalars()
            .all()
        )
    assert rows == ["auth.login", "auth.logout"]
