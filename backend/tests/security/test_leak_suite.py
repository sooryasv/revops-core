"""Cross-tenant leak suite (03 section 6.4 rule 6). As an Org B user, try every way to reach Org A's data.

Every case must end in 404, an empty result, or a validation failure, and never in Org A data. New tenant endpoints
add a row to ENDPOINTS as they are built (M4 onward); the completeness test below fails if one is missing.
"""

from __future__ import annotations

import json

from fastapi import FastAPI
from sqlalchemy import text

from tests.conftest import ClientFactory, World, login_as
from tests.security.routes import application_routes

# (method, path template, json body, who must be the *target* (A's id) or None)
ENDPOINTS_BY_ID = [
    ("PATCH", "/api/v1/users/{user_id}", {"full_name": "pwned"}),
    ("GET", "/api/v1/customers/{customer_id}", None),
    ("PATCH", "/api/v1/customers/{customer_id}", {"name": "pwned"}),
    ("GET", "/api/v1/deals/{deal_id}", None),
    ("PATCH", "/api/v1/deals/{deal_id}", {"title": "pwned"}),
    ("POST", "/api/v1/deals/{deal_id}/transition", {"stage": "qualified"}),
    ("POST", "/api/v1/deals/{deal_id}/create-invoice", None),
    ("GET", "/api/v1/invoices/{invoice_id}", None),
    (
        "PUT",
        "/api/v1/invoices/{invoice_id}",
        {
            "version": 1,
            "payment_terms_days": 30,
            "place_of_supply_state_code": "33",
            "items": [{"description": "x", "quantity": 1, "unit_price_paise": 1, "gst_rate_bp": 0}],
        },
    ),
    ("POST", "/api/v1/invoices/{invoice_id}/issue", None),
    ("POST", "/api/v1/invoices/{invoice_id}/void", {"reason": "pwned"}),
    ("POST", "/api/v1/invoices/{invoice_id}/pay-link", None),
    ("GET", "/api/v1/invoices/{invoice_id}/payments", None),
    (
        "POST",
        "/api/v1/invoices/{invoice_id}/payments/manual",
        {"amount_paise": 1, "method": "cash", "paid_on": "2026-01-01"},
    ),
]


async def test_org_b_admin_cannot_modify_org_a_user(
    world: World, make_client: ClientFactory, app: FastAPI
) -> None:
    b_admin = await login_as(make_client, world.b["admin"])
    target = world.a["sales"]
    for patch in (
        {"full_name": "pwned"},
        {"role": "admin"},
        {"is_active": False},
        {"password": "hijacked-password-1"},
    ):
        r = await b_admin.patch(f"/api/v1/users/{target.id}", json=patch)
        assert r.status_code == 404, patch
        assert r.json()["code"] == "not_found"
    async with app.state.engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT full_name, role, is_active, token_version FROM users WHERE id=:i"),
                {"i": target.id},
            )
        ).one()
    assert row[0] != "pwned"
    assert row[1:] == ("sales", True, 0)


async def test_missing_and_foreign_ids_are_indistinguishable(
    world: World, make_client: ClientFactory
) -> None:
    b_admin = await login_as(make_client, world.b["admin"])
    foreign = await b_admin.patch(f"/api/v1/users/{world.a['sales'].id}", json={"full_name": "x"})
    missing = await b_admin.patch(
        "/api/v1/users/00000000-0000-4000-8000-000000000000", json={"full_name": "x"}
    )
    strip = lambda r: {k: v for k, v in r.json().items() if k not in ("request_id", "instance")}  # noqa: E731
    assert foreign.status_code == missing.status_code == 404
    assert strip(foreign) == strip(missing)


async def test_user_list_only_contains_own_tenant(world: World, make_client: ClientFactory) -> None:
    b_admin = await login_as(make_client, world.b["admin"])
    users = (await b_admin.get("/api/v1/users")).json()
    assert {u["email"] for u in users} == {a.email for a in world.b.users.values()}
    assert not any(a.email in json.dumps(users) for a in world.a.users.values())


