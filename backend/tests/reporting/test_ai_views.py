"""The `ai` schema contract (02 section 3) and 07 sections 1.2, 1.3 and 1.4, against real PostgreSQL."""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg
import pytest

from app.db.contract_sql import AI_VIEW_NAMES
from tests.reporting.conftest import Seeded
from tests.reporting.truth import Truth

# column name -> data type, exactly as 02 section 3.1 lists them (the contract).
CONTRACT: dict[str, dict[str, str]] = {
    "v_invoices": {
        "org_id": "uuid",
        "invoice_id": "uuid",
        "invoice_number": "text",
        "customer_id": "uuid",
        "customer_name": "text",
        "status": "text",
        "payment_status": "text",
        "issue_date": "date",
        "due_date": "date",
        "payment_terms_days": "integer",
        "total_paise": "bigint",
        "amount_paid_paise": "bigint",
        "amount_due_paise": "bigint",
        "overpaid_paise": "bigint",
        "is_overdue": "boolean",
        "days_overdue": "integer",
        "aging_bucket": "text",
        "statutory_limit_days": "integer",
        "terms_exceed_statutory": "boolean",
    },
    "v_customers": {
        "org_id": "uuid",
        "customer_id": "uuid",
        "name": "text",
        "state_code": "text",
        "has_written_agreement": "boolean",
        "default_terms_days": "integer",
        "invoices_count": "integer",
        "total_billed_paise": "bigint",
        "total_paid_paise": "bigint",
        "outstanding_paise": "bigint",
        "paid_invoices_count": "integer",
        "late_paid_invoices_count": "integer",
        "late_payment_rate_bp": "integer",
        "avg_days_to_pay": "numeric",
        "notes": "text",
    },
    "v_payments": {
        "org_id": "uuid",
        "payment_id": "uuid",
        "invoice_id": "uuid",
        "provider": "text",
        "amount_paise": "bigint",
        "status": "text",
        "method": "text",
        "captured_at": "timestamp with time zone",
        "paid_on": "date",
    },
    "v_deals": {
        "org_id": "uuid",
        "deal_id": "uuid",
        "customer_id": "uuid",
        "customer_name": "text",
        "title": "text",
        "stage": "text",
        "amount_estimate_paise": "bigint",
        "created_at": "timestamp with time zone",
        "stage_changed_at": "timestamp with time zone",
        "closed_at": "timestamp with time zone",
    },
    "v_collections_daily": {
        "org_id": "uuid",
        "day": "date",
        "billed_paise": "bigint",
        "collected_paise": "bigint",
        "invoices_issued": "integer",
        "payments_count": "integer",
    },
    "v_receivables_summary": {
        "org_id": "uuid",
        "as_of_date": "date",
        "outstanding_paise": "bigint",
        "overdue_paise": "bigint",
        "billed_last_30d_paise": "bigint",
        "dso_30d": "numeric",
    },
    "v_webhook_health": {
        "org_id": "uuid",
        "provider": "text",
        "event_type": "text",
        "processing_status": "text",
        "event_count_7d": "integer",
        "last_seen_at": "timestamp with time zone",
        "last_error": "text",
    },
}


async def as_tenant(
    conn: asyncpg.Connection, org: str | None, query: str, *args: Any
) -> list[asyncpg.Record]:
    """Exactly how the MCP server will do it: a transaction, `set_config(..., true)` with a bound parameter."""
    async with conn.transaction():
        if org is not None:
            await conn.execute("SELECT set_config('app.current_org', $1, true)", org)
        return await conn.fetch(query, *args)


@pytest.fixture
async def ai(seeded: Seeded) -> Any:
    conn = await asyncpg.connect(seeded.urls.ai)
    yield conn
    await conn.close()


@pytest.fixture
async def app_conn(seeded: Seeded) -> Any:
    conn = await asyncpg.connect(seeded.urls.app)
    yield conn
    await conn.close()


# ----------------------------------------------------------------------------- shape of the contract


async def test_every_view_has_exactly_the_contract_columns_and_types(seeded: Seeded) -> None:
    conn = await seeded.connect()
    try:
        for view, expected in CONTRACT.items():
            rows = await conn.fetch(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema='ai' AND table_name=$1 ORDER BY ordinal_position",
                view,
            )
            assert [(r["column_name"], r["data_type"]) for r in rows] == list(expected.items()), (
                view
            )
    finally:
        await conn.close()
    assert set(CONTRACT) == set(AI_VIEW_NAMES)


