from __future__ import annotations

import uuid

import httpx
from fastapi import FastAPI
from sqlalchemy import text

from app.domain.gstin import make_test_gstin
from tests.conftest import ClientFactory, World, login_as

C = "/api/v1/customers"


def body(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {"name": f"Buyer {uuid.uuid4().hex[:6]}", "state_code": "33"}
    base.update(kw)
    return base


async def test_create_get_and_defaults_from_org(world: World, make_client: ClientFactory) -> None:
    sales = await login_as(make_client, world.a["sales"])
    r = await sales.post(C, json=body(name="Acme Traders", email="Buyer@Example.com"))
    assert r.status_code == 201
    data = r.json()
    assert data["email"] == "buyer@example.com"
    assert data["default_terms_days"] == 30  # inherited from the organization
    assert data["is_active"] is True
    got = await sales.get(f"{C}/{data['id']}")
    assert got.status_code == 200
    assert got.json()["name"] == "Acme Traders"


async def test_explicit_terms_override_org_default(
    world: World, make_client: ClientFactory
) -> None:
    c = await login_as(make_client, world.a["finance"])
    assert (await c.post(C, json=body(default_terms_days=60))).json()["default_terms_days"] == 60


async def test_gstin_must_match_state_and_format(world: World, make_client: ClientFactory) -> None:
    c = await login_as(make_client, world.a["admin"])
    good = make_test_gstin("27", 11)
    assert (await c.post(C, json=body(state_code="27", gstin=good))).status_code == 201
    assert (
        await c.post(C, json=body(state_code="33", gstin=good))
    ).status_code == 422  # prefix says 27
    assert (await c.post(C, json=body(gstin="NOTAGSTIN"))).status_code == 422
    assert (await c.post(C, json=body(state_code="00"))).status_code == 422
    assert (await c.post(C, json=body(state_code="3"))).status_code == 422


async def test_gstin_is_normalised_to_upper_case(world: World, make_client: ClientFactory) -> None:
    c = await login_as(make_client, world.a["admin"])
    gstin = make_test_gstin("33", 12)
    r = await c.post(C, json=body(gstin=gstin.lower()))
    assert r.json()["gstin"] == gstin


async def test_duplicate_gstin_is_a_conflict_within_a_tenant_but_allowed_across_tenants(
    world: World, make_client: ClientFactory
) -> None:
    a = await login_as(make_client, world.a["admin"])
    b = await login_as(make_client, world.b["admin"])
    gstin = make_test_gstin("33", 13)
    assert (await a.post(C, json=body(gstin=gstin))).status_code == 201
    dup = await a.post(C, json=body(gstin=gstin))
    assert dup.status_code == 409
    assert dup.json()["code"] == "conflict"
    assert (await b.post(C, json=body(gstin=gstin))).status_code == 201
    assert (
        await a.post(C, json=body(gstin=make_test_gstin("33", 14)))
    ).status_code == 201  # transaction still healthy


async def test_unknown_and_invalid_fields_are_rejected(
    world: World, make_client: ClientFactory
) -> None:
    c = await login_as(make_client, world.a["admin"])
    assert (await c.post(C, json=body(org_id=str(world.b.org_id)))).status_code == 422
    assert (await c.post(C, json=body(name=""))).status_code == 422
    assert (await c.post(C, json=body(name="x" * 201))).status_code == 422
    assert (await c.post(C, json=body(email="nope"))).status_code == 422
    assert (await c.post(C, json=body(default_terms_days=366))).status_code == 422
    assert (await c.post(C, json=body(is_active=False))).status_code == 422


async def test_update_is_partial_and_audited(
    world: World, make_client: ClientFactory, app: FastAPI
) -> None:
    c = await login_as(make_client, world.a["sales"])
    created = (await c.post(C, json=body(name="Before Ltd", phone="999"))).json()
    r = await c.patch(
        f"{C}/{created['id']}", json={"name": "After Ltd", "has_written_agreement": True}
    )
    assert r.status_code == 200
    out = r.json()
    assert (out["name"], out["phone"], out["has_written_agreement"]) == ("After Ltd", "999", True)
    async with app.state.engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT action, before->>'name', after->>'name', actor_user_id FROM audit_logs WHERE entity_id=:i ORDER BY created_at"
                ),
                {"i": uuid.UUID(created["id"])},
            )
        ).all()
    assert [r[0] for r in rows] == ["customer.create", "customer.update"]
    assert rows[1][1:3] == ("Before Ltd", "After Ltd")
    assert rows[1][3] == world.a["sales"].id


