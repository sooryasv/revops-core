"""The unauthenticated pay page (03 section 8.2)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

from app.core.clock import FixedClock
from app.core.paytoken import generate_pay_token
from tests.conftest import ClientFactory, World
from tests.payments_helpers import KEY_SECRET, I, anon_client, checkout_sig, prepare

P = "/api/v1/public/pay"


async def test_view_shows_only_what_a_payer_needs(world: World, make_client: ClientFactory) -> None:
    s = await prepare(world, make_client, with_order=False)
    r = await s.anon.get(f"{P}/{s.token}")
    assert r.status_code == 200
    data = r.json()
    assert data["supplier_name"] == world.a.name
    assert data["invoice_number"] == s.invoice["number"]
    assert (data["total_paise"], data["amount_due_paise"], data["can_pay"]) == (
        s.total,
        s.total,
        True,
    )
    assert data["payment_status"] == "unpaid"
    forbidden = {
        "id",
        "org_id",
        "customer_id",
        "pay_token_hash",
        "version",
        "created_by_user_id",
        "email",
    }
    assert not forbidden & set(data)
    assert all("id" not in line for line in data["items"])
    assert str(world.a.org_id) not in r.text


async def test_every_kind_of_bad_token_gets_the_same_404_body(
    world: World, make_client: ClientFactory, clock: FixedClock
) -> None:
    s = await prepare(world, make_client, with_order=False)
    bodies = set()
    bad_tokens = ["rvp_" + "A" * 43, "garbage", "rvp_short", generate_pay_token()[0], "x" * 500]
    for token in bad_tokens:
        for suffix in ("", "/order"):
            r = (
                (await s.anon.get(f"{P}/{token}"))
                if not suffix
                else (await s.anon.post(f"{P}/{token}{suffix}"))
            )
            assert r.status_code == 404, (token, suffix)
            bodies.add(r.text.replace(r.headers.get("x-request-id", ""), ""))
    # void invoice and draft share the same answer
    await s.fin.post(f"{I}/{s.invoice_id}/void", json={"reason": "Wrong buyer"})
    voided = await s.anon.get(f"{P}/{s.token}")
    assert voided.status_code == 404
    bodies.add(voided.text)
    normalised = {_strip(b) for b in bodies}
    assert len(normalised) == 1, normalised


def _strip(text: str) -> str:
    import json

    data = json.loads(text)
    data.pop("request_id", None)
    data.pop("instance", None)
    return json.dumps(data, sort_keys=True)


async def test_expired_token_is_a_404(
    world: World, make_client: ClientFactory, clock: FixedClock
) -> None:
    s = await prepare(world, make_client, with_order=False)
    assert (await s.anon.get(f"{P}/{s.token}")).status_code == 200
    clock.set(clock.now_utc() + timedelta(days=31))
    assert (await s.anon.get(f"{P}/{s.token}")).status_code == 404


async def test_regenerating_the_link_kills_the_old_one(
    world: World, make_client: ClientFactory
) -> None:
    s = await prepare(world, make_client, with_order=False)
    new = (await s.fin.post(f"{I}/{s.invoice_id}/pay-link")).json()["url"].rsplit("/pay/", 1)[1]
    assert new != s.token
    assert (await s.anon.get(f"{P}/{s.token}")).status_code == 404
    assert (await s.anon.get(f"{P}/{new}")).status_code == 200


async def test_rate_limit_is_30_per_minute_per_ip(world: World, make_client: ClientFactory) -> None:
    s = await prepare(world, make_client, with_order=False)
    ip = {"x-forwarded-for": "203.0.113.77"}
    codes = [(await s.anon.get(f"{P}/{s.token}", headers=ip)).status_code for _ in range(31)]
    assert codes[:30] == [200] * 30
    assert codes[30] == 429
    other = await s.anon.get(f"{P}/{s.token}", headers={"x-forwarded-for": "203.0.113.78"})
    assert other.status_code == 200  # another client is unaffected


async def test_create_order_returns_key_id_and_amount_and_records_receipt(
    world: World, make_client: ClientFactory, sql: Any
) -> None:
    s = await prepare(world, make_client, with_order=False)
    r = await s.anon.post(f"{P}/{s.token}/order")
    assert r.status_code == 200
    data = r.json()
    assert (data["amount_paise"], data["currency"]) == (s.total, "INR")
    assert data["key_id"].startswith("rzp_test_")
    assert "key_secret" not in r.text
    assert KEY_SECRET not in r.text
    row = await sql.fetch(
        "SELECT receipt, amount_paise, status, provider FROM payment_orders WHERE provider_order_id=$1",
        data["order_id"],
    )
    assert (row[0]["amount_paise"], row[0]["status"], row[0]["provider"]) == (
        s.total,
        "created",
        "razorpay",
    )
    assert s.invoice["number"] in row[0]["receipt"]
    assert len(row[0]["receipt"]) <= 40


async def test_the_open_order_is_reused_while_the_amount_is_unchanged(
    world: World, make_client: ClientFactory, app: Any
) -> None:
    s = await prepare(world, make_client, with_order=False)
    first = (await s.anon.post(f"{P}/{s.token}/order")).json()
    second = (await s.anon.post(f"{P}/{s.token}/order")).json()
    assert first == second
    assert len(app.state.provider.created_orders) >= 1


async def test_parallel_order_requests_yield_a_single_order(
    world: World, make_client: ClientFactory, sql: Any
) -> None:
    s = await prepare(world, make_client, with_order=False)
    clients = [anon_client(make_client) for _ in range(6)]
    results = await asyncio.gather(*(c.post(f"{P}/{s.token}/order") for c in clients))
    assert [r.status_code for r in results] == [200] * 6
    assert len({r.json()["order_id"] for r in results}) == 1
    open_orders = await sql.val(
        "SELECT count(*) FROM payment_orders WHERE invoice_id=$1 AND status='created'",
        uuid.UUID(s.invoice_id),
    )
    assert open_orders == 1


async def test_paid_or_void_invoices_cannot_get_an_order(
    world: World, make_client: ClientFactory, clock: FixedClock
) -> None:
    s = await prepare(world, make_client, with_order=False)
    when = clock.today_ist().isoformat()
    await s.fin.post(
        f"{I}/{s.invoice_id}/payments/manual",
        json={"amount_paise": s.total, "method": "cash", "paid_on": when},
    )
    view = (await s.anon.get(f"{P}/{s.token}")).json()
    assert (view["can_pay"], view["payment_status"], view["amount_due_paise"]) == (False, "paid", 0)
    r = await s.anon.post(f"{P}/{s.token}/order")
    assert r.status_code == 409


async def test_provider_failure_is_a_502_and_leaves_no_order_row(
    world: World, make_client: ClientFactory, app: Any, sql: Any
) -> None:
    s = await prepare(world, make_client, with_order=False)
    app.state.provider.fail_next_create = True
    r = await s.anon.post(f"{P}/{s.token}/order")
    assert r.status_code == 502
    assert (
        await sql.val(
            "SELECT count(*) FROM payment_orders WHERE invoice_id=$1", uuid.UUID(s.invoice_id)
        )
        == 0
    )
    assert (await s.anon.post(f"{P}/{s.token}/order")).status_code == 200  # recovers


async def test_confirm_with_a_valid_signature_is_acknowledged_but_marks_nothing_paid(
    world: World, make_client: ClientFactory, sql: Any
) -> None:
    s = await prepare(world, make_client)
    pid = f"pay_{uuid.uuid4().hex[:10]}"
    r = await s.anon.post(
        f"{P}/{s.token}/confirm",
        json={
            "order_id": s.order_id,
            "payment_id": pid,
            "signature": checkout_sig(s.order_id, pid),
        },
    )
    assert r.status_code == 200
    assert r.json() == {"status": "pending_confirmation"}
    inv = (await s.fin.get(f"{I}/{s.invoice_id}")).json()
    assert (inv["amount_paid_paise"], inv["payment_status"]) == (0, "unpaid")
    assert (
        await sql.val("SELECT count(*) FROM payments WHERE invoice_id=$1", uuid.UUID(s.invoice_id))
        == 0
    )
    actor = await sql.val(
        "SELECT actor_type FROM audit_logs WHERE action='payment.confirm_submitted' AND entity_id=$1",
        uuid.UUID(s.invoice_id),
    )
    assert actor == "public"


async def test_confirm_rejects_bad_foreign_and_tampered_signatures(
    world: World, make_client: ClientFactory
) -> None:
    s = await prepare(world, make_client)
    other = await prepare(world, make_client)
    pid = f"pay_{uuid.uuid4().hex[:10]}"
    good = checkout_sig(s.order_id, pid)
    cases = [
        {"order_id": s.order_id, "payment_id": pid, "signature": "0" * 64},
        {"order_id": s.order_id, "payment_id": pid + "x", "signature": good},  # payment id tampered
        {
            "order_id": s.order_id,
            "payment_id": pid,
            "signature": checkout_sig(s.order_id, pid, "wrong-secret"),
        },
        # an order that belongs to another tenant's invoice, with a correct signature for it
        {
            "order_id": other.order_id,
            "payment_id": pid,
            "signature": checkout_sig(other.order_id, pid),
        },
        {"order_id": "order_nope", "payment_id": pid, "signature": checkout_sig("order_nope", pid)},
    ]
    for payload in cases:
        r = await s.anon.post(f"{P}/{s.token}/confirm", json=payload)
        assert r.status_code == 422, payload
    assert (await s.anon.post(f"{P}/{s.token}/confirm", json={"order_id": "x"})).status_code == 422
    inv = (await s.fin.get(f"{I}/{s.invoice_id}")).json()
    assert inv["amount_paid_paise"] == 0