async def test_every_ai_view_is_a_security_barrier_view_ending_in_the_tenant_filter(
    seeded: Seeded,
) -> None:
    conn = await seeded.connect()
    try:
        for view in AI_VIEW_NAMES:
            options = await conn.fetchval(
                "SELECT reloptions FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='ai' AND c.relname=$1",
                view,
            )
            assert options is not None
            assert "security_barrier=true" in options, view
            definition = await conn.fetchval("SELECT pg_get_viewdef($1::regclass)", f"ai.{view}")
            assert "app.current_org" in definition, view
            assert "CURRENT_DATE" not in definition.upper(), (
                view
            )  # IST logic must not use the server's date
    finally:
        await conn.close()


# ----------------------------------------------------------------------------- isolation (fail closed)


@pytest.mark.parametrize("view", AI_VIEW_NAMES)
async def test_unset_setting_returns_zero_rows_on_every_view(
    ai: asyncpg.Connection, view: str
) -> None:
    assert await as_tenant(ai, None, f"SELECT count(*) AS n FROM ai.{view}") == [(0,)]


@pytest.mark.parametrize("view", AI_VIEW_NAMES)
async def test_empty_string_setting_also_fails_closed(ai: asyncpg.Connection, view: str) -> None:
    assert await as_tenant(ai, "", f"SELECT count(*) AS n FROM ai.{view}") == [(0,)]


@pytest.mark.parametrize("view", AI_VIEW_NAMES)
@pytest.mark.parametrize("org", ["a", "b"])
async def test_a_tenant_setting_yields_only_that_tenants_rows(
    seeded: Seeded, ai: asyncpg.Connection, view: str, org: str
) -> None:
    rows = await as_tenant(ai, seeded.org_ids[org], f"SELECT DISTINCT org_id FROM ai.{view}")
    assert [str(r["org_id"]) for r in rows] == [seeded.org_ids[org]]


async def test_the_setting_is_transaction_local_and_does_not_leak_to_the_next_transaction(
    seeded: Seeded, ai: asyncpg.Connection
) -> None:
    assert len(await as_tenant(ai, seeded.org_ids["a"], "SELECT 1 FROM ai.v_invoices LIMIT 1")) == 1
    assert await as_tenant(ai, None, "SELECT count(*) FROM ai.v_invoices") == [(0,)]


async def test_dropping_the_explicit_org_predicate_still_returns_only_the_current_tenant(
    seeded: Seeded, ai: asyncpg.Connection
) -> None:
    """07 1.4: tool SQL also filters explicitly; the view is the backstop. Without the explicit filter: still one tenant."""
    unfiltered = await as_tenant(ai, seeded.org_ids["a"], "SELECT count(*) AS n FROM ai.v_invoices")
    assert unfiltered[0]["n"] == 140
    both = await as_tenant(
        ai,
        seeded.org_ids["a"],
        "SELECT count(*) AS n FROM ai.v_invoices WHERE org_id = $1::uuid",
        uuid.UUID(seeded.org_ids["b"]),
    )
    assert both[0]["n"] == 0  # asking for the other tenant's rows explicitly yields nothing


async def test_a_leaky_predicate_is_not_evaluated_before_the_tenant_filter(
    seeded: Seeded, ai: asyncpg.Connection
) -> None:
    """security_barrier: a user-supplied function in WHERE only ever sees rows that passed the tenant filter."""
    owner = await seeded.connect()
    try:
        await owner.execute(
            "CREATE OR REPLACE FUNCTION public.leaky(text) RETURNS boolean LANGUAGE plpgsql COST 0.0001 AS "
            "$$ BEGIN IF $1 IS NOT NULL AND $1 LIKE '%(demo)%' THEN PERFORM set_config('leak.seen', "
            "(coalesce(nullif(current_setting('leak.seen', true), '')::int, 0) + 1)::text, false); END IF; RETURN true; END $$"
        )
        await owner.execute("GRANT EXECUTE ON FUNCTION public.leaky(text) TO ai_reader")
        await owner.execute("GRANT USAGE ON SCHEMA public TO ai_reader")
        await as_tenant(
            ai, seeded.org_ids["a"], "SELECT count(*) FROM ai.v_customers WHERE public.leaky(name)"
        )
        async with ai.transaction():
            await ai.execute("SELECT set_config('leak.seen', '', false)")
            await ai.execute("SELECT set_config('app.current_org', $1, true)", seeded.org_ids["a"])
            await ai.fetch("SELECT count(*) FROM ai.v_customers WHERE public.leaky(name)")
            seen = int(
                await ai.fetchval(
                    "SELECT coalesce(nullif(current_setting('leak.seen', true), ''), '0')::int"
                )
            )
        assert (
            seen == 25
        )  # exactly Org A's 25 customers; Org B's rows were never shown to the function
    finally:
        await owner.execute("REVOKE USAGE ON SCHEMA public FROM ai_reader")
        await owner.execute("DROP FUNCTION IF EXISTS public.leaky(text)")
        await owner.close()


