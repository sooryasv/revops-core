"""Webhook handling against real PostgreSQL (03 section 8.3): every required test in the spec."""

from __future__ import annotations

import asyncio
import itertools
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.core.clock import FixedClock
from tests.conftest import ClientFactory, World
from tests.payments_helpers import WEBHOOK, I, Setup, prepare, send, sign


async def counts(sql: Any, setup: Setup) -> dict[str, Any]:
    iid = uuid.UUID(setup.invoice_id)
    return {
        "payments": await sql.val("SELECT count(*) FROM payments WHERE invoice_id=$1", iid),
        "paid": await sql.val("SELECT amount_paid_paise FROM invoices WHERE id=$1", iid),
        "captured_sum": await sql.val(
            "SELECT coalesce(sum(amount_paise),0) FROM payments WHERE invoice_id=$1 AND status='captured'",
            iid,
        ),
    }


async def audit_actions(sql: Any, setup: Setup) -> list[str]:
    rows = await sql.fetch(
        "SELECT action FROM audit_logs WHERE entity_id=$1 OR entity_id IN (SELECT id FROM payments WHERE invoice_id=$1) ORDER BY created_at",
        uuid.UUID(setup.invoice_id),
    )
    return [r["action"] for r in rows]


# ----------------------------------------------------------------------------- signature and intake


async def test_invalid_signature_is_rejected_and_nothing_is_stored(
    world: World, make_client: ClientFactory, sql: Any
) -> None:
    s = await prepare(world, make_client)
    before = await sql.val("SELECT count(*) FROM webhook_events")
    payload = s.event("payment.captured", "pay_bad")
    for kwargs in (
        {"secret": "wrong-secret"},
        {"signature": None},
        {"signature": "deadbeef"},
        {"signature": ""},
    ):
        r = await send(s.anon, payload, **kwargs)
        assert r.status_code == 400, kwargs
        assert r.json()["code"] == "webhook_signature_invalid"
    tampered = await send(
        s.anon,
        raw=b'{"event":"payment.captured"}',
        signature=sign(b'{"event":"payment.captured "}'),
    )
    assert tampered.status_code == 400
    assert await sql.val("SELECT count(*) FROM webhook_events") == before
    assert (await counts(sql, s))["payments"] == 0


async def test_missing_event_id_or_bad_body_after_a_valid_signature_is_a_validation_error(
    world: World, make_client: ClientFactory
) -> None:
    s = await prepare(world, make_client)
    no_id = await send(s.anon, s.event("payment.captured", "pay_1"), event_id="")
    assert (no_id.status_code, no_id.json()["code"]) == (
        400 if no_id.status_code == 400 else 422,
        "validation_error",
    )
    not_json = await send(s.anon, raw=b"this is not json")
    assert not_json.json()["code"] == "validation_error"
    not_object = await send(s.anon, raw=b"[1,2,3]")
    assert not_object.json()["code"] == "validation_error"


async def test_oversized_webhook_body_is_refused(world: World, make_client: ClientFactory) -> None:
    s = await prepare(world, make_client)
    r = await s.anon.post(
        WEBHOOK,
        content=b"x" * 1_100_000,
        headers={"x-razorpay-signature": "x", "x-razorpay-event-id": "e"},
    )
    assert r.status_code == 413


async def test_webhook_route_is_exempt_from_the_origin_check(
    world: World, make_client: ClientFactory
) -> None:
    s = await prepare(world, make_client)
    r = await send(
        s.anon,
        s.event("payment.captured", f"pay_{uuid.uuid4().hex[:10]}"),
        headers={"origin": "https://evil.example"},
    )
    assert r.status_code == 200


# ----------------------------------------------------------------------------- the effect of a capture


async def test_captured_event_marks_the_invoice_paid(
    world: World, make_client: ClientFactory, sql: Any, clock: FixedClock
) -> None:
    clock.set(datetime(2026, 11, 2, 19, 0, tzinfo=UTC))  # 00:30 IST on 3 Nov
    s = await prepare(world, make_client)
    r = await send(s.anon, s.event("payment.captured", "pay_1"))
    assert (r.status_code, r.json()) == (200, {"status": "processed"})
    inv = await s.reload()
    assert (inv["amount_paid_paise"], inv["payment_status"], inv["amount_due_paise"]) == (
        s.total,
        "paid",
        0,
    )
    row = (
        await sql.fetch(
            "SELECT status, amount_paise, paid_on, method, payment_order_id FROM payments WHERE provider_payment_id='pay_1'"
        )
    )[0]
    assert (row["status"], row["amount_paise"], str(row["paid_on"]), row["method"]) == (
        "captured",
        s.total,
        "2026-11-03",
        "upi",
    )  # IST date
    assert (
        await sql.val("SELECT status FROM payment_orders WHERE provider_order_id=$1", s.order_id)
        == "paid"
    )
    ev = (
        await sql.fetch(
            "SELECT processing_status, attempts, org_id FROM webhook_events ORDER BY received_at DESC LIMIT 1"
        )
    )[0]
    assert (ev["processing_status"], ev["attempts"], ev["org_id"]) == (
        "processed",
        1,
        world.a.org_id,
    )
    assert "payment.captured" in await audit_actions(sql, s)