async def test_org_endpoint_returns_only_own_org_and_patch_cannot_target_another(
    world: World, make_client: ClientFactory, app: FastAPI
) -> None:
    b_admin = await login_as(make_client, world.b["admin"])
    assert (await b_admin.get("/api/v1/org")).json()["id"] == str(world.b.org_id)
    r = await b_admin.patch("/api/v1/org", json={"id": str(world.a.org_id), "name": "pwned"})
    assert r.status_code == 422  # org id is never accepted from the client
    await b_admin.patch("/api/v1/org", json={"name": "B renamed"})
    async with app.state.engine.connect() as conn:
        name_a = (
            await conn.execute(
                text("SELECT name FROM organizations WHERE id=:i"), {"i": world.a.org_id}
            )
        ).scalar_one()
    assert name_a == world.a.name


async def test_org_id_in_a_user_creation_body_is_rejected(
    world: World, make_client: ClientFactory
) -> None:
    b_admin = await login_as(make_client, world.b["admin"])
    body = {"email": "plant@example.com", "full_name": "P", "password": "correct-horse-battery", "role": "sales",
            "org_id": str(world.a.org_id)}  # fmt: skip
    assert (await b_admin.post("/api/v1/users", json=body)).status_code == 422


async def test_cookie_for_org_a_user_with_org_b_claim_does_not_authenticate(
    world: World, make_client: ClientFactory, app: FastAPI
) -> None:
    from datetime import UTC, datetime

    token, _ = app.state.jwt.issue(
        user_id=world.a["admin"].id, org_id=world.b.org_id, role="admin", token_version=0, audience="revops",
        now=datetime.now(UTC),
    )  # fmt: skip
    c = make_client(cookies={"revops_session": token})
    assert (await c.get("/api/v1/users")).status_code == 401


async def test_tenant_tables_hold_no_row_without_the_callers_org_after_writes(
    world: World, make_client: ClientFactory, app: FastAPI
) -> None:
    """Writes by A and B never produce a user whose org_id is the other tenant's."""
    a_admin = await login_as(make_client, world.a["admin"])
    await a_admin.post(
        "/api/v1/users",
        json={
            "email": "ax@example.com",
            "full_name": "A",
            "password": "correct-horse-battery",
            "role": "sales",
        },
    )
    async with app.state.engine.connect() as conn:
        rows = (
            (await conn.execute(text("SELECT org_id FROM users WHERE email='ax@example.com'")))
            .scalars()
            .all()
        )
    assert rows == [world.a.org_id]


def test_every_tenant_route_is_covered_by_this_suite(app: FastAPI) -> None:
    """A route that takes a resource id must be listed; new modules must extend the suite before merging."""
    id_routes = {
        (m, r.path)
        for r in application_routes(app)
        if "{" in r.path
        and not r.is_public  # public routes are keyed by an unguessable token, tested separately
        for m in r.methods
    }
    covered = {(m, p) for m, p, _ in ENDPOINTS_BY_ID}
    assert id_routes <= covered, f"add leak tests for: {sorted(id_routes - covered)}"


# ----------------------------------------------------------------------------- CRM (M4)


async def _a_customer_and_deal(world: World, make_client: ClientFactory) -> tuple[str, str]:
    a = await login_as(make_client, world.a["admin"], forwarded_for="10.6.0.1")
    cust = (
        await a.post(
            "/api/v1/customers",
            json={"name": "A Secret Buyer", "state_code": "33", "email": "secret@a.example"},
        )
    ).json()["id"]
    deal = (
        await a.post("/api/v1/deals", json={"customer_id": cust, "title": "A Secret Deal"})
    ).json()["id"]
    return cust, deal