# ----------------------------------------------------------------------------- privileges (07 1.2 and 1.3)


@pytest.mark.parametrize(
    "table",
    [
        "invoices",
        "customers",
        "payments",
        "deals",
        "users",
        "webhook_events",
        "audit_logs",
        "mv_collections_daily",
    ],
)
async def test_ai_reader_cannot_read_any_public_object(ai: asyncpg.Connection, table: str) -> None:
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await ai.fetch(f"SELECT 1 FROM public.{table} LIMIT 1")


async def test_ai_reader_is_read_only_and_cannot_create_or_refresh(ai: asyncpg.Connection) -> None:
    for statement in (
        "DELETE FROM ai.v_payments",  # an auto-updatable view: only the missing privilege stops this
        "UPDATE ai.v_deals SET title = 'x'",
        "DELETE FROM ai.v_invoices",  # not updatable at all
        "UPDATE ai.v_customers SET name = 'x'",
        "CREATE TABLE ai.evil (id int)",
        "CREATE TABLE public.evil (id int)",
        "REFRESH MATERIALIZED VIEW public.mv_collections_daily",
    ):
        with pytest.raises(
            (
                asyncpg.InsufficientPrivilegeError,
                asyncpg.UndefinedTableError,
                asyncpg.ObjectNotInPrerequisiteStateError,
            )
        ):
            await ai.execute(statement)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await ai.fetchval("SELECT public.refresh_collections_mv()")


async def test_runtime_role_can_call_the_definer_function_but_not_refresh_or_write_the_view_itself(
    app_conn: asyncpg.Connection,
) -> None:
    await app_conn.fetchval(
        "SELECT public.refresh_collections_mv()"
    )  # the definer function works for revops_app
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_conn.execute("REFRESH MATERIALIZED VIEW public.mv_collections_daily")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_conn.execute(
            "INSERT INTO public.mv_collections_daily VALUES (gen_random_uuid(), now()::date, 1, 1, 1, 1)"
        )
    assert (
        await app_conn.fetchval("SELECT count(*) FROM public.mv_collections_daily") > 0
    )  # readable
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_conn.fetch(
            "SELECT 1 FROM ai.v_invoices LIMIT 1"
        )  # the runtime role has no business in ai


async def test_nobody_else_can_execute_the_refresh_function(seeded: Seeded) -> None:
    conn = await seeded.connect()
    try:
        rows = await conn.fetch(
            "SELECT r, has_function_privilege(r, 'public.refresh_collections_mv()', 'EXECUTE') AS ok "
            "FROM unnest(ARRAY['revops_app','ai_reader']) r"
        )
        assert {r["r"]: r["ok"] for r in rows} == {"revops_app": True, "ai_reader": False}
        definer = await conn.fetchrow(
            "SELECT prosecdef, proconfig FROM pg_proc WHERE proname = 'refresh_collections_mv'"
        )
        assert definer is not None
        assert definer["prosecdef"] is True
        assert any(
            "search_path" in c for c in definer["proconfig"]
        )  # pinned: no search_path hijack of a definer function
    finally:
        await conn.close()


async def test_the_materialized_view_has_the_unique_index_concurrent_refresh_needs(
    seeded: Seeded,
) -> None:
    conn = await seeded.connect()
    try:
        row = await conn.fetchrow(
            "SELECT indexdef FROM pg_indexes WHERE schemaname='public' AND tablename='mv_collections_daily' AND indexname='uq_mv_collections_daily'"
        )
        assert row is not None
        assert "UNIQUE" in row["indexdef"]
        assert (
            await conn.fetchval(
                "SELECT ispopulated FROM pg_matviews WHERE matviewname='mv_collections_daily'"
            )
            is True
        )
    finally:
        await conn.close()


# ----------------------------------------------------------------------------- semantics against ground truth