async def test_authorized_alone_does_not_count_as_paid(
    world: World, make_client: ClientFactory, sql: Any
) -> None:
    s = await prepare(world, make_client)
    await send(s.anon, s.event("payment.authorized", "pay_a"))
    inv = await s.reload()
    assert (inv["amount_paid_paise"], inv["payment_status"]) == (0, "unpaid")
    assert (
        await sql.val("SELECT status FROM payments WHERE provider_payment_id='pay_a'")
        == "authorized"
    )


async def test_failed_alone_records_the_failure_code(
    world: World, make_client: ClientFactory, sql: Any
) -> None:
    s = await prepare(world, make_client)
    await send(s.anon, s.event("payment.failed", "pay_f", error_code="BAD_REQUEST_ERROR"))
    row = (
        await sql.fetch(
            "SELECT status, failure_code FROM payments WHERE provider_payment_id='pay_f'"
        )
    )[0]
    assert (row["status"], row["failure_code"]) == ("failed", "BAD_REQUEST_ERROR")
    assert (await s.reload())["amount_paid_paise"] == 0


# ----------------------------------------------------------------------------- idempotency


async def test_duplicate_delivery_has_no_second_effect(
    world: World, make_client: ClientFactory, sql: Any
) -> None:
    s = await prepare(world, make_client)
    payload = s.event("payment.captured", "pay_d")
    first = await send(s.anon, payload, event_id="evt_same")
    second = await send(s.anon, payload, event_id="evt_same")
    third = await send(s.anon, payload, event_id="evt_same")
    assert [r.status_code for r in (first, second, third)] == [200, 200, 200]
    assert [r.json()["status"] for r in (first, second, third)] == [
        "processed",
        "duplicate",
        "duplicate",
    ]
    assert await counts(sql, s) == {"payments": 1, "paid": s.total, "captured_sum": s.total}
    assert (await audit_actions(sql, s)).count("payment.captured") == 1
    assert (
        await sql.val("SELECT attempts FROM webhook_events WHERE provider_event_id='evt_same'") == 1
    )


async def test_two_concurrent_deliveries_of_one_event_produce_exactly_one_effect(
    world: World, make_client: ClientFactory, sql: Any
) -> None:
    s = await prepare(world, make_client)
    payload = s.event("payment.captured", "pay_race")
    results = await asyncio.gather(*[send(s.anon, payload, event_id="evt_race") for _ in range(6)])
    assert [r.status_code for r in results] == [200] * 6
    assert sorted(r.json()["status"] for r in results) == ["duplicate"] * 5 + ["processed"]
    assert await counts(sql, s) == {"payments": 1, "paid": s.total, "captured_sum": s.total}
    assert (await audit_actions(sql, s)).count("payment.captured") == 1
    assert (
        await sql.val("SELECT count(*) FROM webhook_events WHERE provider_event_id='evt_race'") == 1
    )


async def test_same_payment_in_different_events_is_one_payment(
    world: World, make_client: ClientFactory, sql: Any
) -> None:
    s = await prepare(world, make_client)
    await send(s.anon, s.event("payment.authorized", "pay_x"))
    await send(s.anon, s.event("payment.captured", "pay_x"))
    await send(s.anon, s.event("order.paid", "pay_x"))
    assert await counts(sql, s) == {
        "payments": 1,
        "paid": s.total,
        "captured_sum": s.total,
    }  # captured once, never doubled


# ----------------------------------------------------------------------------- ordering


async def test_order_paid_before_payment_captured_and_the_reverse(
    world: World, make_client: ClientFactory, sql: Any
) -> None:
    for first, second in (("order.paid", "payment.captured"), ("payment.captured", "order.paid")):
        s = await prepare(world, make_client)
        pid = f"pay_{uuid.uuid4().hex[:10]}"
        await send(s.anon, s.event(first, pid))
        await send(s.anon, s.event(second, pid))
        assert await counts(sql, s) == {"payments": 1, "paid": s.total, "captured_sum": s.total}, (
            first,
            second,
        )


