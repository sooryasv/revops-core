"""Manual payments (03 section 8.4) and the payments listing."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import httpx

from app.core.clock import FixedClock
from tests.conftest import ClientFactory, World, login_as
from tests.payments_helpers import I, Setup, prepare, send

TODAY_KEYS = ("amount_paid_paise", "payment_status", "amount_due_paise")


def body(amount: int, paid_on: str, method: str = "bank_transfer", **kw: Any) -> dict[str, Any]:
    return {"amount_paise": amount, "method": method, "paid_on": paid_on, **kw}


async def today(s: Setup, clock: FixedClock) -> str:
    return clock.today_ist().isoformat()


async def test_roles_finance_and_admin_record_sales_and_viewer_cannot(
    world: World, make_client: ClientFactory, clock: FixedClock
) -> None:
    s = await prepare(world, make_client, with_order=False)
    when = clock.today_ist().isoformat()
    url = f"{I}/{s.invoice_id}/payments/manual"
    sales = await login_as(make_client, world.a["sales"], forwarded_for="10.9.9.1")
    assert (await sales.post(url, json=body(100, when))).status_code == 403
    assert (await s.fin.post(url, json=body(100, when))).status_code == 201
    admin = await login_as(make_client, world.a["admin"], forwarded_for="10.9.9.2")
    assert (await admin.post(url, json=body(100, when))).status_code == 201
    # sales may read the listing
    assert (await sales.get(f"{I}/{s.invoice_id}/payments")).status_code == 200


async def test_partial_then_full_payment_moves_status_and_derives_the_cache(
    world: World, make_client: ClientFactory, clock: FixedClock, sql: Any
) -> None:
    s = await prepare(world, make_client, with_order=False)
    when = clock.today_ist().isoformat()
    url = f"{I}/{s.invoice_id}/payments/manual"
    r = await s.fin.post(url, json=body(40_000, when, reference="UTR123"))
    assert r.status_code == 201
    pay = r.json()
    assert (pay["provider"], pay["status"], pay["amount_paise"], pay["reference"]) == (
        "manual",
        "captured",
        40_000,
        "UTR123",
    )
    assert pay["paid_on"] == when
    inv = (await s.fin.get(f"{I}/{s.invoice_id}")).json()
    assert (inv["amount_paid_paise"], inv["payment_status"], inv["amount_due_paise"]) == (
        40_000,
        "partially_paid",
        s.total - 40_000,
    )
    assert (await s.fin.post(url, json=body(s.total - 40_000, when))).status_code == 201
    inv = (await s.fin.get(f"{I}/{s.invoice_id}")).json()
    assert (inv["amount_paid_paise"], inv["payment_status"], inv["amount_due_paise"]) == (
        s.total,
        "paid",
        0,
    )
    cached = await sql.val(
        "SELECT amount_paid_paise FROM invoices WHERE id=$1", uuid.UUID(s.invoice_id)
    )
    summed = await sql.val(
        "SELECT sum(amount_paise) FROM payments WHERE invoice_id=$1 AND status='captured'",
        uuid.UUID(s.invoice_id),
    )
    assert cached == summed == s.total


async def test_amount_bounds_overpayment_and_a_fully_paid_invoice_are_rejected(
    world: World, make_client: ClientFactory, clock: FixedClock
) -> None:
    s = await prepare(world, make_client, with_order=False)
    when = clock.today_ist().isoformat()
    url = f"{I}/{s.invoice_id}/payments/manual"
    for bad in (0, -5, s.total + 1, 10**13 + 1, 1.5, "100"):
        assert (await s.fin.post(url, json=body(bad, when))).status_code == 422, bad  # type: ignore[arg-type]
    assert (await s.fin.post(url, json=body(s.total, when))).status_code == 201
    again = await s.fin.post(url, json=body(1, when))
    assert again.status_code == 422  # exceeds the (zero) amount due
    assert len((await s.fin.get(f"{url.rsplit('/manual', 1)[0]}")).json()) == 1


async def test_unknown_method_extra_fields_and_missing_fields_are_422(
    world: World, make_client: ClientFactory, clock: FixedClock
) -> None:
    s = await prepare(world, make_client, with_order=False)
    when = clock.today_ist().isoformat()
    url = f"{I}/{s.invoice_id}/payments/manual"
    for payload in (
        body(100, when, method="bitcoin"),
        body(100, when, org_id=str(uuid.uuid4())),
        {"amount_paise": 100, "method": "cash"},
        body(100, "not-a-date"),
    ):
        assert (await s.fin.post(url, json=payload)).status_code == 422


async def test_paid_on_must_lie_between_the_issue_date_and_today(
    world: World, make_client: ClientFactory, clock: FixedClock
) -> None:
    s = await prepare(world, make_client, with_order=False)
    today_ist = clock.today_ist()
    url = f"{I}/{s.invoice_id}/payments/manual"
    issue = s.invoice["issue_date"]
    future = (today_ist + timedelta(days=1)).isoformat()
    before_issue = (today_ist - timedelta(days=400)).isoformat()
    assert issue <= today_ist.isoformat()
    for bad in (future, before_issue):
        r = await s.fin.post(url, json=body(100, bad))
        assert r.status_code == 422, bad
        assert r.json()["errors"][0]["field"] == "paid_on"
    for ok in (today_ist.isoformat(), issue):
        assert (await s.fin.post(url, json=body(100, ok))).status_code == 201


async def test_only_issued_invoices_accept_payments(
    world: World, make_client: ClientFactory, clock: FixedClock
) -> None:
    s = await prepare(world, make_client, with_order=False)
    when = clock.today_ist().isoformat()
    cust = s.invoice["customer_id"]
    item = {"description": "x", "quantity": 1, "unit_price_paise": 1000, "gst_rate_bp": 0}
    draft = (await s.fin.post(I, json={"customer_id": cust, "items": [item]})).json()
    r = await s.fin.post(f"{I}/{draft['id']}/payments/manual", json=body(100, when))
    assert r.status_code == 409
    await s.fin.post(f"{I}/{s.invoice_id}/void", json={"reason": "Wrong buyer"})
    r = await s.fin.post(f"{I}/{s.invoice_id}/payments/manual", json=body(100, when))
    assert r.status_code == 409


async def test_a_manual_payment_supersedes_the_open_checkout_order_and_the_next_order_is_for_the_balance(
    world: World, make_client: ClientFactory, clock: FixedClock, sql: Any
) -> None:
    s = await prepare(world, make_client)
    when = clock.today_ist().isoformat()
    assert (
        await s.fin.post(f"{I}/{s.invoice_id}/payments/manual", json=body(50_000, when))
    ).status_code == 201
    assert (
        await sql.val("SELECT status FROM payment_orders WHERE provider_order_id=$1", s.order_id)
    ) == "superseded"
    fresh = (await s.anon.post(f"/api/v1/public/pay/{s.token}/order")).json()
    assert fresh["order_id"] != s.order_id
    assert fresh["amount_paise"] == s.total - 50_000


async def test_void_is_blocked_once_money_has_been_received(
    world: World, make_client: ClientFactory, clock: FixedClock
) -> None:
    s = await prepare(world, make_client, with_order=False)
    when = clock.today_ist().isoformat()
    await s.fin.post(f"{I}/{s.invoice_id}/payments/manual", json=body(100, when))
    r = await s.fin.post(f"{I}/{s.invoice_id}/void", json={"reason": "Too late"})
    assert r.status_code == 409
    assert (await s.fin.get(f"{I}/{s.invoice_id}")).json()["status"] == "issued"


async def test_manual_payment_is_audited_with_the_user_as_actor(
    world: World, make_client: ClientFactory, clock: FixedClock, sql: Any
) -> None:
    s = await prepare(world, make_client, with_order=False)
    when = clock.today_ist().isoformat()
    pay = (await s.fin.post(f"{I}/{s.invoice_id}/payments/manual", json=body(700, when))).json()
    row = await sql.fetch(
        "SELECT actor_type, actor_user_id FROM audit_logs WHERE action='payment.manual' AND entity_id=$1",
        uuid.UUID(pay["id"]),
    )
    assert [(r["actor_type"], r["actor_user_id"]) for r in row] == [("user", world.a["finance"].id)]


async def test_payment_listing_returns_only_that_invoices_payments_in_order(
    world: World, make_client: ClientFactory, clock: FixedClock
) -> None:
    s = await prepare(world, make_client, with_order=False)
    other = await prepare(world, make_client, with_order=False)
    when = clock.today_ist().isoformat()
    for amount in (100, 200):
        await s.fin.post(f"{I}/{s.invoice_id}/payments/manual", json=body(amount, when))
    await other.fin.post(f"{I}/{other.invoice_id}/payments/manual", json=body(999, when))
    rows = (await s.fin.get(f"{I}/{s.invoice_id}/payments")).json()
    assert [r["amount_paise"] for r in rows] == [100, 200]
    assert {r["invoice_id"] for r in rows} == {s.invoice_id}


async def test_concurrent_manual_payments_cannot_overpay(
    world: World, make_client: ClientFactory, clock: FixedClock, sql: Any
) -> None:
    import asyncio

    s = await prepare(world, make_client, with_order=False)
    when = clock.today_ist().isoformat()
    url = f"{I}/{s.invoice_id}/payments/manual"
    half = s.total // 2 + 1  # two of these exceed the total
    results = await asyncio.gather(*(s.fin.post(url, json=body(half, when)) for _ in range(4)))
    assert sorted(r.status_code for r in results) == [201, 422, 422, 422]
    paid = await sql.val(
        "SELECT amount_paid_paise FROM invoices WHERE id=$1", uuid.UUID(s.invoice_id)
    )
    assert paid == half


async def test_manual_payment_then_webhook_for_the_rest_converges(
    world: World, make_client: ClientFactory, clock: FixedClock
) -> None:
    s = await prepare(world, make_client)
    when = clock.today_ist().isoformat()
    await s.fin.post(f"{I}/{s.invoice_id}/payments/manual", json=body(1_000, when))
    # the stale order (full amount) is still what the provider knows; its webhook is quarantined, not applied
    r = await send(s.anon, s.event("payment.captured", f"pay_{uuid.uuid4().hex[:10]}"))
    assert r.status_code == 200
    inv = (await s.fin.get(f"{I}/{s.invoice_id}")).json()
    assert inv["amount_paid_paise"] >= 1_000
    assert isinstance(r, httpx.Response)