async def test_v_invoices_semantics(seeded: Seeded, ai: asyncpg.Connection) -> None:
    for org, limit_org in (("a", "small"), ("b", "micro")):
        assert limit_org in ("small", "micro")
        rows = await as_tenant(ai, seeded.org_ids[org], "SELECT * FROM ai.v_invoices")
        assert len(rows) == 140
        by_status = {s: [r for r in rows if r["status"] == s] for s in ("draft", "issued", "void")}
        assert {s: len(v) for s, v in by_status.items()} == {"draft": 8, "issued": 124, "void": 8}
        for r in by_status["draft"] + by_status["void"]:
            assert (r["is_overdue"], r["days_overdue"], r["aging_bucket"], r["payment_status"]) == (
                False,
                0,
                "not_due",
                None,
            )
        buckets = {r["aging_bucket"] for r in by_status["issued"] if r["is_overdue"]}
        assert buckets == {"1-30", "31-60", "61-90", "90+"}
        for r in by_status["issued"]:
            assert r["amount_due_paise"] == max(r["total_paise"] - r["amount_paid_paise"], 0)
            assert r["overpaid_paise"] == max(r["amount_paid_paise"] - r["total_paise"], 0)
            expected_status = (
                "unpaid"
                if r["amount_paid_paise"] == 0
                else "partially_paid"
                if r["amount_paid_paise"] < r["total_paise"]
                else "paid"
            )
            assert r["payment_status"] == expected_status
            if r["payment_status"] == "paid":
                assert r["is_overdue"] is False
            assert r["is_overdue"] == (r["days_overdue"] > 0)
            if r["is_overdue"]:
                assert r["days_overdue"] == (seeded.today - r["due_date"]).days
            assert r["statutory_limit_days"] in (15, 45)  # both seeded tenants are micro or small
            assert r["terms_exceed_statutory"] == (
                r["payment_terms_days"] > r["statutory_limit_days"]
            )
        assert (
            sum(1 for r in by_status["issued"] if r["overpaid_paise"] > 0) == 1
        )  # one flagged overpayment per org


async def test_statutory_limit_follows_the_written_agreement_flag(
    seeded: Seeded, ai: asyncpg.Connection
) -> None:
    rows = await as_tenant(
        ai, seeded.org_ids["a"],
        "SELECT i.statutory_limit_days, c.has_written_agreement FROM ai.v_invoices i "
        "JOIN ai.v_customers c ON c.customer_id = i.customer_id",
    )  # fmt: skip
    assert rows
    assert all(
        r["statutory_limit_days"] == (45 if r["has_written_agreement"] else 15) for r in rows
    )


async def test_v_customers_aggregates_equal_ground_truth(
    seeded: Seeded, ai: asyncpg.Connection
) -> None:
    conn = await seeded.connect()
    try:
        truth = await Truth.load(conn, seeded.org_ids["a"], seeded.today)
    finally:
        await conn.close()
    rows = await as_tenant(ai, seeded.org_ids["a"], "SELECT * FROM ai.v_customers")
    assert len(rows) == 25
    last_paid: dict[Any, Any] = {}
    for p in truth.payments:
        if p["status"] == "captured":
            last_paid[p["invoice_id"]] = max(
                last_paid.get(p["invoice_id"], p["paid_on"]), p["paid_on"]
            )
    for r in rows:
        mine = [i for i in truth.issued() if i["customer_id"] == r["customer_id"]]
        paid = [
            i for i in mine if i["total_paise"] > 0 and i["amount_paid_paise"] >= i["total_paise"]
        ]
        late = [i for i in paid if last_paid[i["id"]] > i["due_date"]]
        assert r["invoices_count"] == len(mine)
        assert r["total_billed_paise"] == sum(i["total_paise"] for i in mine)
        assert r["total_paid_paise"] == sum(i["amount_paid_paise"] for i in mine)
        assert r["outstanding_paise"] == sum(truth.due(i) for i in mine)
        assert (r["paid_invoices_count"], r["late_paid_invoices_count"]) == (len(paid), len(late))
        assert r["late_payment_rate_bp"] == (
            (len(late) * 20000 + len(paid)) // (2 * len(paid)) if paid else 0
        )
        if paid:
            days = [(last_paid[i["id"]] - i["issue_date"]).days for i in paid]
            assert float(r["avg_days_to_pay"]) == pytest.approx(
                round(sum(days) / len(days), 2), abs=0.011
            )
        else:
            assert r["avg_days_to_pay"] is None
        assert 0 <= r["late_payment_rate_bp"] <= 10000