async def test_a_late_authorized_never_undoes_a_capture(
    world: World, make_client: ClientFactory, sql: Any
) -> None:
    s = await prepare(world, make_client)
    await send(s.anon, s.event("payment.captured", "pay_l"))
    await send(s.anon, s.event("payment.authorized", "pay_l"))
    assert (
        await sql.val("SELECT status FROM payments WHERE provider_payment_id='pay_l'") == "captured"
    )
    assert (await s.reload())["payment_status"] == "paid"


async def test_failed_after_captured_does_not_override_it(
    world: World, make_client: ClientFactory, sql: Any
) -> None:
    s = await prepare(world, make_client)
    await send(s.anon, s.event("payment.captured", "pay_c"))
    await send(s.anon, s.event("payment.failed", "pay_c", error_code="GATEWAY_ERROR"))
    row = (
        await sql.fetch(
            "SELECT status, failure_code FROM payments WHERE provider_payment_id='pay_c'"
        )
    )[0]
    assert (row["status"], row["failure_code"]) == ("captured", None)
    assert (await s.reload())["amount_paid_paise"] == s.total


KINDS = ["payment.authorized", "payment.captured", "payment.failed", "order.paid"]


@pytest.mark.parametrize(
    "order", list(itertools.permutations(KINDS)), ids=lambda o: "-".join(k.split(".")[1] for k in o)
)
async def test_every_arrival_order_converges_to_the_same_final_state(
    world: World, make_client: ClientFactory, sql: Any, order: tuple[str, ...]
) -> None:
    s = await prepare(world, make_client)
    pid = f"pay_{uuid.uuid4().hex[:10]}"
    for kind in order:
        assert (await send(s.anon, s.event(kind, pid))).status_code == 200
    assert (
        await sql.val(f"SELECT status FROM payments WHERE provider_payment_id='{pid}'")
        == "captured"
    )
    assert await counts(sql, s) == {"payments": 1, "paid": s.total, "captured_sum": s.total}


# ----------------------------------------------------------------------------- quarantine and unknowns


@pytest.mark.parametrize(("amount", "currency"), [(1, "INR"), (118_001, "INR"), (118_000, "USD")])
async def test_amount_or_currency_mismatch_is_quarantined_and_leaves_the_invoice_alone(
    world: World, make_client: ClientFactory, sql: Any, amount: int, currency: str
) -> None:
    s = await prepare(world, make_client)
    evt = f"evt_mismatch_{uuid.uuid4().hex[:8]}"
    r = await send(
        s.anon,
        s.event("payment.captured", "pay_m", amount=amount, currency=currency),
        event_id=evt,
    )
    assert (r.status_code, r.json()["status"]) == (200, "quarantined")  # 200: a retry cannot fix it
    assert await counts(sql, s) == {"payments": 0, "paid": 0, "captured_sum": 0}
    assert (
        await sql.val(
            f"SELECT processing_status FROM webhook_events WHERE provider_event_id='{evt}'"
        )
        == "quarantined"
    )
    assert (
        await sql.val(f"SELECT last_error FROM webhook_events WHERE provider_event_id='{evt}'")
        == "amount_or_currency_mismatch"
    )
    assert "payment.quarantined" in await audit_actions(sql, s)
    again = await send(
        s.anon,
        s.event("payment.captured", "pay_m", amount=amount, currency=currency),
        event_id=evt,
    )
    assert again.json()["status"] == "duplicate"


async def test_empty_currency_in_the_payload_is_accepted_when_the_amount_matches(
    world: World, make_client: ClientFactory
) -> None:
    s = await prepare(world, make_client)
    r = await send(s.anon, s.event("payment.authorized", "pay_ec", currency=""))
    assert (
        r.json()["status"] == "processed"
    )  # SPEC_ISSUES S6: Razorpay doc samples show an empty currency


async def test_unknown_order_is_ignored(world: World, make_client: ClientFactory, sql: Any) -> None:
    s = await prepare(world, make_client)
    r = await send(
        s.anon,
        {**s.event("payment.captured", "pay_u"), **{}}
        | {
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_u",
                        "order_id": "order_not_ours",
                        "amount": 5,
                        "currency": "INR",
                    }
                }
            }
        },
        event_id="evt_unknown",
    )
    assert (r.status_code, r.json()["status"]) == (200, "ignored")
    row = (
        await sql.fetch(
            "SELECT processing_status, last_error, org_id FROM webhook_events WHERE provider_event_id='evt_unknown'"
        )
    )[0]
    assert (row["processing_status"], row["last_error"], row["org_id"]) == (
        "ignored",
        "unknown_order",
        None,
    )
    assert (await counts(sql, s))["payments"] == 0


