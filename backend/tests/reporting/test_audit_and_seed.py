"""GET /audit-logs and the seed's determinism and safety properties."""

from __future__ import annotations

import ast
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.seed.demo import CANARY_NAME, POISON_NOTE, generate
from tests.conftest import PASSWORD
from tests.reporting.conftest import Seeded

A = "/api/v1/audit-logs"


async def test_audit_log_is_admin_only(seeded: Seeded, seeded_login: Any) -> None:
    assert (await (await seeded_login("a", "admin")).get(A)).status_code == 200
    for role in ("finance", "sales"):
        assert (await (await seeded_login("a", role)).get(A)).status_code == 403


async def test_audit_log_is_tenant_scoped_and_newest_first(
    seeded: Seeded, seeded_login: Any
) -> None:
    admin = await seeded_login("a", "admin")
    body = (await admin.get(A, params={"limit": 100})).json()
    assert body["total"] > 100
    stamps = [r["created_at"] for r in body["items"]]
    assert stamps == sorted(stamps, reverse=True)
    conn = await seeded.connect()
    try:
        total_a = await conn.fetchval(
            "SELECT count(*) FROM audit_logs WHERE org_id=$1", uuid.UUID(seeded.org_ids["a"])
        )
        foreign = await conn.fetchval(
            "SELECT id FROM audit_logs WHERE org_id=$1 LIMIT 1", uuid.UUID(seeded.org_ids["b"])
        )
    finally:
        await conn.close()
    assert body["total"] == total_a
    everything = []
    for offset in range(0, total_a, 100):
        everything += (await admin.get(A, params={"limit": 100, "offset": offset})).json()["items"]
    assert len(everything) == total_a
    assert str(foreign) not in {r["id"] for r in everything}


async def test_audit_filters(seeded: Seeded, seeded_login: Any) -> None:
    admin = await seeded_login("a", "admin")
    by_action = (await admin.get(A, params={"action": "payment.overpaid"})).json()
    assert by_action["total"] == 1
    assert {r["action"] for r in by_action["items"]} == {"payment.overpaid"}
    by_entity = (await admin.get(A, params={"entity_type": "payment", "limit": 100})).json()
    assert by_entity["total"] > 0
    assert {r["entity_type"] for r in by_entity["items"]} == {"payment"}
    entity_id = by_action["items"][0]["entity_id"]
    one = (await admin.get(A, params={"entity_id": entity_id, "limit": 100})).json()
    assert one["total"] >= 1
    assert {r["entity_id"] for r in one["items"]} == {entity_id}
    nobody = (await admin.get(A, params={"actor_user_id": str(uuid.uuid4())})).json()
    assert nobody["total"] == 0
    today = seeded.today
    future = (await admin.get(A, params={"from": (today + timedelta(days=1)).isoformat()})).json()
    assert future["total"] == 0 or all(
        r["created_at"][:10] >= today.isoformat() for r in future["items"]
    )
    old = (await admin.get(A, params={"to": (today - timedelta(days=400)).isoformat()})).json()
    assert old["total"] == 0


async def test_audit_validation_and_pagination_bounds(seeded: Seeded, seeded_login: Any) -> None:
    admin = await seeded_login("a", "admin")
    for params in (
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"entity_id": "nope"},
        {"from": "x"},
    ):
        assert (await admin.get(A, params=params)).status_code == 422, params


async def test_the_api_audits_what_it_does_and_the_row_is_visible(
    seeded: Seeded, seeded_login: Any
) -> None:
    admin = await seeded_login("b", "admin")
    before = (await admin.get(A, params={"action": "reporting.refreshed"})).json()["total"]
    assert (await admin.post("/api/v1/admin/reporting/refresh")).status_code == 200
    after = (await admin.get(A, params={"action": "reporting.refreshed"})).json()
    assert after["total"] == before + 1
    assert after["items"][0]["actor_type"] == "user"


# ----------------------------------------------------------------------------- seed properties


def test_seed_is_deterministic_and_seed_sensitive() -> None:
    today = date(2026, 11, 3)
    assert generate(42, today).tables == generate(42, today).tables
    assert generate(42, today).tables != generate(7, today).tables
    assert generate(42, today).tables != generate(42, today + timedelta(days=1)).tables


def test_seed_matches_the_contract_shape() -> None:
    data = generate(42, date(2026, 11, 3))
    orgs = data.tables["organizations"]
    assert [(o["name"], o["state_code"], o["udyam_category"]) for o in orgs] == [
        ("Kovai Precision Components (demo)", "33", "small"),
        ("Tiruppur Knit Exports (demo)", "33", "micro"),
    ]
    for org in orgs:
        users = [u for u in data.tables["users"] if u["org_id"] == org["id"]]
        assert sorted(u["role"] for u in users) == ["admin", "finance", "sales"]
        customers = [c for c in data.tables["customers"] if c["org_id"] == org["id"]]
        assert len(customers) == 25
        in_tn = sum(1 for c in customers if c["state_code"] == "33")
        assert in_tn == 15  # 60%
        assert {c["state_code"] for c in customers} >= {"33", "29", "27", "24", "07", "32"}
        assert any(c["gstin"] is None for c in customers)
        assert any(c["gstin"] for c in customers)
        assert {c["has_written_agreement"] for c in customers} == {True, False}
        assert {c["default_terms_days"] for c in customers} == {15, 30, 45}
        invoices = [i for i in data.tables["invoices"] if i["org_id"] == org["id"]]
        assert len(invoices) == 140
        assert {i["status"] for i in invoices} == {"draft", "issued", "void"}
    all_emails = [u["email"] for u in data.tables["users"]] + [
        c["email"] for c in data.tables["customers"]
    ]
    assert all(e.endswith("@example.com") for e in all_emails)
    assert {u["email"] for u in data.tables["users"]} == {
        f"{role}.{k}@example.com" for role in ("admin", "finance", "sales") for k in ("a", "b")
    }