async def test_org_b_cannot_read_or_change_org_a_customers_and_deals(
    world: World, make_client: ClientFactory, app: FastAPI
) -> None:
    cust, deal = await _a_customer_and_deal(world, make_client)
    b = await login_as(make_client, world.b["admin"], forwarded_for="10.6.0.2")
    for method, template, payload in ENDPOINTS_BY_ID:
        if "{user_id}" in template or "{invoice_id}" in template:
            continue
        path = template.replace("{customer_id}", cust).replace("{deal_id}", deal)
        r = await b.request(method, path, json=payload)
        assert r.status_code == 404, (method, path, r.status_code)
        assert r.json()["code"] == "not_found"
        assert "Secret" not in r.text
    async with app.state.engine.connect() as conn:
        name = (
            await conn.execute(text("SELECT name FROM customers WHERE id=:i"), {"i": cust})
        ).scalar_one()
        stage = (
            await conn.execute(text("SELECT stage FROM deals WHERE id=:i"), {"i": deal})
        ).scalar_one()
    assert (name, stage) == ("A Secret Buyer", "lead")


async def test_lists_and_searches_never_return_another_tenants_rows(
    world: World, make_client: ClientFactory
) -> None:
    await _a_customer_and_deal(world, make_client)
    b = await login_as(make_client, world.b["admin"], forwarded_for="10.6.0.3")
    for params in (
        {},
        {"q": "Secret"},
        {"q": "secret@a.example"},
        {"is_active": "true"},
        {"limit": 100},
    ):
        r = await b.get("/api/v1/customers", params=params)
        assert r.json()["total"] == 0
        assert "Secret" not in r.text
    for params in ({}, {"stage": "lead"}):
        r = await b.get("/api/v1/deals", params=params)
        assert r.json()["total"] == 0
        assert "Secret" not in r.text


async def test_org_b_cannot_reference_org_a_ids_when_creating(
    world: World, make_client: ClientFactory
) -> None:
    cust, _ = await _a_customer_and_deal(world, make_client)
    b = await login_as(make_client, world.b["admin"], forwarded_for="10.6.0.4")
    r = await b.post("/api/v1/deals", json={"customer_id": cust, "title": "IDOR"})
    assert r.status_code == 404
    mine = (await b.post("/api/v1/customers", json={"name": "B Buyer", "state_code": "27"})).json()[
        "id"
    ]
    r2 = await b.post(
        "/api/v1/deals",
        json={"customer_id": mine, "title": "ok", "owner_user_id": str(world.a["admin"].id)},
    )
    assert r2.status_code == 404  # owner from another tenant


async def test_same_customer_gstin_in_two_tenants_never_collides_in_lookups(
    world: World, make_client: ClientFactory
) -> None:
    from app.domain.gstin import make_test_gstin

    gstin = make_test_gstin("33", 99)
    a = await login_as(make_client, world.a["admin"], forwarded_for="10.6.0.5")
    b = await login_as(make_client, world.b["admin"], forwarded_for="10.6.0.6")
    ca = (
        await a.post(
            "/api/v1/customers", json={"name": "Shared Buyer A", "state_code": "33", "gstin": gstin}
        )
    ).json()
    cb = (
        await b.post(
            "/api/v1/customers", json={"name": "Shared Buyer B", "state_code": "33", "gstin": gstin}
        )
    ).json()
    assert (await a.get("/api/v1/customers", params={"q": gstin})).json()["items"][0]["id"] == ca[
        "id"
    ]
    assert (await b.get("/api/v1/customers", params={"q": gstin})).json()["items"][0]["id"] == cb[
        "id"
    ]


# ----------------------------------------------------------------------------- invoices (M5)


async def _a_issued_invoice(world: World, make_client: ClientFactory) -> tuple[str, str, str]:
    a = await login_as(make_client, world.a["finance"], forwarded_for="10.6.1.1")
    cust = (
        await a.post("/api/v1/customers", json={"name": "A Invoice Buyer", "state_code": "33"})
    ).json()["id"]
    item = {
        "description": "A Secret Line",
        "quantity": 1,
        "unit_price_paise": 100_000,
        "gst_rate_bp": 1800,
    }
    draft = (await a.post("/api/v1/invoices", json={"customer_id": cust, "items": [item]})).json()[
        "id"
    ]
    issued = (await a.post(f"/api/v1/invoices/{draft}/issue")).json()
    return cust, draft, issued["number"]