@pytest.mark.parametrize(
    "body",
    [
        {"event": "refund.created", "payload": {}},
        {"event": "payment.captured", "payload": {}},
        {"hello": "world"},
    ],
)
async def test_unhandled_or_malformed_events_are_ignored_not_retried(
    world: World, make_client: ClientFactory, sql: Any, body: dict[str, Any]
) -> None:
    s = await prepare(world, make_client)
    r = await send(s.anon, body, event_id=f"evt_{uuid.uuid4().hex}")
    assert (r.status_code, r.json()["status"]) == (200, "ignored")
    assert (await counts(sql, s))["payments"] == 0


# ----------------------------------------------------------------------------- partial, over, void


async def test_partial_payments_accumulate(
    world: World, make_client: ClientFactory, sql: Any, clock: FixedClock
) -> None:
    clock.set(datetime(2026, 11, 2, 4, 0, tzinfo=UTC))
    s = await prepare(world, make_client, with_order=False)
    manual = await s.fin.post(
        f"{I}/{s.invoice_id}/payments/manual",
        json={"amount_paise": 40_000, "method": "cash", "paid_on": "2026-11-02"},
    )
    assert manual.status_code == 201
    assert (await s.reload())["payment_status"] == "partially_paid"
    order = (await s.anon.post(f"/api/v1/public/pay/{s.token}/order")).json()
    assert order["amount_paise"] == s.total - 40_000  # the order is for the remaining balance
    s.order_id = order["order_id"]
    r = await send(s.anon, s.event("payment.captured", "pay_rest", amount=s.total - 40_000))
    assert r.json()["status"] == "processed"
    inv = await s.reload()
    assert (inv["amount_paid_paise"], inv["payment_status"], inv["amount_due_paise"]) == (
        s.total,
        "paid",
        0,
    )
    assert await counts(sql, s) == {"payments": 2, "paid": s.total, "captured_sum": s.total}


async def test_overpayment_is_recorded_flagged_and_keeps_the_cache_consistent(
    world: World, make_client: ClientFactory, sql: Any
) -> None:
    s = await prepare(world, make_client)
    tag = uuid.uuid4().hex[:8]
    first = await send(s.anon, s.event("payment.captured", f"pay_1_{tag}"))
    second = await send(
        s.anon, s.event("payment.captured", f"pay_2_{tag}")
    )  # a second full payment against the same order
    assert (first.json()["status"], second.json()["status"]) == ("processed", "processed")
    inv = await s.reload()
    assert inv["amount_paid_paise"] == 2 * s.total
    assert (inv["payment_status"], inv["overpaid_paise"], inv["amount_due_paise"]) == (
        "paid",
        s.total,
        0,
    )
    assert "payment.overpaid" in await audit_actions(sql, s)
    assert await counts(sql, s) == {"payments": 2, "paid": 2 * s.total, "captured_sum": 2 * s.total}


async def test_concurrent_captures_of_different_payments_never_lose_an_update(
    world: World, make_client: ClientFactory, sql: Any
) -> None:
    s = await prepare(world, make_client)
    results = await asyncio.gather(
        *[send(s.anon, s.event("payment.captured", f"pay_c{i}")) for i in range(5)]
    )
    assert [r.status_code for r in results] == [200] * 5
    assert await counts(sql, s) == {
        "payments": 5,
        "paid": 5 * s.total,
        "captured_sum": 5 * s.total,
    }  # derived, not incremented


async def test_a_payment_arriving_for_a_void_invoice_is_recorded_and_flagged(
    world: World, make_client: ClientFactory, sql: Any
) -> None:
    s = await prepare(world, make_client)
    assert (
        await s.fin.post(f"{I}/{s.invoice_id}/void", json={"reason": "mistake"})
    ).status_code == 200
    r = await send(s.anon, s.event("payment.captured", "pay_v"))
    assert r.json()["status"] == "processed"  # money really moved; never silently drop it
    assert (await s.reload())["amount_paid_paise"] == s.total
    assert "payment.on_void_invoice" in await audit_actions(sql, s)


# ----------------------------------------------------------------------------- failure and retry


