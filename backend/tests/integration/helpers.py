"""Tiny SQL seeding helpers for schema-level tests. They bypass the app on purpose: these tests probe the database."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import asyncpg


def uid() -> uuid.UUID:
    return uuid.uuid4()


async def seed_org(conn: asyncpg.Connection, name: str = "Org", state: str = "33") -> uuid.UUID:
    org = uid()
    await conn.execute(
        "INSERT INTO organizations (id, name, legal_name, state_code, udyam_category) VALUES ($1,$2,$2,$3,'small')",
        org, name, state,
    )  # fmt: skip
    return org


async def seed_user(conn: asyncpg.Connection, org: uuid.UUID, role: str = "admin") -> uuid.UUID:
    user = uid()
    await conn.execute(
        "INSERT INTO users (id, org_id, email, full_name, password_hash, role) VALUES ($1,$2,$3,'T','x',$4)",
        user, org, f"{user}@example.com", role,
    )  # fmt: skip
    return user


async def seed_customer(
    conn: asyncpg.Connection, org: uuid.UUID, gstin: str | None = None
) -> uuid.UUID:
    cust = uid()
    await conn.execute(
        "INSERT INTO customers (id, org_id, name, state_code, gstin) VALUES ($1,$2,'Buyer','33',$3)",
        cust, org, gstin,
    )  # fmt: skip
    return cust


async def seed_invoice(
    conn: asyncpg.Connection,
    org: uuid.UUID,
    customer: uuid.UUID,
    *,
    number: str | None = None,
    status: str = "draft",
    **overrides: Any,
) -> uuid.UUID:
    inv = uid()
    cols: dict[str, Any] = {
        "id": inv, "org_id": org, "customer_id": customer, "number": number, "status": status,
        "payment_terms_days": 30, "place_of_supply_state_code": "33",
        "subtotal_paise": 100_000, "cgst_paise": 9_000, "sgst_paise": 9_000, "igst_paise": 0, "total_paise": 118_000,
    }  # fmt: skip
    if status != "draft":
        cols |= {
            "issue_date": date(2026, 11, 1),
            "due_date": date(2026, 12, 1),
            "supply_type": "intra",
        }
    if status == "void":
        cols["void_reason"] = "test"
    cols |= overrides
    names = ", ".join(cols)
    marks = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
    await conn.execute(f"INSERT INTO invoices ({names}) VALUES ({marks})", *cols.values())
    return inv
