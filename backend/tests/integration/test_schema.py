"""M2 acceptance: constraints, composite foreign keys, grants and migration round-trip, on real PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import asyncpg
import pytest
import pytest_asyncio

from tests.conftest import DbUrls, create_database, migrate
from tests.integration.helpers import seed_customer, seed_invoice, seed_org, seed_user, uid

pytestmark = pytest.mark.integration

ISSUED = {"issue_date": date(2026, 11, 1), "due_date": date(2026, 12, 1)}


@pytest_asyncio.fixture
async def owner(db_urls: DbUrls) -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(db_urls.owner)
    yield conn
    await conn.close()


@pytest_asyncio.fixture
async def app_conn(db_urls: DbUrls) -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(db_urls.app)
    yield conn
    await conn.close()


# ----------------------------------------------------------------------------- composite FKs (tenant safety)


async def test_composite_fk_blocks_cross_tenant_deal(owner: asyncpg.Connection) -> None:
    org_a, org_b = await seed_org(owner, "A"), await seed_org(owner, "B")
    user_a = await seed_user(owner, org_a)
    customer_b = await seed_customer(owner, org_b)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await owner.execute(
            "INSERT INTO deals (id, org_id, customer_id, title, owner_user_id) VALUES ($1,$2,$3,'x',$4)",
            uid(), org_a, customer_b, user_a,
        )  # fmt: skip


async def test_composite_fk_blocks_cross_tenant_invoice(owner: asyncpg.Connection) -> None:
    org_a, org_b = await seed_org(owner), await seed_org(owner)
    customer_b = await seed_customer(owner, org_b)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await seed_invoice(owner, org_a, customer_b)


async def test_composite_fk_blocks_cross_tenant_payment_and_item(owner: asyncpg.Connection) -> None:
    org_a, org_b = await seed_org(owner), await seed_org(owner)
    inv_b = await seed_invoice(owner, org_b, await seed_customer(owner, org_b))
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await owner.execute(
            "INSERT INTO payments (id, org_id, invoice_id, provider, provider_payment_id, amount_paise, status)"
            " VALUES ($1,$2,$3,'manual',$4,100,'authorized')",
            uid(), org_a, inv_b, f"m_{uid()}",
        )  # fmt: skip
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await owner.execute(
            "INSERT INTO invoice_items (id, org_id, invoice_id, line_no, description, quantity, unit_price_paise,"
            " gst_rate_bp, taxable_paise, cgst_paise, sgst_paise, igst_paise, line_total_paise)"
            " VALUES ($1,$2,$3,1,'d',1,100,0,100,0,0,0,100)",
            uid(), org_a, inv_b,
        )  # fmt: skip


async def test_same_org_references_work(owner: asyncpg.Connection) -> None:
    org = await seed_org(owner)
    await seed_invoice(owner, org, await seed_customer(owner, org))


# ----------------------------------------------------------------------------- CHECK constraints


async def test_total_check_fires(owner: asyncpg.Connection) -> None:
    org = await seed_org(owner)
    cust = await seed_customer(owner, org)
    with pytest.raises(asyncpg.CheckViolationError, match="total_matches"):
        await seed_invoice(owner, org, cust, total_paise=1)


async def test_igst_exclusivity_check_fires(owner: asyncpg.Connection) -> None:
    org = await seed_org(owner)
    cust = await seed_customer(owner, org)
    with pytest.raises(asyncpg.CheckViolationError, match="igst_exclusive"):
        await seed_invoice(
            owner, org, cust, cgst_paise=100, sgst_paise=100, igst_paise=100,
            subtotal_paise=1000, total_paise=1300,
        )  # fmt: skip


async def test_item_checks_fire(owner: asyncpg.Connection) -> None:
    org = await seed_org(owner)
    inv = await seed_invoice(owner, org, await seed_customer(owner, org))

    async def item(**o: int) -> None:
        v = {"qty": 1, "rate": 0, "tax": 100, "cgst": 0, "sgst": 0, "igst": 0, "total": 100} | o
        await owner.execute(
            "INSERT INTO invoice_items (id, org_id, invoice_id, line_no, description, quantity, unit_price_paise,"
            " gst_rate_bp, taxable_paise, cgst_paise, sgst_paise, igst_paise, line_total_paise)"
            " VALUES ($1,$2,$3,$4,'d',$5,100,$6,$7,$8,$9,$10,$11)",
            uid(), org, inv, uid().int % 10**9, v["qty"], v["rate"], v["tax"], v["cgst"], v["sgst"], v["igst"], v["total"],
        )  # fmt: skip

    with pytest.raises(asyncpg.CheckViolationError, match="quantity_positive"):
        await item(qty=0)
    with pytest.raises(asyncpg.CheckViolationError, match="gst_rate"):
        await item(rate=1200)
    with pytest.raises(asyncpg.CheckViolationError, match="total_matches"):
        await item(total=1)
    with pytest.raises(asyncpg.CheckViolationError, match="igst_exclusive"):
        await item(cgst=5, igst=5, total=110)
    await item()  # the valid one still inserts


async def test_invoice_number_length_check(owner: asyncpg.Connection) -> None:
    org = await seed_org(owner)
    cust = await seed_customer(owner, org)
    await seed_invoice(owner, org, cust, number="INV/26-27/000001", status="issued")  # exactly 16
    with pytest.raises(asyncpg.CheckViolationError, match="number_len"):
        await seed_invoice(owner, org, cust, number="INV/26-27/0000012", status="issued")  # 17


async def test_issued_invoice_requires_number_dates_and_supply_type(
    owner: asyncpg.Connection,
) -> None:
    org = await seed_org(owner)
    cust = await seed_customer(owner, org)
    with pytest.raises(asyncpg.CheckViolationError, match="number_when_issued"):
        await seed_invoice(owner, org, cust, status="issued", number=None)
    with pytest.raises(asyncpg.CheckViolationError, match="issued_fields"):
        await seed_invoice(
            owner, org, cust, status="issued", number="INV/26-27/000009", supply_type=None
        )


async def test_void_requires_reason_and_currency_is_inr(owner: asyncpg.Connection) -> None:
    org = await seed_org(owner)
    cust = await seed_customer(owner, org)
    with pytest.raises(asyncpg.CheckViolationError, match="void_needs_reason"):
        await seed_invoice(
            owner, org, cust, status="void", number="INV/26-27/000003", void_reason=None
        )
    with pytest.raises(asyncpg.CheckViolationError, match="currency_inr"):
        await seed_invoice(owner, org, cust, currency="USD")


async def test_deal_and_payment_checks(owner: asyncpg.Connection) -> None:
    org = await seed_org(owner)
    cust, user = await seed_customer(owner, org), await seed_user(owner, org)
    with pytest.raises(asyncpg.CheckViolationError, match="lost_needs_reason"):
        await owner.execute(
            "INSERT INTO deals (id, org_id, customer_id, title, owner_user_id, stage, closed_at)"
            " VALUES ($1,$2,$3,'x',$4,'lost',now())",
            uid(), org, cust, user,
        )  # fmt: skip
    with pytest.raises(asyncpg.CheckViolationError, match="closed_matches_stage"):
        await owner.execute(
            "INSERT INTO deals (id, org_id, customer_id, title, owner_user_id, stage) VALUES ($1,$2,$3,'x',$4,'won')",
            uid(), org, cust, user,
        )  # fmt: skip
    inv = await seed_invoice(owner, org, cust)
    with pytest.raises(asyncpg.CheckViolationError, match="captured_fields"):
        await owner.execute(
            "INSERT INTO payments (id, org_id, invoice_id, provider, provider_payment_id, amount_paise, status)"
            " VALUES ($1,$2,$3,'manual',$4,100,'captured')",
            uid(), org, inv, f"m_{uid()}",
        )  # fmt: skip
    with pytest.raises(asyncpg.CheckViolationError, match="amount_positive"):
        await owner.execute(
            "INSERT INTO payments (id, org_id, invoice_id, provider, provider_payment_id, amount_paise, status,"
            " captured_at, paid_on) VALUES ($1,$2,$3,'manual',$4,0,'captured',$5,$6)",
            uid(), org, inv, f"m_{uid()}", datetime.now(UTC), date(2026, 11, 1),
        )  # fmt: skip


# ----------------------------------------------------------------------------- uniqueness


async def test_invoice_number_unique_per_org_not_global(owner: asyncpg.Connection) -> None:
    org_a, org_b = await seed_org(owner), await seed_org(owner)
    cust_a, cust_b = await seed_customer(owner, org_a), await seed_customer(owner, org_b)
    await seed_invoice(owner, org_a, cust_a, number="INV/26-27/000001", status="issued")
    await seed_invoice(
        owner, org_b, cust_b, number="INV/26-27/000001", status="issued"
    )  # other tenant: fine
    with pytest.raises(asyncpg.UniqueViolationError):
        await seed_invoice(owner, org_a, cust_a, number="INV/26-27/000001", status="issued")


async def test_many_drafts_without_number_are_allowed(owner: asyncpg.Connection) -> None:
    org = await seed_org(owner)
    cust = await seed_customer(owner, org)
    for _ in range(3):
        await seed_invoice(owner, org, cust)


async def test_partial_unique_open_order_index(owner: asyncpg.Connection) -> None:
    org = await seed_org(owner)
    inv = await seed_invoice(owner, org, await seed_customer(owner, org))

    async def order(status: str) -> None:
        await owner.execute(
            "INSERT INTO payment_orders (id, org_id, invoice_id, provider_order_id, receipt, amount_paise, status)"
            " VALUES ($1,$2,$3,$4,$5,100,$6)",
            uid(), org, inv, f"order_{uid()}", f"r{uid().hex}"[:40], status,
        )  # fmt: skip

    await order("created")
    with pytest.raises(asyncpg.UniqueViolationError, match="open_per_invoice"):
        await order("created")
    await order("superseded")  # closed orders are unlimited
    await order("paid")


async def test_payment_and_webhook_idempotency_keys_are_unique(owner: asyncpg.Connection) -> None:
    org = await seed_org(owner)
    inv = await seed_invoice(owner, org, await seed_customer(owner, org))
    pid = f"pay_{uid().hex}"
    insert = (
        "INSERT INTO payments (id, org_id, invoice_id, provider, provider_payment_id, amount_paise, status)"
        " VALUES ($1,$2,$3,'razorpay',$4,100,'authorized')"
    )
    await owner.execute(insert, uid(), org, inv, pid)
    with pytest.raises(asyncpg.UniqueViolationError):
        await owner.execute(insert, uid(), org, inv, pid)
    evt = f"evt_{uid().hex}"
    ins_evt = (
        "INSERT INTO webhook_events (id, provider, provider_event_id, event_type, payload, raw_body)"
        " VALUES ($1,'razorpay',$2,'payment.captured','{}'::jsonb,'{}')"
    )
    await owner.execute(ins_evt, uid(), evt)
    with pytest.raises(asyncpg.UniqueViolationError):
        await owner.execute(ins_evt, uid(), evt)


async def test_email_unique_case_insensitively_across_tenants(owner: asyncpg.Connection) -> None:
    org_a, org_b = await seed_org(owner), await seed_org(owner)
    email = f"Mixed.{uid().hex}@example.com"
    ins = "INSERT INTO users (id, org_id, email, full_name, password_hash, role) VALUES ($1,$2,$3,'n','x','admin')"
    await owner.execute(ins, uid(), org_a, email)
    with pytest.raises(asyncpg.UniqueViolationError):
        await owner.execute(ins, uid(), org_b, email.lower())


async def test_gstin_unique_per_org_only(owner: asyncpg.Connection) -> None:
    org_a, org_b = await seed_org(owner), await seed_org(owner)
    g = "33TESTA1234B1Z5"
    await seed_customer(owner, org_a, g)
    await seed_customer(owner, org_b, g)  # another tenant may have the same buyer
    with pytest.raises(asyncpg.UniqueViolationError):
        await seed_customer(owner, org_a, g)
    await seed_customer(owner, org_a, None)
    await seed_customer(owner, org_a, None)  # NULL gstin is unlimited


async def test_invoice_items_cascade_with_invoice(owner: asyncpg.Connection) -> None:
    org = await seed_org(owner)
    inv = await seed_invoice(owner, org, await seed_customer(owner, org))
    await owner.execute(
        "INSERT INTO invoice_items (id, org_id, invoice_id, line_no, description, quantity, unit_price_paise,"
        " gst_rate_bp, taxable_paise, cgst_paise, sgst_paise, igst_paise, line_total_paise)"
        " VALUES ($1,$2,$3,1,'d',1,100,0,100,0,0,0,100)",
        uid(), org, inv,
    )  # fmt: skip
    await owner.execute("DELETE FROM invoices WHERE id = $1", inv)
    assert (
        await owner.fetchval("SELECT count(*) FROM invoice_items WHERE invoice_id = $1", inv) == 0
    )


async def test_invoice_sequence_upsert_increments_atomically(owner: asyncpg.Connection) -> None:
    org = await seed_org(owner)
    sql = (
        "INSERT INTO invoice_sequences (org_id, fy_label, last_value) VALUES ($1,'26-27',1)"
        " ON CONFLICT (org_id, fy_label) DO UPDATE SET last_value = invoice_sequences.last_value + 1"
        " RETURNING last_value"
    )
    assert [await owner.fetchval(sql, org) for _ in range(3)] == [1, 2, 3]


# ----------------------------------------------------------------------------- grants (07 section 1.3)


async def test_app_role_cannot_run_ddl(app_conn: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_conn.execute("CREATE TABLE public.sneaky (id int)")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_conn.execute("DROP TABLE public.invoices")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_conn.execute("ALTER TABLE public.invoices ADD COLUMN x int")


async def test_audit_logs_are_append_only_for_app_role(
    owner: asyncpg.Connection, app_conn: asyncpg.Connection
) -> None:
    org = await seed_org(owner)
    row = uid()
    await app_conn.execute(
        "INSERT INTO audit_logs (id, org_id, actor_type, action, entity_type) VALUES ($1,$2,'system','x','y')",
        row,
        org,
    )
    assert await app_conn.fetchval("SELECT count(*) FROM audit_logs WHERE id = $1", row) == 1
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_conn.execute("UPDATE audit_logs SET action = 'tampered' WHERE id = $1", row)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_conn.execute("DELETE FROM audit_logs WHERE id = $1", row)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_conn.execute("TRUNCATE audit_logs")


async def test_app_role_has_dml_on_business_tables_and_read_only_alembic_version(
    app_conn: asyncpg.Connection,
) -> None:
    org = await seed_org(app_conn)  # INSERT works
    await app_conn.execute("UPDATE organizations SET name = 'renamed' WHERE id = $1", org)
    assert await app_conn.fetchval("SELECT version_num FROM alembic_version")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_conn.execute("UPDATE alembic_version SET version_num = 'x'")


async def test_ai_reader_cannot_touch_public_tables(db_urls: DbUrls) -> None:
    conn = await asyncpg.connect(db_urls.ai)
    try:
        for table in ("invoices", "customers", "users", "audit_logs", "webhook_events", "payments"):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.fetch(f"SELECT * FROM public.{table}")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("CREATE TABLE ai.x (id int)")
    finally:
        await conn.close()


# ----------------------------------------------------------------------------- migrations round-trip


async def test_upgrade_downgrade_upgrade_on_clean_database() -> None:
    urls = await create_database("revops_roundtrip")
    await migrate(urls, "upgrade", "head")
    conn = await asyncpg.connect(urls.owner)
    try:
        tables = {
            r[0]
            for r in await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        }
        assert len(tables - {"alembic_version"}) == 11
        await migrate(urls, "downgrade", "base")
        left = {
            r[0]
            for r in await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        }
        assert left <= {"alembic_version"}
        await migrate(urls, "upgrade", "head")
        again = {
            r[0]
            for r in await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        }
        assert again == tables
        # the audit-log revoke is re-applied by the second upgrade
        app = await asyncpg.connect(urls.app)
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await app.execute("DELETE FROM audit_logs")
        finally:
            await app.close()
    finally:
        await conn.close()


async def test_downgrade_minus_one_works() -> None:
    urls = await create_database("revops_roundtrip2")
    await migrate(urls, "upgrade", "head")
    await migrate(urls, "downgrade", "-1")
    await migrate(urls, "upgrade", "head")
