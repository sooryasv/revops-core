"""Report endpoints against ground truth computed independently from the raw rows (M7 acceptance)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from tests.reporting.conftest import Seeded
from tests.reporting.truth import Truth

R = "/api/v1/reports"


async def truth_for(seeded: Seeded, org: str) -> Truth:
    conn = await seeded.connect()
    try:
        return await Truth.load(conn, seeded.org_ids[org], seeded.today)
    finally:
        await conn.close()


@pytest.mark.parametrize("org", ["a", "b"])
async def test_summary_equals_ground_truth(seeded: Seeded, seeded_login: Any, org: str) -> None:
    truth = (await truth_for(seeded, org)).summary()
    client = await seeded_login(org, "finance")
    r = await client.get(f"{R}/summary")
    assert r.status_code == 200
    body = r.json()
    for key in ("outstanding_paise", "overdue_paise", "overdue_invoices", "open_invoices",
                "billed_last_30d_paise", "collected_last_30d_paise", "dso_30d", "invoices"):  # fmt: skip
        assert body[key] == truth[key], key
    assert body["as_of_date"] == seeded.today.isoformat()
    assert body["outstanding_paise"] > 0
    assert body["overdue_paise"] > 0


@pytest.mark.parametrize("org", ["a", "b"])
async def test_aging_equals_ground_truth_and_covers_every_bucket(
    seeded: Seeded, seeded_login: Any, org: str
) -> None:
    truth = (await truth_for(seeded, org)).aging()
    r = await (await seeded_login(org, "finance")).get(f"{R}/aging")
    body = r.json()
    got = {b["bucket"]: (b["invoices"], b["due_paise"]) for b in body["buckets"]}
    assert [b["bucket"] for b in body["buckets"]] == ["not_due", "1-30", "31-60", "61-90", "90+"]
    assert got == truth
    assert all(count > 0 for count, _ in got.values()), (
        "the seed must spread overdue invoices across all buckets"
    )
    assert body["total_due_paise"] == sum(amount for _, amount in truth.values())


@pytest.mark.parametrize("group", ["day", "month"])
async def test_collections_equal_ground_truth(
    seeded: Seeded, seeded_login: Any, group: str
) -> None:
    truth = await truth_for(seeded, "a")
    start = seeded.today - timedelta(days=200)
    client = await seeded_login("a", "finance")
    r = await client.get(
        f"{R}/collections",
        params={"from": start.isoformat(), "to": seeded.today.isoformat(), "group": group},
    )
    assert r.status_code == 200
    rows = r.json()["rows"]
    daily = {d: v for d, v in truth.daily().items() if start <= d <= seeded.today}
    if group == "day":
        expected = {d.isoformat(): v for d, v in daily.items()}
    else:
        expected = {}
        for d, v in daily.items():
            bucket = expected.setdefault(d.strftime("%Y-%m"), dict.fromkeys(v, 0))
            for k, n in v.items():
                bucket[k] += n
    got = {
        row["period"]: {
            k: row[k]
            for k in ("billed_paise", "collected_paise", "invoices_issued", "payments_count")
        }
        for row in rows
    }
    assert got == expected
    assert [row["period"] for row in rows] == sorted(got)


async def test_collections_validates_the_range(seeded: Seeded, seeded_login: Any) -> None:
    client = await seeded_login("a", "finance")
    assert (
        await client.get(f"{R}/collections", params={"from": "2026-05-01", "to": "2026-04-01"})
    ).status_code == 422
    assert (
        await client.get(f"{R}/collections", params={"from": "2020-01-01", "to": "2026-04-01"})
    ).status_code == 422
    assert (await client.get(f"{R}/collections", params={"group": "year"})).status_code == 422
    assert (await client.get(f"{R}/collections", params={"from": "not-a-date"})).status_code == 422
    default = await client.get(f"{R}/collections")
    assert default.status_code == 200
    assert default.json()["date_from"] == (seeded.today - timedelta(days=89)).isoformat()


@pytest.mark.parametrize("months", [1, 6, 12])
async def test_dso_trend_equals_ground_truth(
    seeded: Seeded, seeded_login: Any, months: int
) -> None:
    truth = await truth_for(seeded, "a")
    r = await (await seeded_login("a", "finance")).get(f"{R}/dso", params={"months": months})
    body = r.json()
    assert body["months"] == truth.dso_months(months)
    assert body["current_dso_30d"] == truth.summary()["dso_30d"]
    assert len(body["months"]) == months


async def test_dso_months_bounds(seeded: Seeded, seeded_login: Any) -> None:
    client = await seeded_login("a", "finance")
    for bad in (0, 13, -1):
        assert (await client.get(f"{R}/dso", params={"months": bad})).status_code == 422


@pytest.mark.parametrize("org", ["a", "b"])
async def test_pipeline_equals_ground_truth(seeded: Seeded, seeded_login: Any, org: str) -> None:
    truth = (await truth_for(seeded, org)).pipeline()
    r = await (await seeded_login(org, "sales")).get(f"{R}/pipeline")
    assert r.status_code == 200
    body = r.json()
    assert body["by_stage"] == truth["by_stage"]
    for key in ("open_value_paise", "won_last_90d", "lost_last_90d_count", "win_rate_bp_90d"):
        assert body[key] == truth[key], key
    assert [d["days_in_stage"] for d in body["stale_open_deals"]] == truth["stale_days"]
    assert len(body["stale_open_deals"]) <= 10


async def test_report_permissions_match_the_matrix(seeded: Seeded, seeded_login: Any) -> None:
    sales = await seeded_login("a", "sales")
    finance = await seeded_login("a", "finance")
    admin = await seeded_login("a", "admin")
    for path in ("summary", "aging", "collections", "dso"):
        assert (await sales.get(f"{R}/{path}")).status_code == 403, path
        assert (await finance.get(f"{R}/{path}")).status_code == 200, path
        assert (await admin.get(f"{R}/{path}")).status_code == 200, path
    for client in (sales, finance, admin):
        assert (await client.get(f"{R}/pipeline")).status_code == 200
    anonymous = await seeded_login("a", "admin")
    anonymous.cookies.clear()
    assert (await anonymous.get(f"{R}/summary")).status_code == 401


async def test_each_tenant_sees_only_its_own_numbers(seeded: Seeded, seeded_login: Any) -> None:
    a = (await (await seeded_login("a", "finance")).get(f"{R}/summary")).json()
    b = (await (await seeded_login("b", "finance")).get(f"{R}/summary")).json()
    assert a["outstanding_paise"] != b["outstanding_paise"]
    assert a["outstanding_paise"] == (await truth_for(seeded, "a")).summary()["outstanding_paise"]
    assert b["outstanding_paise"] == (await truth_for(seeded, "b")).summary()["outstanding_paise"]


async def test_collections_come_from_the_materialized_view_until_refreshed(
    seeded: Seeded, seeded_login: Any
) -> None:
    """A payment inserted behind the application's back is invisible until the refresh endpoint runs."""
    import uuid

    conn = await seeded.connect()
    try:
        invoice = await conn.fetchrow(
            "SELECT id, org_id FROM invoices WHERE org_id=$1 AND status='issued' AND total_paise > amount_paid_paise LIMIT 1",
            uuid.UUID(seeded.org_ids["a"]),
        )
        assert invoice is not None
        client = await seeded_login("a", "finance")
        day = seeded.today.isoformat()
        before = (await client.get(f"{R}/collections", params={"from": day, "to": day})).json()[
            "rows"
        ]
        collected_before = sum(r["collected_paise"] for r in before)
        await conn.execute(
            "INSERT INTO payments (id, org_id, invoice_id, provider, provider_payment_id, amount_paise, status, method, captured_at, paid_on)"
            " VALUES ($1,$2,$3,'manual',$4,1234,'captured','cash', now(), $5)",
            uuid.uuid4(), invoice["org_id"], invoice["id"], f"manual_{uuid.uuid4().hex}", seeded.today,
        )  # fmt: skip
        stale = (await client.get(f"{R}/collections", params={"from": day, "to": day})).json()[
            "rows"
        ]
        assert sum(r["collected_paise"] for r in stale) == collected_before
        assert (
            await (await seeded_login("a", "finance")).post("/api/v1/admin/reporting/refresh")
        ).status_code == 403
        admin = await seeded_login("a", "admin")
        refreshed = await admin.post("/api/v1/admin/reporting/refresh")
        assert refreshed.status_code == 200
        assert "refreshed_at" in refreshed.json()
        fresh = (await client.get(f"{R}/collections", params={"from": day, "to": day})).json()[
            "rows"
        ]
        assert sum(r["collected_paise"] for r in fresh) == collected_before + 1234
        audit = await conn.fetchval(
            "SELECT count(*) FROM audit_logs WHERE org_id=$1 AND action='reporting.refreshed'",
            invoice["org_id"],
        )
        assert audit == 1
    finally:
        await conn.execute("DELETE FROM payments WHERE amount_paise = 1234 AND provider = 'manual'")
        await conn.execute("SELECT public.refresh_collections_mv()")
        await conn.close()