async def test_a_failure_midway_rolls_everything_back_then_the_retry_succeeds(
    world: World, make_client: ClientFactory, sql: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = await prepare(world, make_client)
    payload = s.event("payment.captured", "pay_rb")
    import app.services.webhooks as svc

    real = svc.record_audit

    def flaky(session: Any, **kwargs: Any) -> None:
        if kwargs["action"] == "payment.captured":
            raise RuntimeError("simulated crash after the payment row and balance were written")
        real(session, **kwargs)

    monkeypatch.setattr(svc, "record_audit", flaky)
    broken = await send(s.anon, payload, event_id="evt_retry")
    assert broken.status_code == 500  # the provider will retry
    # no half-state: payment, balance and order are untouched
    assert await counts(sql, s) == {"payments": 0, "paid": 0, "captured_sum": 0}
    assert (
        await sql.val("SELECT status FROM payment_orders WHERE provider_order_id=$1", s.order_id)
        == "created"
    )
    ev = (
        await sql.fetch(
            "SELECT processing_status, attempts, last_error FROM webhook_events WHERE provider_event_id='evt_retry'"
        )
    )[0]
    assert (ev["processing_status"], ev["attempts"]) == ("failed", 1)
    assert "simulated crash" in ev["last_error"]

    monkeypatch.undo()
    retry = await send(s.anon, payload, event_id="evt_retry")
    assert (retry.status_code, retry.json()["status"]) == (200, "processed")
    assert await counts(sql, s) == {"payments": 1, "paid": s.total, "captured_sum": s.total}
    ev = (
        await sql.fetch(
            "SELECT processing_status, attempts, last_error FROM webhook_events WHERE provider_event_id='evt_retry'"
        )
    )[0]
    assert (ev["processing_status"], ev["attempts"], ev["last_error"]) == ("processed", 2, None)
    again = await send(s.anon, payload, event_id="evt_retry")
    assert again.json()["status"] == "duplicate"


async def test_repeated_failures_keep_counting_attempts(
    world: World, make_client: ClientFactory, sql: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = await prepare(world, make_client)
    import app.services.webhooks as svc

    monkeypatch.setattr(
        svc, "record_audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    )
    for _ in range(3):
        assert (
            await send(s.anon, s.event("payment.captured", "pay_x"), event_id="evt_flaky")
        ).status_code == 500
    assert (
        await sql.val("SELECT attempts FROM webhook_events WHERE provider_event_id='evt_flaky'")
        == 3
    )
    assert (
        await sql.val(
            "SELECT processing_status FROM webhook_events WHERE provider_event_id='evt_flaky'"
        )
        == "failed"
    )


async def test_the_webhook_log_keeps_the_raw_body_and_secrets_are_not_logged(
    world: World,
    make_client: ClientFactory,
    sql: Any,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    s = await prepare(world, make_client)
    payload = s.event("payment.captured", "pay_log")
    r = await send(s.anon, payload, event_id="evt_log")
    assert r.status_code == 200
    stored = await sql.val("SELECT raw_body FROM webhook_events WHERE provider_event_id='evt_log'")
    assert '"pay_log"' in stored
    await send(s.anon, payload, secret="wrong-secret-xyz")
    output = capsys.readouterr().out + caplog.text
    assert "wrong-secret-xyz" not in output
    assert (
        "pay_log" not in output.split("webhook_signature_invalid")[-1]
    )  # a rejected body is never logged


async def test_webhook_response_never_leaks_internals(
    world: World, make_client: ClientFactory
) -> None:
    s = await prepare(world, make_client)
    r = await send(s.anon, s.event("payment.captured", "pay_leak"))
    assert set(r.json()) == {"status"}
    bad = await send(s.anon, s.event("payment.captured", "pay_leak"), secret="nope")
    assert "Traceback" not in bad.text
    assert isinstance(bad, httpx.Response)


async def test_a_provider_payment_id_that_belongs_to_another_tenant_is_quarantined_not_retried_forever(
    world: World, make_client: ClientFactory, sql: Any
) -> None:
    a = await prepare(world, make_client)
    b = await prepare(world, make_client)
    pid = f"pay_{uuid.uuid4().hex[:10]}"
    assert (await send(a.anon, a.event("payment.captured", pid))).json()["status"] == "processed"
    other = await send(b.anon, b.event("payment.captured", pid), event_id="evt_conflict")
    assert (other.status_code, other.json()["status"]) == (200, "quarantined")
    assert (await counts(sql, b))["paid"] == 0
