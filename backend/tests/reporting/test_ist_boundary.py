"""07 section 1.4 / 8: overdue is judged on the IST calendar date, never the server's UTC date.

The views call an UNQUALIFIED `now()`. Here the exact contract DDL is applied in a scratch database with a search_path in
which `fake.now()` comes before pg_catalog, so every view binds to a clock the test controls. Nothing in the production
SQL knows about the test."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import asyncpg
import pytest_asyncio

from app.db.contract_sql import AI_VIEWS_SQL, REPORTING_SQL, statements
from tests.conftest import DbUrls, create_database, migrate

DB = "revops_ist_test"


@pytest_asyncio.fixture(scope="module")
async def clock_db() -> AsyncIterator[tuple[asyncpg.Connection, asyncpg.Connection, uuid.UUID]]:
    urls: DbUrls = await create_database(DB)
    await migrate(urls, "upgrade", "0002")
    owner = await asyncpg.connect(urls.owner)
    await owner.execute("CREATE SCHEMA fake")
    await owner.execute(
        "CREATE FUNCTION fake.now() RETURNS timestamptz LANGUAGE sql STABLE AS "
        "$$ SELECT current_setting('fake.now')::timestamptz $$"
    )
    await owner.execute("SET search_path = fake, pg_catalog, public")
    for statement in (*statements(REPORTING_SQL), *statements(AI_VIEWS_SQL)):
        await owner.execute(statement)
    org, cust = uuid.uuid4(), uuid.uuid4()
    await owner.execute(
        "INSERT INTO organizations (id,name,legal_name,state_code,udyam_category) VALUES ($1,'O','O','33','small')",
        org,
    )
    await owner.execute(
        "INSERT INTO customers (id,org_id,name,state_code) VALUES ($1,$2,'C','33')", cust, org
    )

    async def invoice(due: str, issue: str, total: int = 100_000, paid: int = 0) -> None:
        await owner.execute(
            "INSERT INTO invoices (id,org_id,customer_id,number,status,issue_date,due_date,payment_terms_days,"
            "place_of_supply_state_code,supply_type,subtotal_paise,total_paise,amount_paid_paise) "
            "VALUES ($1,$2,$3,$4,'issued',$5,$6,30,'33','intra',$7,$7,$8)",
            uuid.uuid4(), org, cust, f"INV/26-27/{uuid.uuid4().int % 10**6:06d}", date.fromisoformat(issue),
            date.fromisoformat(due), total, paid,
        )  # fmt: skip

    await invoice(due="2026-06-15", issue="2026-05-16")
    ai = await asyncpg.connect(urls.ai)
    yield ai, owner, org
    await ai.close()
    await owner.close()


async def view_at(ai: asyncpg.Connection, org: uuid.UUID, utc_instant: str) -> dict[str, Any]:
    async with ai.transaction():
        await ai.execute("SELECT set_config('fake.now', $1, true)", utc_instant)
        await ai.execute("SELECT set_config('app.current_org', $1, true)", str(org))
        row = await ai.fetchrow("SELECT is_overdue, days_overdue, aging_bucket FROM ai.v_invoices")
        summary = await ai.fetchrow(
            "SELECT as_of_date, overdue_paise FROM ai.v_receivables_summary"
        )
    assert row is not None
    assert summary is not None
    return {
        **dict(row),
        "as_of": str(summary["as_of_date"]),
        "overdue_paise": summary["overdue_paise"],
    }


async def test_the_clock_shadowing_really_drives_the_views(clock_db: Any) -> None:
    ai, _, org = clock_db
    assert (await view_at(ai, org, "2026-01-01T00:00:00Z"))["as_of"] == "2026-01-01"


async def test_due_today_in_ist_is_not_overdue_at_23_00_ist(clock_db: Any) -> None:
    ai, _, org = clock_db
    got = await view_at(ai, org, "2026-06-15T17:30:00Z")  # 23:00 IST on the due date
    assert got["as_of"] == "2026-06-15"
    assert (got["is_overdue"], got["days_overdue"], got["aging_bucket"], got["overdue_paise"]) == (
        False,
        0,
        "not_due",
        0,
    )


async def test_one_minute_before_ist_midnight_is_still_not_overdue(clock_db: Any) -> None:
    ai, _, org = clock_db
    got = await view_at(ai, org, "2026-06-15T18:29:00Z")  # 23:59 IST
    assert got["is_overdue"] is False


async def test_overdue_from_midnight_ist_even_though_the_utc_date_is_unchanged(
    clock_db: Any,
) -> None:
    """18:30 UTC on 15 June is 00:00 IST on 16 June. A CURRENT_DATE (UTC) implementation would still say 15 June."""
    ai, _, org = clock_db
    got = await view_at(ai, org, "2026-06-15T18:30:00Z")
    assert got["as_of"] == "2026-06-16"
    assert (got["is_overdue"], got["days_overdue"], got["aging_bucket"]) == (True, 1, "1-30")
    assert got["overdue_paise"] == 100_000
    later = await view_at(
        ai, org, "2026-06-15T23:30:00Z"
    )  # 05:00 IST on the 16th, UTC date still the 15th
    assert later["is_overdue"] is True


async def test_bucket_edges_in_ist_days(clock_db: Any) -> None:
    ai, _, org = clock_db
    expect = {"2026-07-15": "1-30", "2026-07-16": "31-60", "2026-08-14": "31-60", "2026-08-15": "61-90",
              "2026-09-13": "61-90", "2026-09-14": "90+"}  # fmt: skip
    for day, bucket in expect.items():
        # due 15 June: 30 days late on 15 July, 31 on 16 July, and so on (noon IST avoids any boundary doubt)
        assert (await view_at(ai, org, f"{day}T06:30:00Z"))["aging_bucket"] == bucket, day