def test_seed_plants_the_poison_note_and_canary_in_the_right_tenants() -> None:
    data = generate(42, date(2026, 11, 3))
    org_a, org_b = (o["id"] for o in data.tables["organizations"])
    poisoned = [c for c in data.tables["customers"] if c["notes"] == POISON_NOTE]
    assert len(poisoned) == 1
    assert poisoned[0]["org_id"] == org_a
    canary = [c for c in data.tables["customers"] if c["name"] == CANARY_NAME]
    assert [c["org_id"] for c in canary] == [org_b]


def test_seed_numbers_invoices_consecutively_per_financial_year() -> None:
    data = generate(42, date(2026, 11, 3))
    for org in data.tables["organizations"]:
        by_fy: dict[str, list[int]] = {}
        for inv in data.tables["invoices"]:
            if inv["org_id"] == org["id"] and inv["number"]:
                _, fy, counter = inv["number"].split("/")
                by_fy.setdefault(fy, []).append(int(counter))
        assert len(by_fy) == 2
        for counters in by_fy.values():
            assert sorted(counters) == list(range(1, len(counters) + 1))
        sequences = {
            s["fy_label"]: s["last_value"]
            for s in data.tables["invoice_sequences"]
            if s["org_id"] == org["id"]
        }
        assert sequences == {fy: len(c) for fy, c in by_fy.items()}


def test_seed_money_is_integer_paise_and_invoice_cache_equals_captured_sum() -> None:
    data = generate(42, date(2026, 11, 3))
    captured: dict[Any, int] = {}
    for p in data.tables["payments"]:
        assert isinstance(p["amount_paise"], int)
        if p["status"] == "captured":
            captured[p["invoice_id"]] = captured.get(p["invoice_id"], 0) + p["amount_paise"]
    for inv in data.tables["invoices"]:
        assert isinstance(inv["total_paise"], int)
        assert inv["amount_paid_paise"] == captured.get(inv["id"], 0)
        assert (
            inv["total_paise"]
            == inv["subtotal_paise"] + inv["cgst_paise"] + inv["sgst_paise"] + inv["igst_paise"]
        )


def test_seed_has_the_required_behaviours() -> None:
    data = generate(42, date(2026, 11, 3))
    for key, org in zip(("a", "b"), data.tables["organizations"], strict=True):
        assert data.tally[key]["overpaid_invoices"] == 1
        events = [
            e
            for e in data.tables["webhook_events"]
            if e["provider_event_id"].endswith(f"_{key}")
            and e["processing_status"] == "quarantined"
        ]
        assert sorted(e["last_error"] for e in events) == [
            "amount_or_currency_mismatch",
            "unknown_order",
        ]
        assert sorted(str(e["org_id"]) if e["org_id"] else "none" for e in events) == sorted(
            [str(org["id"]), "none"]
        )
        retried = [
            e
            for e in data.tables["webhook_events"]
            if e["org_id"] == org["id"] and e["attempts"] == 2
        ]
        assert len(retried) == 1  # a redelivered event, still exactly one payment


def test_seed_uses_no_clock_and_no_unseeded_randomness() -> None:
    """Determinism guard: the generator may not read the wall clock or use the global random state."""
    source = Path("app/seed/demo.py").read_text()
    tree = ast.parse(source)
    banned_calls = {"now", "today", "utcnow", "time", "uuid4", "uuid7"}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in banned_calls
        ):
            raise AssertionError(f"non-deterministic call at line {node.lineno}")
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "random"
            and node.attr != "Random"
        ):
            raise AssertionError(f"global random state used at line {node.lineno}")


async def test_seeded_users_can_log_in_with_the_seed_password_and_roles_differ(
    seeded: Seeded, seeded_login: Any
) -> None:
    for role in ("admin", "finance", "sales"):
        client = await seeded_login("a", role)
        me = (await client.get("/api/v1/auth/me")).json()
        assert me["role"] == role
        assert me["org_id"] == seeded.org_ids["a"]
    assert PASSWORD


async def test_login_disabled_users_cannot_authenticate(seeded: Seeded, seeded_app: Any) -> None:
    import httpx

    conn = await seeded.connect()
    try:
        await conn.execute(
            "UPDATE users SET password_hash = '!login-disabled-demo-seed' WHERE email = 'sales.b@example.com'"
        )
        transport = httpx.ASGITransport(app=seeded_app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"x-forwarded-for": "10.88.0.1"},
        ) as c:
            r = await c.post(
                "/api/v1/auth/login", json={"email": "sales.b@example.com", "password": PASSWORD}
            )
            assert r.status_code == 401
    finally:
        from app.core.passwords import make_hasher

        await conn.execute(
            "UPDATE users SET password_hash = $1 WHERE email = 'sales.b@example.com'",
            make_hasher(fast_for_tests=True).hash(PASSWORD),
        )
        await conn.close()