async def test_org_b_cannot_touch_org_a_invoices(
    world: World, make_client: ClientFactory, app: FastAPI
) -> None:
    _cust, inv, _number = await _a_issued_invoice(world, make_client)
    b = await login_as(make_client, world.b["admin"], forwarded_for="10.6.1.2")
    for method, template, payload in ENDPOINTS_BY_ID:
        if "{invoice_id}" not in template:
            continue
        r = await b.request(method, template.replace("{invoice_id}", inv), json=payload)
        assert r.status_code == 404, (method, template, r.status_code)
        assert r.json()["code"] == "not_found"
        assert "Secret" not in r.text
    async with app.state.engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT status, void_reason, pay_token_hash, version FROM invoices WHERE id=:i"
                ),
                {"i": inv},
            )
        ).one()
    assert row == ("issued", None, None, 2)


async def test_invoice_lists_and_searches_are_tenant_scoped(
    world: World, make_client: ClientFactory
) -> None:
    cust, _inv, number = await _a_issued_invoice(world, make_client)
    b = await login_as(make_client, world.b["admin"], forwarded_for="10.6.1.3")
    for params in (
        {},
        {"q": number},
        {"q": "A Invoice Buyer"},
        {"customer_id": cust},
        {"status": "issued"},
        {"overdue": "false"},
    ):
        r = await b.get("/api/v1/invoices", params=params)
        assert r.status_code == 200
        assert r.json()["total"] == 0, params
        assert "Secret" not in r.text


async def test_org_b_cannot_invoice_org_a_customer_or_deal(
    world: World, make_client: ClientFactory
) -> None:
    cust, _, _ = await _a_issued_invoice(world, make_client)
    a = await login_as(make_client, world.a["admin"], forwarded_for="10.6.1.4")
    deal = (await a.post("/api/v1/deals", json={"customer_id": cust, "title": "A deal"})).json()[
        "id"
    ]
    b = await login_as(make_client, world.b["admin"], forwarded_for="10.6.1.5")
    item = {"description": "x", "quantity": 1, "unit_price_paise": 1, "gst_rate_bp": 0}
    assert (
        await b.post("/api/v1/invoices", json={"customer_id": cust, "items": [item]})
    ).status_code == 404
    mine = (await b.post("/api/v1/customers", json={"name": "B", "state_code": "27"})).json()["id"]
    assert (
        await b.post(
            "/api/v1/invoices", json={"customer_id": mine, "deal_id": deal, "items": [item]}
        )
    ).status_code == 404
    assert (
        await b.post("/api/v1/invoices/preview", json={"customer_id": cust, "items": [item]})
    ).status_code == 404


async def test_org_b_issuing_does_not_touch_org_a_counter(
    world: World, make_client: ClientFactory, app: FastAPI
) -> None:
    await _a_issued_invoice(world, make_client)
    b = await login_as(make_client, world.b["finance"], forwarded_for="10.6.1.6")
    cb = (await b.post("/api/v1/customers", json={"name": "B", "state_code": "27"})).json()["id"]
    item = {"description": "x", "quantity": 1, "unit_price_paise": 100, "gst_rate_bp": 0}
    d = (await b.post("/api/v1/invoices", json={"customer_id": cb, "items": [item]})).json()["id"]
    assert (await b.post(f"/api/v1/invoices/{d}/issue")).json()["number"].endswith("000001")
    async with app.state.engine.connect() as conn:
        counters = dict(
            (
                await conn.execute(
                    text(
                        "SELECT org_id::text, last_value FROM invoice_sequences WHERE org_id IN (:a,:b)"
                    ),
                    {"a": world.a.org_id, "b": world.b.org_id},
                )
            ).all()
        )
    assert counters == {str(world.a.org_id): 1, str(world.b.org_id): 1}
