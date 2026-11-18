from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy import text

from app.domain.gstin import make_test_gstin
from tests.conftest import PASSWORD, ClientFactory, World, login_as

ORG, USERS = "/api/v1/org", "/api/v1/users"


async def test_every_role_can_read_the_org(world: World, make_client: ClientFactory) -> None:
    for role in ("admin", "finance", "sales"):
        c = await login_as(make_client, world.a[role], forwarded_for=f"10.2.0.{len(role)}")
        r = await c.get(ORG)
        assert r.status_code == 200
        assert r.json()["id"] == str(world.a.org_id)


async def test_only_admin_can_write_the_org(world: World, make_client: ClientFactory) -> None:
    finance = await login_as(make_client, world.a["finance"])
    sales = await login_as(make_client, world.a["sales"])
    assert (await finance.patch(ORG, json={"name": "Hacked"})).status_code == 403
    assert (await sales.patch(ORG, json={"name": "Hacked"})).status_code == 403


async def test_admin_updates_org_and_it_is_audited(
    world: World, make_client: ClientFactory, app: FastAPI
) -> None:
    admin = await login_as(make_client, world.a["admin"])
    r = await admin.patch(
        ORG, json={"name": "Renamed Co", "default_terms_days": 45, "udyam_category": "micro"}
    )
    assert r.status_code == 200
    assert (r.json()["name"], r.json()["default_terms_days"], r.json()["udyam_category"]) == (
        "Renamed Co",
        45,
        "micro",
    )
    async with app.state.engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT before->>'name', after->>'name' FROM audit_logs WHERE org_id=:o AND action='org.update'"
                ),
                {"o": world.a.org_id},
            )
        ).one()
    assert row[1] == "Renamed Co"
    assert row[0] != "Renamed Co"


async def test_org_validation(world: World, make_client: ClientFactory) -> None:
    admin = await login_as(make_client, world.a["admin"])
    assert (await admin.patch(ORG, json={"state_code": "99x"})).status_code == 422  # length
    assert (await admin.patch(ORG, json={"state_code": "00"})).status_code == 422  # not a GST state
    assert (await admin.patch(ORG, json={"gstin": "garbage"})).status_code == 422
    assert (await admin.patch(ORG, json={"default_terms_days": 400})).status_code == 422
    assert (await admin.patch(ORG, json={"id": "x"})).status_code == 422  # extra fields forbidden
    good = make_test_gstin("33", 77)
    assert (await admin.patch(ORG, json={"state_code": "33", "gstin": good})).status_code == 200
    # GSTIN must match the (possibly new) state prefix
    assert (await admin.patch(ORG, json={"state_code": "27"})).status_code == 422


async def test_only_admin_manages_users(world: World, make_client: ClientFactory) -> None:
    for role in ("finance", "sales"):
        c = await login_as(make_client, world.a[role], forwarded_for=f"10.3.0.{len(role)}")
        assert (await c.get(USERS)).status_code == 403
        body = {"email": "x@example.com", "full_name": "X", "password": PASSWORD, "role": "admin"}
        assert (await c.post(USERS, json=body)).status_code == 403
        assert (
            await c.patch(f"{USERS}/{world.a['admin'].id}", json={"role": "sales"})
        ).status_code == 403


async def test_admin_creates_lists_and_never_sees_password_hashes(
    world: World, make_client: ClientFactory
) -> None:
    admin = await login_as(make_client, world.a["admin"])
    body = {
        "email": "New.Person@Example.com",
        "full_name": "New Person",
        "password": PASSWORD,
        "role": "sales",
    }
    r = await admin.post(USERS, json=body)
    assert r.status_code == 201
    assert r.json()["email"] == "new.person@example.com"
    assert "password" not in r.text
    listing = await admin.get(USERS)
    assert listing.status_code == 200
    assert {u["email"] for u in listing.json()} >= {
        "new.person@example.com",
        world.a["admin"].email,
    }
    assert "hash" not in listing.text
    # and the new user can log in
    login = await make_client().post(
        "/api/v1/auth/login", json={"email": "new.person@example.com", "password": PASSWORD}
    )
    assert login.status_code == 200


async def test_duplicate_email_is_a_conflict_even_across_tenants(
    world: World, make_client: ClientFactory
) -> None:
    admin = await login_as(make_client, world.a["admin"])
    body = {
        "email": world.b["admin"].email.upper(),
        "full_name": "Dup",
        "password": PASSWORD,
        "role": "sales",
    }
    r = await admin.post(USERS, json=body)
    assert r.status_code == 409
    assert r.json()["code"] == "conflict"
    assert (
        await admin.post(USERS, json={**body, "email": "fresh@example.com"})
    ).status_code == 201  # tx still usable


async def test_weak_and_malformed_user_input_is_rejected(
    world: World, make_client: ClientFactory
) -> None:
    admin = await login_as(make_client, world.a["admin"])
    ok = {"email": "p@example.com", "full_name": "P", "password": PASSWORD, "role": "sales"}
    assert (await admin.post(USERS, json={**ok, "password": "short"})).status_code == 422
    assert (await admin.post(USERS, json={**ok, "role": "superuser"})).status_code == 422
    assert (await admin.post(USERS, json={**ok, "org_id": str(world.b.org_id)})).status_code == 422
    assert (await admin.post(USERS, json={**ok, "email": "bad"})).status_code == 422


async def test_new_user_always_lands_in_the_callers_org(
    world: World, make_client: ClientFactory, app: FastAPI
) -> None:
    admin = await login_as(make_client, world.a["admin"])
    r = await admin.post(
        USERS,
        json={"email": "land@example.com", "full_name": "L", "password": PASSWORD, "role": "sales"},
    )
    async with app.state.engine.connect() as conn:
        org = (
            await conn.execute(text("SELECT org_id FROM users WHERE id=:i"), {"i": r.json()["id"]})
        ).scalar_one()
    assert org == world.a.org_id


async def test_last_admin_cannot_be_demoted_or_deactivated(
    world: World, make_client: ClientFactory
) -> None:
    admin = await login_as(make_client, world.a["admin"])
    me = f"{USERS}/{world.a['admin'].id}"
    for patch in ({"role": "finance"}, {"is_active": False}):
        r = await admin.patch(me, json=patch)
        assert r.status_code == 409 or r.status_code == 422 or r.status_code == 400
        assert r.json()["code"] == "invalid_state"


async def test_a_second_admin_allows_demotion(world: World, make_client: ClientFactory) -> None:
    admin = await login_as(make_client, world.a["admin"])
    await admin.patch(f"{USERS}/{world.a['finance'].id}", json={"role": "admin"})
    r = await admin.patch(f"{USERS}/{world.a['admin'].id}", json={"role": "finance"})
    assert r.status_code == 200
    assert r.json()["role"] == "finance"


async def test_user_audit_rows_never_contain_password_material(
    world: World, make_client: ClientFactory, app: FastAPI
) -> None:
    admin = await login_as(make_client, world.a["admin"])
    await admin.patch(f"{USERS}/{world.a['sales'].id}", json={"password": "another-new-password"})
    async with app.state.engine.connect() as conn:
        dump = (
            await conn.execute(
                text(
                    "SELECT coalesce(string_agg(coalesce(before::text,'')||coalesce(after::text,''),' '),'') FROM audit_logs WHERE org_id=:o"
                ),
                {"o": world.a.org_id},
            )
        ).scalar_one()
    assert "another-new-password" not in dump
    assert "argon2" not in dump
    assert '"password_changed": true' in dump