async def test_dashboard_extras_equal_ground_truth(seeded: Seeded, seeded_login: Any) -> None:
    truth = await truth_for(seeded, "a")
    body = (await (await seeded_login("a", "finance")).get(f"{R}/summary")).json()
    per_customer: dict[Any, int] = {}
    for inv in truth.issued():
        if truth.days_overdue(inv) > 0:
            per_customer[inv["customer_id"]] = per_customer.get(inv["customer_id"], 0) + truth.due(
                inv
            )
    expected = sorted(per_customer.values(), reverse=True)[:5]
    assert [c["overdue_paise"] for c in body["top_overdue_customers"]] == expected
    captured = sorted(
        (p for p in truth.payments if p["status"] == "captured"),
        key=lambda p: (p["captured_at"], str(p["id"])),
        reverse=True,
    )[:5]
    assert [p["amount_paise"] for p in body["recent_payments"]] == [
        p["amount_paise"] for p in captured
    ]
    assert (
        body["webhook_problems_7d"] == 2
    )  # the seeded mismatch (quarantined) and failed events of this tenant
    other = (await (await seeded_login("b", "finance")).get(f"{R}/summary")).json()
    assert {c["customer_id"] for c in body["top_overdue_customers"]}.isdisjoint(
        {c["customer_id"] for c in other["top_overdue_customers"]}
    )
