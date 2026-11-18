"""Invoicing through the API against real PostgreSQL (03 sections 4.2 to 4.4, 8.2)."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.core.clock import FixedClock
from app.domain.gst import LineInput, compute_invoice
from app.schemas.invoices import SORT_FIELDS
from tests.conftest import ClientFactory, World, login_as

I, C, D = "/api/v1/invoices", "/api/v1/customers", "/api/v1/deals"  # noqa: E741


def at(iso_utc: str) -> datetime:
    return datetime.fromisoformat(iso_utc).replace(tzinfo=UTC)


def item(
    qty: int = 1, price: int = 100_000, rate: int = 1800, desc: str = "Widget", **kw: Any
) -> dict[str, Any]:
    return {
        "description": desc,
        "quantity": qty,
        "unit_price_paise": price,
        "gst_rate_bp": rate,
        **kw,
    }


async def new_customer(c: httpx.AsyncClient, state: str = "33", **kw: Any) -> str:
    r = await c.post(C, json={"name": f"Buyer {uuid.uuid4().hex[:6]}", "state_code": state, **kw})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def new_draft(
    c: httpx.AsyncClient, cust: str, items: list[dict[str, Any]] | None = None, **kw: Any
) -> dict[str, Any]:
    r = await c.post(I, json={"customer_id": cust, "items": items or [item()], **kw})
    assert r.status_code == 201, r.text
    return dict(r.json())


async def issued(c: httpx.AsyncClient, cust: str, **kw: Any) -> dict[str, Any]:
    draft = await new_draft(c, cust, **kw)
    r = await c.post(f"{I}/{draft['id']}/issue")
    assert r.status_code == 200, r.text
    return dict(r.json())


@pytest.fixture
async def fin(world: World, make_client: ClientFactory) -> httpx.AsyncClient:
    return await login_as(make_client, world.a["finance"])


# ----------------------------------------------------------------------------- totals


async def test_intra_state_invoice_splits_gst_into_cgst_and_sgst(fin: httpx.AsyncClient) -> None:
    inv = await new_draft(
        fin, await new_customer(fin, "33"), [item(qty=3, price=10_000, rate=1800)]
    )
    assert inv["status"] == "draft"
    assert inv["number"] is None
    assert (inv["subtotal_paise"], inv["cgst_paise"], inv["sgst_paise"], inv["igst_paise"], inv["total_paise"]) == (
        30_000, 2_700, 2_700, 0, 35_400,
    )  # fmt: skip
    (line,) = inv["items"]
    assert (line["line_no"], line["taxable_paise"], line["line_total_paise"]) == (1, 30_000, 35_400)


async def test_inter_state_invoice_uses_igst_only(fin: httpx.AsyncClient) -> None:
    inv = await new_draft(
        fin, await new_customer(fin, "27"), [item(qty=3, price=10_000, rate=1800)]
    )
    assert (inv["cgst_paise"], inv["sgst_paise"], inv["igst_paise"], inv["total_paise"]) == (
        0,
        0,
        5_400,
        35_400,
    )


async def test_place_of_supply_override_changes_supply_type(fin: httpx.AsyncClient) -> None:
    cust = await new_customer(fin, "33")
    inv = await new_draft(fin, cust, place_of_supply_state_code="27")
    assert inv["place_of_supply_state_code"] == "27"
    assert inv["igst_paise"] == 18_000


async def test_rounding_is_per_line_and_half_up(fin: httpx.AsyncClient) -> None:
    # 1001 paise at 5%: cgst = round(1001 * 250 / 10000) = round(25.025) = 25 each
    items = [
        item(price=1_001, rate=500),
        item(price=1_001, rate=500),
        item(qty=7, price=333, rate=1800),
    ]
    inv = await new_draft(fin, await new_customer(fin, "33"), items)
    expected = compute_invoice(
        "33",
        "33",
        [LineInput(i["quantity"], i["unit_price_paise"], i["gst_rate_bp"]) for i in items],
    )
    assert inv["total_paise"] == expected.total_paise
    assert (
        inv["cgst_paise"] == expected.cgst_paise == sum(line["cgst_paise"] for line in inv["items"])
    )
    assert [x["cgst_paise"] for x in inv["items"]][:2] == [25, 25]


@pytest.mark.parametrize("rate", [0, 500, 1800, 4000])
async def test_every_supported_rate_matches_the_domain_calculation(
    fin: httpx.AsyncClient, rate: int
) -> None:
    for state in ("33", "27"):
        inv = await new_draft(
            fin, await new_customer(fin, state), [item(qty=13, price=7_777, rate=rate)]
        )
        expected = compute_invoice("33", state, [LineInput(13, 7_777, rate)])
        assert inv["total_paise"] == expected.total_paise
        assert (inv["cgst_paise"], inv["sgst_paise"], inv["igst_paise"]) == (
            expected.cgst_paise, expected.sgst_paise, expected.igst_paise,
        )  # fmt: skip


async def test_preview_matches_the_saved_draft_and_writes_nothing(
    fin: httpx.AsyncClient, sql: Any
) -> None:
    cust = await new_customer(fin, "27")
    items = [item(qty=2, price=5_050, rate=1800), item(price=999, rate=500)]
    before = await sql.val("SELECT count(*) FROM invoices")
    prev = await fin.post(f"{I}/preview", json={"customer_id": cust, "items": items})
    assert prev.status_code == 200
    assert await sql.val("SELECT count(*) FROM invoices") == before
    draft = await new_draft(fin, cust, items)
    p = prev.json()
    assert p["supply_type"] == "inter"
    assert p["total_paise"] == draft["total_paise"]
    assert [x["line_total_paise"] for x in p["lines"]] == [
        x["line_total_paise"] for x in draft["items"]
    ]


async def test_preview_needs_a_customer_or_place_of_supply(fin: httpx.AsyncClient) -> None:
    assert (await fin.post(f"{I}/preview", json={"items": [item()]})).status_code == 422
    ok = await fin.post(
        f"{I}/preview", json={"place_of_supply_state_code": "33", "items": [item()]}
    )
    assert ok.json()["supply_type"] == "intra"


# ----------------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "bad_item",
    [
        item(rate=1200),  # unsupported rate
        item(qty=0),
        item(qty=1_000_001),
        item(price=-1),
        item(price=10**11 + 1),
        item(qty=1.5),  # type: ignore[arg-type]
        item(price=100.5),  # type: ignore[arg-type]
        item(price="100"),  # type: ignore[arg-type]
        item(desc=""),
        item(hsn_sac="12"),
        item(hsn_sac="ABCD"),
        {**item(), "line_total_paise": 1},  # clients cannot supply computed fields
    ],
)
async def test_bad_line_items_are_rejected(
    fin: httpx.AsyncClient, bad_item: dict[str, Any]
) -> None:
    r = await fin.post(I, json={"customer_id": await new_customer(fin), "items": [bad_item]})
    assert r.status_code == 422, r.text


async def test_structure_validation(fin: httpx.AsyncClient) -> None:
    cust = await new_customer(fin)
    assert (await fin.post(I, json={"customer_id": cust, "items": []})).status_code == 422
    assert (
        await fin.post(I, json={"customer_id": cust, "items": [item()] * 201})
    ).status_code == 422
    assert (
        await fin.post(I, json={"customer_id": cust, "items": [item()], "status": "issued"})
    ).status_code == 422
    assert (
        await fin.post(
            I, json={"customer_id": cust, "items": [item()], "number": "INV/26-27/000001"}
        )
    ).status_code == 422
    assert (
        await fin.post(I, json={"customer_id": cust, "items": [item()], "payment_terms_days": 366})
    ).status_code == 422
    assert (
        await fin.post(
            I, json={"customer_id": cust, "items": [item()], "place_of_supply_state_code": "00"}
        )
    ).status_code == 422
    assert (
        await fin.post(I, json={"customer_id": str(uuid.uuid4()), "items": [item()]})
    ).status_code == 404


async def test_inactive_customer_cannot_be_invoiced(fin: httpx.AsyncClient) -> None:
    cust = await new_customer(fin)
    await fin.patch(f"{C}/{cust}", json={"is_active": False})
    r = await fin.post(I, json={"customer_id": cust, "items": [item()]})
    assert r.status_code == 409
    assert r.json()["code"] == "invalid_state"


async def test_terms_default_to_the_customers_and_can_be_overridden(fin: httpx.AsyncClient) -> None:
    cust = await new_customer(fin, default_terms_days=45)
    assert (await new_draft(fin, cust))["payment_terms_days"] == 45
    assert (await new_draft(fin, cust, payment_terms_days=7))["payment_terms_days"] == 7


# ----------------------------------------------------------------------------- editing drafts


async def test_put_replaces_items_renumbers_lines_and_bumps_version(fin: httpx.AsyncClient) -> None:
    draft = await new_draft(fin, await new_customer(fin), [item(), item(desc="Second")])
    assert draft["version"] == 1
    body = {
        "version": 1, "payment_terms_days": 15, "place_of_supply_state_code": "33",
        "items": [item(qty=2, price=50_000, desc="Only line", hsn_sac="998313")],
    }  # fmt: skip
    r = await fin.put(f"{I}/{draft['id']}", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["version"] == 2
    assert out["payment_terms_days"] == 15
    assert [(x["line_no"], x["description"], x["hsn_sac"]) for x in out["items"]] == [
        (1, "Only line", "998313")
    ]
    assert out["total_paise"] == 118_000


async def test_stale_version_is_a_conflict(fin: httpx.AsyncClient) -> None:
    draft = await new_draft(fin, await new_customer(fin))
    body = {
        "version": 1,
        "payment_terms_days": 30,
        "place_of_supply_state_code": "33",
        "items": [item()],
    }
    assert (await fin.put(f"{I}/{draft['id']}", json=body)).status_code == 200
    stale = await fin.put(f"{I}/{draft['id']}", json=body)
    assert stale.status_code == 409
    assert stale.json()["code"] == "conflict"


async def test_issued_invoice_rejects_edits(fin: httpx.AsyncClient) -> None:
    inv = await issued(fin, await new_customer(fin))
    body = {
        "version": inv["version"],
        "payment_terms_days": 30,
        "place_of_supply_state_code": "33",
        "items": [item(price=1)],
    }
    r = await fin.put(f"{I}/{inv['id']}", json=body)
    assert r.status_code == 409
    assert r.json()["code"] == "invalid_state"
    assert (await fin.get(f"{I}/{inv['id']}")).json()["total_paise"] == inv["total_paise"]


# ----------------------------------------------------------------------------- issue


async def test_issue_assigns_number_dates_and_supply_type(
    fin: httpx.AsyncClient, clock: FixedClock
) -> None:
    clock.set(at("2026-11-02T04:00:00"))  # 09:30 IST
    inv = await issued(fin, await new_customer(fin, "27"), payment_terms_days=30)
    assert inv["status"] == "issued"
    assert inv["number"] == "INV/26-27/000001"
    assert len(inv["number"]) <= 16
    assert (inv["issue_date"], inv["due_date"]) == ("2026-11-02", "2026-12-02")
    assert inv["supply_type"] == "inter"
    assert inv["version"] == 2


async def test_numbers_are_consecutive_per_org_and_independent_between_orgs(
    world: World, make_client: ClientFactory, clock: FixedClock
) -> None:
    clock.set(at("2026-11-02T04:00:00"))
    a = await login_as(make_client, world.a["finance"], forwarded_for="10.7.0.1")
    b = await login_as(make_client, world.b["finance"], forwarded_for="10.7.0.2")
    ca, cb = await new_customer(a), await new_customer(b, "27")
    nums_a = [(await issued(a, ca))["number"] for _ in range(3)]
    nums_b = [(await issued(b, cb))["number"] for _ in range(2)]
    assert nums_a == [f"INV/26-27/{n:06d}" for n in (1, 2, 3)]
    assert nums_b == [f"INV/26-27/{n:06d}" for n in (1, 2)]


async def test_issue_is_idempotent_by_state(fin: httpx.AsyncClient, sql: Any) -> None:
    draft = await new_draft(fin, await new_customer(fin))
    first = (await fin.post(f"{I}/{draft['id']}/issue")).json()
    again = await fin.post(f"{I}/{draft['id']}/issue")
    assert again.status_code == 200
    assert again.json()["number"] == first["number"]
    assert again.json()["version"] == first["version"]
    assert (
        await sql.val(
            "SELECT count(*) FROM audit_logs WHERE entity_id=$1 AND action='invoice.issue'",
            uuid.UUID(draft["id"]),
        )
        == 1
    )


async def test_issue_freezes_totals_even_if_the_org_state_changes_later(
    world: World, make_client: ClientFactory, fin: httpx.AsyncClient
) -> None:
    inv = await issued(fin, await new_customer(fin, "33"))
    admin = await login_as(make_client, world.a["admin"])
    await admin.patch("/api/v1/org", json={"state_code": "27", "gstin": None})
    after = (await fin.get(f"{I}/{inv['id']}")).json()
    assert (after["cgst_paise"], after["igst_paise"], after["supply_type"]) == (
        inv["cgst_paise"],
        0,
        "intra",
    )


async def test_zero_total_invoice_cannot_be_issued(fin: httpx.AsyncClient) -> None:
    draft = await new_draft(fin, await new_customer(fin), [item(price=0)])
    r = await fin.post(f"{I}/{draft['id']}/issue")
    assert r.status_code == 422
    assert (await fin.get(f"{I}/{draft['id']}")).json()["status"] == "draft"


async def test_financial_year_rolls_over_on_the_ist_calendar(
    fin: httpx.AsyncClient, clock: FixedClock
) -> None:
    cust = await new_customer(fin)
    clock.set(at("2027-03-31T17:30:00"))  # 23:00 IST on 31 Mar 2027
    last = await issued(fin, cust)
    clock.set(at("2027-03-31T19:00:00"))  # still 31 Mar in UTC, but 00:30 IST on 1 Apr 2027
    first = await issued(fin, cust)
    assert last["number"] == "INV/26-27/000001"
    assert first["number"] == "INV/27-28/000001"
    assert first["issue_date"] == "2027-04-01"


async def test_failed_issue_does_not_burn_a_number(
    fin: httpx.AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch, clock: FixedClock
) -> None:
    clock.set(at("2026-11-02T04:00:00"))
    cust = await new_customer(fin)
    draft = await new_draft(fin, cust)

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated failure after the number was allocated")

    import app.services.invoices as svc

    monkeypatch.setattr(svc, "record_audit", boom)
    broken = await fin.post(f"{I}/{draft['id']}/issue")
    assert broken.status_code == 500
    assert (await fin.get(f"{I}/{draft['id']}")).json()["status"] == "draft"
    monkeypatch.undo()
    ok = await fin.post(f"{I}/{draft['id']}/issue")
    assert ok.json()["number"] == "INV/26-27/000001"  # the rolled-back attempt gave its number back
    assert (await issued(fin, cust))["number"] == "INV/26-27/000002"


# ----------------------------------------------------------------------------- void


async def test_void_requires_issued_status_and_a_reason(fin: httpx.AsyncClient) -> None:
    draft = await new_draft(fin, await new_customer(fin))
    r = await fin.post(f"{I}/{draft['id']}/void", json={"reason": "mistake"})
    assert r.status_code == 409
    inv = await issued(fin, await new_customer(fin))
    for body in ({}, {"reason": ""}, {"reason": "   "}):
        assert (await fin.post(f"{I}/{inv['id']}/void", json=body)).status_code == 422
    ok = await fin.post(f"{I}/{inv['id']}/void", json={"reason": "Wrong buyer"})
    assert ok.status_code == 200
    out = ok.json()
    assert (out["status"], out["void_reason"], out["number"]) == (
        "void",
        "Wrong buyer",
        inv["number"],
    )  # number kept


async def test_void_is_idempotent_and_a_void_invoice_cannot_be_reissued_or_paid(
    fin: httpx.AsyncClient,
) -> None:
    inv = await issued(fin, await new_customer(fin))
    first = (await fin.post(f"{I}/{inv['id']}/void", json={"reason": "one"})).json()
    again = await fin.post(f"{I}/{inv['id']}/void", json={"reason": "two"})
    assert again.status_code == 200
    assert again.json()["void_reason"] == first["void_reason"] == "one"
    assert (await fin.post(f"{I}/{inv['id']}/issue")).status_code == 409
    assert (await fin.post(f"{I}/{inv['id']}/pay-link")).status_code == 409


async def test_invoice_with_payments_cannot_be_voided(fin: httpx.AsyncClient, sql: Any) -> None:
    inv = await issued(fin, await new_customer(fin))
    await sql.execute(
        "UPDATE invoices SET amount_paid_paise = 1000 WHERE id=$1", uuid.UUID(inv["id"])
    )
    r = await fin.post(f"{I}/{inv['id']}/void", json={"reason": "oops"})
    assert r.status_code == 409
    assert (await fin.get(f"{I}/{inv['id']}")).json()["status"] == "issued"


async def test_voiding_kills_an_existing_pay_link(fin: httpx.AsyncClient) -> None:
    inv = await issued(fin, await new_customer(fin))
    await fin.post(f"{I}/{inv['id']}/pay-link")
    assert (await fin.get(f"{I}/{inv['id']}")).json()["pay_link_active"] is True
    await fin.post(f"{I}/{inv['id']}/void", json={"reason": "cancelled"})
    assert (await fin.get(f"{I}/{inv['id']}")).json()["pay_link_active"] is False


# ----------------------------------------------------------------------------- derived fields


async def test_payment_status_and_amount_due_are_derived(fin: httpx.AsyncClient, sql: Any) -> None:
    inv = await issued(fin, await new_customer(fin), items=[item(price=100_000, rate=0)])
    iid = uuid.UUID(inv["id"])
    cases = [
        (0, "unpaid", 100_000, 0),
        (40_000, "partially_paid", 60_000, 0),
        (100_000, "paid", 0, 0),
        (130_000, "paid", 0, 30_000),
    ]
    for paid, status, due, over in cases:
        await sql.execute("UPDATE invoices SET amount_paid_paise=$2 WHERE id=$1", iid, paid)
        got = (await fin.get(f"{I}/{inv['id']}")).json()
        assert (got["payment_status"], got["amount_due_paise"], got["overpaid_paise"]) == (
            status,
            due,
            over,
        )


async def test_overdue_is_judged_on_the_ist_calendar_date(
    fin: httpx.AsyncClient, clock: FixedClock, sql: Any
) -> None:
    clock.set(at("2026-11-02T04:00:00"))
    inv = await issued(fin, await new_customer(fin), payment_terms_days=10)  # due 2026-11-12
    assert inv["due_date"] == "2026-11-12"

    async def state() -> dict[str, Any]:
        return dict((await fin.get(f"{I}/{inv['id']}")).json())

    clock.set(at("2026-11-12T17:29:00"))  # 22:59 IST on the due date
    assert (await state())["is_overdue"] is False  # due today is not overdue
    clock.set(at("2026-11-12T18:31:00"))  # 00:01 IST on 13 Nov, although UTC is still 12 Nov
    s = await state()
    assert (s["is_overdue"], s["days_overdue"], s["aging_bucket"]) == (True, 1, "1-30")
    clock.set(at("2027-02-20T00:00:00"))  # 100 days past due
    s = await state()
    assert (s["days_overdue"], s["aging_bucket"]) == (100, "90+")
    await sql.execute(
        "UPDATE invoices SET amount_paid_paise = total_paise WHERE id=$1", uuid.UUID(inv["id"])
    )
    s = await state()
    assert (s["is_overdue"], s["days_overdue"], s["aging_bucket"]) == (
        False,
        0,
        "not_due",
    )  # settled invoices are not overdue


@pytest.mark.parametrize(
    ("days_late", "bucket"),
    [
        (1, "1-30"),
        (30, "1-30"),
        (31, "31-60"),
        (60, "31-60"),
        (61, "61-90"),
        (90, "61-90"),
        (91, "90+"),
    ],
)
async def test_aging_bucket_edges(
    fin: httpx.AsyncClient, clock: FixedClock, days_late: int, bucket: str
) -> None:
    clock.set(at("2026-06-01T06:00:00"))
    inv = await issued(
        fin, await new_customer(fin), payment_terms_days=0
    )  # due the day it is issued
    clock.set(at("2026-06-01T06:00:00") + timedelta(days=days_late))
    got = (await fin.get(f"{I}/{inv['id']}")).json()
    assert got["days_overdue"] == days_late
    assert got["aging_bucket"] == bucket


async def test_statutory_flag_depends_on_tenant_category_and_agreement(
    world: World, make_client: ClientFactory, fin: httpx.AsyncClient
) -> None:
    admin = await login_as(make_client, world.a["admin"])
    await admin.patch("/api/v1/org", json={"udyam_category": "small"})
    no_agreement = await new_customer(fin, has_written_agreement=False)
    agreement = await new_customer(fin, has_written_agreement=True)
    a = await new_draft(fin, no_agreement, payment_terms_days=30)
    b = await new_draft(fin, agreement, payment_terms_days=30)
    c = await new_draft(fin, agreement, payment_terms_days=45)
    assert (a["statutory_limit_days"], a["terms_exceed_statutory"]) == (15, True)
    assert (b["statutory_limit_days"], b["terms_exceed_statutory"]) == (45, False)
    assert c["terms_exceed_statutory"] is False
    await admin.patch("/api/v1/org", json={"udyam_category": "medium"})
    again = (await fin.get(f"{I}/{a['id']}")).json()
    assert (again["statutory_limit_days"], again["terms_exceed_statutory"]) == (None, False)


# ----------------------------------------------------------------------------- lists


async def test_list_filters_search_sort_and_paging(
    fin: httpx.AsyncClient, clock: FixedClock, sql: Any
) -> None:
    clock.set(at("2026-11-02T04:00:00"))
    c1 = await new_customer(fin)
    await fin.patch(f"{C}/{c1}", json={"name": "Zephyr Industries"})
    c2 = await new_customer(fin)
    i1 = await issued(fin, c1, items=[item(price=100_000)])
    clock.set(at("2026-11-05T04:00:00"))
    i2 = await issued(fin, c2, items=[item(price=300_000)], payment_terms_days=5)
    d3 = await new_draft(fin, c2, [item(price=200_000)])
    await sql.execute(
        "UPDATE invoices SET amount_paid_paise = total_paise WHERE id=$1", uuid.UUID(i1["id"])
    )

    async def ids(**params: Any) -> list[str]:
        r = await fin.get(I, params=params)
        assert r.status_code == 200, r.text
        return [x["id"] for x in r.json()["items"]]

    assert set(await ids()) == {i1["id"], i2["id"], d3["id"]}
    assert await ids(status="draft") == [d3["id"]]
    assert await ids(status="issued", sort="total_paise") == [i1["id"], i2["id"]]
    assert await ids(status="issued", sort="-total_paise") == [i2["id"], i1["id"]]
    assert await ids(payment_status="paid") == [i1["id"]]
    assert set(await ids(payment_status="unpaid")) == {i2["id"], d3["id"]}
    assert await ids(customer_id=c1) == [i1["id"]]
    assert await ids(q="zephyr") == [i1["id"]]
    assert await ids(q=i2["number"]) == [i2["id"]]
    assert await ids(issue_from="2026-11-03") == [i2["id"]]
    assert await ids(issue_to="2026-11-04") == [i1["id"]]
    clock.set(at("2026-11-30T04:00:00"))
    assert await ids(overdue="true") == [i2["id"]]  # i1 is fully paid, so it is not overdue
    assert i2["id"] not in await ids(overdue="false")
    page = (await fin.get(I, params={"limit": 2, "offset": 0, "sort": "number"})).json()
    assert (page["total"], len(page["items"])) == (3, 2)
    assert (await fin.get(I, params={"limit": 2, "offset": 2, "sort": "number"})).json()["items"][
        0
    ]["id"] == d3["id"]  # NULL number last


async def test_sort_field_is_whitelisted(fin: httpx.AsyncClient) -> None:
    for bad in ("password_hash", "id; DROP TABLE invoices", "-org_id", ""):
        assert (await fin.get(I, params={"sort": bad})).status_code == 422
    for good in SORT_FIELDS:
        assert (await fin.get(I, params={"sort": good})).status_code == 200
        assert (await fin.get(I, params={"sort": f"-{good}"})).status_code == 200


def test_repository_sort_columns_match_the_public_whitelist() -> None:
    from app.repositories.invoices import SORT_COLUMNS

    assert set(SORT_COLUMNS) == set(SORT_FIELDS)


async def test_list_filters_reject_bad_values(fin: httpx.AsyncClient) -> None:
    for params in (
        {"status": "paid"},
        {"payment_status": "late"},
        {"issue_from": "yesterday"},
        {"overdue": "maybe"},
    ):
        assert (await fin.get(I, params=params)).status_code == 422


# ----------------------------------------------------------------------------- permissions and audit


async def test_roles_and_invoice_actions(world: World, make_client: ClientFactory) -> None:
    sales = await login_as(make_client, world.a["sales"])
    fin = await login_as(make_client, world.a["finance"])
    cust = await new_customer(sales)
    draft = await new_draft(sales, cust)  # sales may draft
    assert (await sales.get(f"{I}/{draft['id']}")).status_code == 200
    assert (
        await sales.post(f"{I}/preview", json={"customer_id": cust, "items": [item()]})
    ).status_code == 200
    assert (await sales.post(f"{I}/{draft['id']}/issue")).status_code == 403
    assert (await sales.post(f"{I}/{draft['id']}/pay-link")).status_code == 403
    inv = (await fin.post(f"{I}/{draft['id']}/issue")).json()
    assert (await sales.post(f"{I}/{inv['id']}/void", json={"reason": "x"})).status_code == 403
    assert (await fin.post(f"{I}/{inv['id']}/pay-link")).status_code == 201


async def test_audit_trail_for_the_whole_lifecycle(fin: httpx.AsyncClient, sql: Any) -> None:
    draft = await new_draft(fin, await new_customer(fin))
    await fin.put(
        f"{I}/{draft['id']}",
        json={
            "version": 1,
            "payment_terms_days": 30,
            "place_of_supply_state_code": "33",
            "items": [item(price=5)],
        },
    )
    await fin.post(f"{I}/{draft['id']}/issue")
    await fin.post(f"{I}/{draft['id']}/pay-link")
    await fin.post(f"{I}/{draft['id']}/void", json={"reason": "audit me"})
    rows = await sql.fetch(
        "SELECT action, after FROM audit_logs WHERE entity_id=$1 ORDER BY created_at",
        uuid.UUID(draft["id"]),
    )
    assert [r["action"] for r in rows] == [
        "invoice.create",
        "invoice.update",
        "invoice.issue",
        "invoice.pay_link",
        "invoice.void",
    ]
    assert "audit me" in rows[-1]["after"]


# ----------------------------------------------------------------------------- pay link


async def test_pay_link_is_shown_once_and_only_its_hash_is_stored(
    fin: httpx.AsyncClient, sql: Any, clock: FixedClock
) -> None:
    clock.set(at("2026-11-02T04:00:00"))
    inv = await issued(fin, await new_customer(fin))
    r = await fin.post(f"{I}/{inv['id']}/pay-link")
    assert r.status_code == 201
    url = r.json()["url"]
    token = url.rsplit("/pay/", 1)[1]
    assert url.startswith("http://localhost:8000/pay/rvp_")
    assert r.json()["expires_at"].startswith("2026-12-02T04:00:00")  # 30 days
    stored = await sql.val("SELECT pay_token_hash FROM invoices WHERE id=$1", uuid.UUID(inv["id"]))
    assert stored == hashlib.sha256(token.encode()).hexdigest()
    assert token not in str(
        await sql.fetch("SELECT * FROM invoices WHERE id=$1", uuid.UUID(inv["id"]))
    )
    assert token not in str(
        await sql.fetch("SELECT after FROM audit_logs WHERE entity_id=$1", uuid.UUID(inv["id"]))
    )
    detail = (await fin.get(f"{I}/{inv['id']}")).text
    assert token not in detail
    assert "pay_token" not in detail
    assert "pay_link_active" in detail


async def test_regenerating_replaces_the_hash(fin: httpx.AsyncClient, sql: Any) -> None:
    inv = await issued(fin, await new_customer(fin))
    await fin.post(f"{I}/{inv['id']}/pay-link")
    first = await sql.val("SELECT pay_token_hash FROM invoices WHERE id=$1", uuid.UUID(inv["id"]))
    await fin.post(f"{I}/{inv['id']}/pay-link")
    second = await sql.val("SELECT pay_token_hash FROM invoices WHERE id=$1", uuid.UUID(inv["id"]))
    assert first != second


async def test_pay_link_needs_an_issued_invoice_with_something_to_pay(
    fin: httpx.AsyncClient, sql: Any
) -> None:
    draft = await new_draft(fin, await new_customer(fin))
    assert (await fin.post(f"{I}/{draft['id']}/pay-link")).status_code == 409
    inv = await issued(fin, await new_customer(fin))
    await sql.execute(
        "UPDATE invoices SET amount_paid_paise = total_paise WHERE id=$1", uuid.UUID(inv["id"])
    )
    assert (await fin.post(f"{I}/{inv['id']}/pay-link")).status_code == 409


async def test_pay_link_expiry_is_reflected_in_active_flag(
    fin: httpx.AsyncClient, clock: FixedClock
) -> None:
    clock.set(at("2026-11-02T04:00:00"))
    inv = await issued(fin, await new_customer(fin))
    await fin.post(f"{I}/{inv['id']}/pay-link")
    assert (await fin.get(f"{I}/{inv['id']}")).json()["pay_link_active"] is True
    clock.set(at("2026-12-03T04:00:00"))
    assert (await fin.get(f"{I}/{inv['id']}")).json()["pay_link_active"] is False


# ----------------------------------------------------------------------------- from a deal


async def test_won_deal_creates_one_draft_invoice(world: World, make_client: ClientFactory) -> None:
    sales = await login_as(make_client, world.a["sales"])
    cust = await new_customer(sales, "27")
    deal = (
        await sales.post(
            D,
            json={"customer_id": cust, "title": "Annual support", "amount_estimate_paise": 250_000},
        )
    ).json()
    early = await sales.post(f"{D}/{deal['id']}/create-invoice")
    assert early.status_code == 409
    for stage in ("qualified", "proposal", "won"):
        await sales.post(f"{D}/{deal['id']}/transition", json={"stage": stage})
    r = await sales.post(f"{D}/{deal['id']}/create-invoice")
    assert r.status_code == 201, r.text
    inv = r.json()
    assert (inv["status"], inv["deal_id"], inv["customer_id"]) == ("draft", deal["id"], cust)
    assert [(x["description"], x["unit_price_paise"], x["gst_rate_bp"]) for x in inv["items"]] == [
        ("Annual support", 250_000, 1800)
    ]
    assert inv["supply_type"] is None
    assert inv["igst_paise"] == 45_000
    again = await sales.post(f"{D}/{deal['id']}/create-invoice")
    assert again.status_code == 409
    assert again.json()["code"] == "conflict"
    fin = await login_as(make_client, world.a["finance"])
    await fin.post(f"{I}/{inv['id']}/issue")
    await fin.post(f"{I}/{inv['id']}/void", json={"reason": "redo"})
    assert (
        await sales.post(f"{D}/{deal['id']}/create-invoice")
    ).status_code == 201  # a voided invoice frees the deal


async def test_deal_must_belong_to_the_invoiced_customer(
    world: World, make_client: ClientFactory
) -> None:
    c = await login_as(make_client, world.a["admin"])
    c1, c2 = await new_customer(c), await new_customer(c)
    deal = (await c.post(D, json={"customer_id": c1, "title": "x"})).json()
    r = await c.post(I, json={"customer_id": c2, "deal_id": deal["id"], "items": [item()]})
    assert r.status_code == 422
    assert (
        await c.post(I, json={"customer_id": c1, "deal_id": str(uuid.uuid4()), "items": [item()]})
    ).status_code == 404


async def test_unauthenticated_invoice_access_is_refused(client: httpx.AsyncClient) -> None:
    for method, path in (
        ("GET", I),
        ("POST", I),
        ("GET", f"{I}/{uuid.uuid4()}"),
        ("POST", f"{I}/{uuid.uuid4()}/issue"),
    ):
        assert (await client.request(method, path)).status_code == 401


async def test_database_agrees_with_the_api_totals(fin: httpx.AsyncClient, sql: Any) -> None:
    inv = await issued(
        fin,
        await new_customer(fin),
        items=[item(qty=3, price=12_345, rate=1800), item(price=99, rate=500)],
    )
    row = await sql.fetch(
        "SELECT subtotal_paise, cgst_paise, sgst_paise, igst_paise, total_paise FROM invoices WHERE id=$1",
        uuid.UUID(inv["id"]),
    )
    sums = await sql.fetch(
        "SELECT sum(taxable_paise) t, sum(cgst_paise) c, sum(sgst_paise) s, sum(igst_paise) i, sum(line_total_paise) l FROM invoice_items WHERE invoice_id=$1",
        uuid.UUID(inv["id"]),
    )
    assert tuple(row[0]) == (sums[0]["t"], sums[0]["c"], sums[0]["s"], sums[0]["i"], sums[0]["l"])
