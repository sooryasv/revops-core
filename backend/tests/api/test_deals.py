from __future__ import annotations

import uuid

from fastapi import FastAPI
from sqlalchemy import text

from tests.conftest import ClientFactory, World, login_as

D, C = "/api/v1/deals", "/api/v1/customers"


async def customer(client, name: str = "Buyer") -> str:  # type: ignore[no-untyped-def]
    r = await client.post(C, json={"name": f"{name} {uuid.uuid4().hex[:6]}", "state_code": "33"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def make_deal(client, cust: str, **kw: object) -> dict:  # type: ignore[no-untyped-def,type-arg]
    r = await client.post(
        D, json={"customer_id": cust, "title": "Big order", "amount_estimate_paise": 500_000, **kw}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_create_defaults_to_lead_and_caller_as_owner(
    world: World, make_client: ClientFactory
) -> None:
    sales = await login_as(make_client, world.a["sales"])
    deal = await make_deal(sales, await customer(sales))
    assert deal["stage"] == "lead"
    assert deal["owner_user_id"] == str(world.a["sales"].id)
    assert deal["closed_at"] is None
    assert (await sales.get(f"{D}/{deal['id']}")).json()["title"] == "Big order"


async def test_full_happy_path_to_won(
    world: World, make_client: ClientFactory, app: FastAPI
) -> None:
    c = await login_as(make_client, world.a["sales"])
    deal = await make_deal(c, await customer(c))
    for stage in ("qualified", "proposal", "won"):
        r = await c.post(f"{D}/{deal['id']}/transition", json={"stage": stage})
        assert r.status_code == 200, r.text
        assert r.json()["stage"] == stage
    final = r.json()
    assert final["closed_at"] is not None
    assert final["stage_changed_at"] >= deal["stage_changed_at"]
    async with app.state.engine.connect() as conn:
        actions = (
            await conn.execute(
                text(
                    "SELECT action, before->>'stage', after->>'stage' FROM audit_logs WHERE entity_id=:i ORDER BY created_at"
                ),
                {"i": uuid.UUID(deal["id"])},
            )
        ).all()
    assert [a[0] for a in actions] == ["deal.create"] + ["deal.transition"] * 3
    assert [(a[1], a[2]) for a in actions[1:]] == [
        ("lead", "qualified"),
        ("qualified", "proposal"),
        ("proposal", "won"),
    ]


async def test_illegal_transitions_are_rejected(world: World, make_client: ClientFactory) -> None:
    c = await login_as(make_client, world.a["sales"])
    deal = await make_deal(c, await customer(c))
    for bad in ("proposal", "won", "lead"):
        r = await c.post(f"{D}/{deal['id']}/transition", json={"stage": bad})
        assert r.status_code == 409, bad
        assert r.json()["code"] == "invalid_state"
    assert (await c.get(f"{D}/{deal['id']}")).json()["stage"] == "lead"


async def test_lost_requires_a_reason_and_records_it(
    world: World, make_client: ClientFactory
) -> None:
    c = await login_as(make_client, world.a["sales"])
    deal = await make_deal(c, await customer(c))
    for payload in ({"stage": "lost"}, {"stage": "lost", "lost_reason": "   "}):
        r = await c.post(f"{D}/{deal['id']}/transition", json=payload)
        assert r.status_code == 422
        assert r.json()["code"] == "validation_error"
    ok = await c.post(
        f"{D}/{deal['id']}/transition",
        json={"stage": "lost", "lost_reason": "Went with a competitor"},
    )
    assert ok.status_code == 200
    assert (ok.json()["stage"], ok.json()["lost_reason"]) == ("lost", "Went with a competitor")
    assert ok.json()["closed_at"] is not None


async def test_lost_is_reachable_from_every_open_stage(
    world: World, make_client: ClientFactory
) -> None:
    c = await login_as(make_client, world.a["sales"])
    for steps in ([], ["qualified"], ["qualified", "proposal"]):
        deal = await make_deal(c, await customer(c))
        for s in steps:
            await c.post(f"{D}/{deal['id']}/transition", json={"stage": s})
        r = await c.post(
            f"{D}/{deal['id']}/transition", json={"stage": "lost", "lost_reason": "budget"}
        )
        assert r.status_code == 200


async def test_terminal_deals_cannot_move_or_be_edited(
    world: World, make_client: ClientFactory
) -> None:
    c = await login_as(make_client, world.a["sales"])
    deal = await make_deal(c, await customer(c))
    await c.post(f"{D}/{deal['id']}/transition", json={"stage": "lost", "lost_reason": "budget"})
    for stage in ("lead", "qualified", "won", "lost"):
        r = await c.post(f"{D}/{deal['id']}/transition", json={"stage": stage, "lost_reason": "x"})
        assert r.status_code == 409
    assert (await c.patch(f"{D}/{deal['id']}", json={"title": "new"})).status_code == 409


async def test_unknown_stage_is_a_validation_error(
    world: World, make_client: ClientFactory
) -> None:
    c = await login_as(make_client, world.a["sales"])
    deal = await make_deal(c, await customer(c))
    assert (
        await c.post(f"{D}/{deal['id']}/transition", json={"stage": "archived"})
    ).status_code == 422
    assert (
        await c.post(f"{D}/{deal['id']}/transition", json={"stage": "won", "extra": 1})
    ).status_code == 422


async def test_update_fields_and_audit(
    world: World, make_client: ClientFactory, app: FastAPI
) -> None:
    c = await login_as(make_client, world.a["admin"])
    deal = await make_deal(c, await customer(c))
    r = await c.patch(
        f"{D}/{deal['id']}",
        json={"title": "Renamed", "amount_estimate_paise": 1_000_000, "expected_close_date": "2026-12-31",
              "owner_user_id": str(world.a["finance"].id)},
    )  # fmt: skip
    assert r.status_code == 200
    out = r.json()
    assert (out["title"], out["amount_estimate_paise"], out["expected_close_date"]) == (
        "Renamed",
        1_000_000,
        "2026-12-31",
    )
    assert out["owner_user_id"] == str(world.a["finance"].id)
    async with app.state.engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT before->>'title', after->>'title' FROM audit_logs WHERE entity_id=:i AND action='deal.update'"
                ),
                {"i": uuid.UUID(deal["id"])},
            )
        ).one()
    assert row == ("Big order", "Renamed")