async def test_clearing_optional_fields_and_deactivating(
    world: World, make_client: ClientFactory
) -> None:
    c = await login_as(make_client, world.a["admin"])
    created = (
        await c.post(C, json=body(phone="123", notes="hello", gstin=make_test_gstin("33", 15)))
    ).json()
    r = await c.patch(
        f"{C}/{created['id']}",
        json={"phone": None, "notes": None, "gstin": None, "is_active": False},
    )
    assert r.status_code == 200
    out = r.json()
    assert (out["phone"], out["notes"], out["gstin"], out["is_active"]) == (None, None, None, False)


async def test_changing_state_must_keep_an_existing_gstin_consistent(
    world: World, make_client: ClientFactory
) -> None:
    c = await login_as(make_client, world.a["admin"])
    created = (await c.post(C, json=body(gstin=make_test_gstin("33", 16)))).json()
    assert (await c.patch(f"{C}/{created['id']}", json={"state_code": "27"})).status_code == 422
    r = await c.patch(
        f"{C}/{created['id']}", json={"state_code": "27", "gstin": make_test_gstin("27", 16)}
    )
    assert r.status_code == 200


async def test_search_filters_and_paginates(world: World, make_client: ClientFactory) -> None:
    c = await login_as(make_client, world.a["admin"])
    for i in range(7):
        await c.post(C, json=body(name=f"Zebra {i}"))
    await c.post(C, json=body(name="Quokka 100% Pure", email="q@example.com"))
    inactive = (await c.post(C, json=body(name="Zebra dormant"))).json()
    await c.patch(f"{C}/{inactive['id']}", json={"is_active": False})

    page1 = (await c.get(C, params={"q": "zebra", "limit": 5})).json()
    assert page1["total"] == 8
    assert len(page1["items"]) == 5
    assert page1["items"] == sorted(page1["items"], key=lambda x: (x["name"], x["id"]))
    page2 = (await c.get(C, params={"q": "zebra", "limit": 5, "offset": 5})).json()
    assert len(page2["items"]) == 3
    assert not {x["id"] for x in page1["items"]} & {x["id"] for x in page2["items"]}

    active_only = (await c.get(C, params={"q": "zebra", "is_active": "true"})).json()
    assert active_only["total"] == 7
    # '%' is a literal in search, not a wildcard
    assert (await c.get(C, params={"q": "100%"})).json()["total"] == 1
    assert (await c.get(C, params={"q": "%"})).json()["total"] == 1
    assert (await c.get(C, params={"q": "q@example"})).json()["total"] == 1


async def test_paging_parameters_are_bounded(world: World, make_client: ClientFactory) -> None:
    c = await login_as(make_client, world.a["admin"])
    for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"limit": "x"}):
        assert (await c.get(C, params=params)).status_code == 422


async def test_every_role_can_read_and_write_customers(
    world: World, make_client: ClientFactory
) -> None:
    for i, role in enumerate(("admin", "finance", "sales")):
        c = await login_as(make_client, world.a[role], forwarded_for=f"10.5.0.{i}")
        assert (await c.post(C, json=body())).status_code == 201
        assert (await c.get(C)).status_code == 200


async def test_unauthenticated_access_is_refused(client: httpx.AsyncClient) -> None:
    assert (await client.get(C)).status_code == 401
    assert (await client.post(C, json=body())).status_code == 401


async def test_invalid_uuid_path_is_a_validation_error_not_a_server_error(
    world: World, make_client: ClientFactory
) -> None:
    c = await login_as(make_client, world.a["admin"])
    assert (await c.get(f"{C}/not-a-uuid")).status_code == 422
    assert (await c.get(f"{C}/{uuid.uuid4()}")).status_code == 404