async def test_receivables_summary_equals_ground_truth(
    seeded: Seeded, ai: asyncpg.Connection
) -> None:
    conn = await seeded.connect()
    try:
        truth = (await Truth.load(conn, seeded.org_ids["b"], seeded.today)).summary()
    finally:
        await conn.close()
    (row,) = await as_tenant(ai, seeded.org_ids["b"], "SELECT * FROM ai.v_receivables_summary")
    assert row["as_of_date"] == seeded.today
    assert (row["outstanding_paise"], row["overdue_paise"], row["billed_last_30d_paise"]) == (
        truth["outstanding_paise"], truth["overdue_paise"], truth["billed_last_30d_paise"],
    )  # fmt: skip
    assert float(row["dso_30d"]) == truth["dso_30d"]


async def test_ai_aging_agrees_with_the_api_report(
    seeded: Seeded, seeded_login: Any, ai: asyncpg.Connection
) -> None:
    """Two independent implementations (API SQL with a bound date, views with the database clock) must agree."""
    api = (await (await seeded_login("a", "finance")).get("/api/v1/reports/aging")).json()[
        "buckets"
    ]
    rows = await as_tenant(
        ai, seeded.org_ids["a"],
        "SELECT aging_bucket, count(*) AS n, sum(amount_due_paise) AS due FROM ai.v_invoices "
        "WHERE status = 'issued' AND amount_due_paise > 0 GROUP BY 1",
    )  # fmt: skip
    from_views = {r["aging_bucket"]: (r["n"], r["due"]) for r in rows}
    assert {
        b["bucket"]: (b["invoices"], b["due_paise"]) for b in api if b["invoices"]
    } == from_views


async def test_collections_view_matches_the_payments_and_invoices(
    seeded: Seeded, ai: asyncpg.Connection
) -> None:
    conn = await seeded.connect()
    try:
        truth = await Truth.load(conn, seeded.org_ids["a"], seeded.today)
    finally:
        await conn.close()
    rows = await as_tenant(ai, seeded.org_ids["a"], "SELECT * FROM ai.v_collections_daily")
    got = {
        r["day"]: {
            k: r[k]
            for k in ("billed_paise", "collected_paise", "invoices_issued", "payments_count")
        }
        for r in rows
    }
    assert got == truth.daily()


async def test_payments_and_deals_views_return_tenant_rows_with_names(
    seeded: Seeded, ai: asyncpg.Connection
) -> None:
    payments = await as_tenant(ai, seeded.org_ids["a"], "SELECT * FROM ai.v_payments")
    assert len(payments) == 87
    assert {r["provider"] for r in payments} == {"razorpay", "manual"}
    deals = await as_tenant(ai, seeded.org_ids["a"], "SELECT * FROM ai.v_deals")
    assert len(deals) == 22
    assert all(r["customer_name"] for r in deals)


async def test_webhook_health_shows_only_resolvable_events(
    seeded: Seeded, ai: asyncpg.Connection
) -> None:
    rows = await as_tenant(ai, seeded.org_ids["a"], "SELECT * FROM ai.v_webhook_health")
    statuses = {(r["processing_status"], r["event_type"]) for r in rows}
    assert (
        "quarantined",
        "payment.captured",
    ) in statuses  # the amount-mismatch event of this tenant
    assert ("failed", "order.paid") in statuses
    quarantined = [r for r in rows if r["processing_status"] == "quarantined"]
    assert (
        sum(r["event_count_7d"] for r in quarantined) == 1
    )  # the unknown-order event has no org: invisible by design
    assert quarantined[0]["last_error"] == "amount_or_currency_mismatch"
    owner = await seeded.connect()
    try:
        hidden = await owner.fetchval("SELECT count(*) FROM webhook_events WHERE org_id IS NULL")
        assert hidden == 2  # one per org, in the table but in nobody's view
    finally:
        await owner.close()


# ----------------------------------------------------------------------------- seed fixtures the agent tests need


async def test_poison_note_and_canary_are_visible_only_where_the_contract_says(
    seeded: Seeded, ai: asyncpg.Connection
) -> None:
    a = await as_tenant(ai, seeded.org_ids["a"], "SELECT name, notes FROM ai.v_customers")
    b = await as_tenant(ai, seeded.org_ids["b"], "SELECT name, notes FROM ai.v_customers")
    poisoned = [
        r for r in a if r["notes"] and r["notes"].startswith("SYSTEM: ignore previous instructions")
    ]
    assert len(poisoned) == 1
    assert not any(r["notes"] and "SYSTEM:" in r["notes"] for r in b)
    assert any(r["name"] == "Zephyr Canary Traders (demo)" for r in b)
    assert not any(r["name"] == "Zephyr Canary Traders (demo)" for r in a)