async def test_validation_on_money_and_references(world: World, make_client: ClientFactory) -> None:
    c = await login_as(make_client, world.a["admin"])
    cust = await customer(c)
    base = {"customer_id": cust, "title": "T"}
    assert (await c.post(D, json={**base, "amount_estimate_paise": -1})).status_code == 422
    assert (await c.post(D, json={**base, "amount_estimate_paise": 10**13 + 1})).status_code == 422
    assert (
        await c.post(D, json={**base, "amount_estimate_paise": 1.5})
    ).status_code == 422  # no floats for money
    assert (await c.post(D, json={**base, "title": ""})).status_code == 422
    assert (
        await c.post(D, json={**base, "stage": "won"})
    ).status_code == 422  # stage is not settable on create
    assert (await c.post(D, json={**base, "customer_id": str(uuid.uuid4())})).status_code == 404
    assert (await c.post(D, json={**base, "owner_user_id": str(uuid.uuid4())})).status_code == 404


async def test_inactive_owner_is_rejected(world: World, make_client: ClientFactory) -> None:
    admin = await login_as(make_client, world.a["admin"])
    await admin.patch(f"/api/v1/users/{world.a['finance'].id}", json={"is_active": False})
    r = await admin.post(
        D,
        json={
            "customer_id": await customer(admin),
            "title": "T",
            "owner_user_id": str(world.a["finance"].id),
        },
    )
    assert r.status_code == 404


async def test_list_filters_and_pagination(world: World, make_client: ClientFactory) -> None:
    c = await login_as(make_client, world.a["admin"])
    c1, c2 = await customer(c), await customer(c)
    for i in range(4):
        await make_deal(c, c1, title=f"D{i}")
    other = await make_deal(c, c2)
    await c.post(f"{D}/{other['id']}/transition", json={"stage": "qualified"})
    assert (await c.get(D)).json()["total"] == 5
    assert (await c.get(D, params={"customer_id": c1})).json()["total"] == 4
    assert (await c.get(D, params={"stage": "qualified"})).json()["total"] == 1
    assert (await c.get(D, params={"stage": "bogus"})).status_code == 422
    page = (await c.get(D, params={"limit": 2, "offset": 2})).json()
    assert (len(page["items"]), page["total"]) == (2, 5)


async def test_role_permissions_on_deals(world: World, make_client: ClientFactory) -> None:
    sales = await login_as(make_client, world.a["sales"])
    finance = await login_as(make_client, world.a["finance"])
    deal = await make_deal(sales, await customer(sales))
    assert (await finance.get(f"{D}/{deal['id']}")).status_code == 200  # finance reads
    assert (await finance.get(D)).status_code == 200
    assert (
        await finance.post(D, json={"customer_id": deal["customer_id"], "title": "x"})
    ).status_code == 403
    assert (await finance.patch(f"{D}/{deal['id']}", json={"title": "x"})).status_code == 403
    assert (
        await finance.post(f"{D}/{deal['id']}/transition", json={"stage": "qualified"})
    ).status_code == 403


async def test_create_invoice_requires_a_won_deal(world: World, make_client: ClientFactory) -> None:
    c = await login_as(make_client, world.a["sales"])
    deal = await make_deal(c, await customer(c))
    r = await c.post(f"{D}/{deal['id']}/create-invoice")
    assert r.status_code == 409
    assert r.json()["code"] == "invalid_state"
    assert "won" in r.json()["detail"]
