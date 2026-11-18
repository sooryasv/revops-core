"""Gap-free numbering under real concurrency (03 section 4.3): parallel requests, one database, real row locks."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.clock import FixedClock
from tests.conftest import ClientFactory, World, login_as

I, C = "/api/v1/invoices", "/api/v1/customers"  # noqa: E741
ITEM = {"description": "Widget", "quantity": 1, "unit_price_paise": 100_000, "gst_rate_bp": 1800}


async def _drafts(c: httpx.AsyncClient, n: int) -> list[str]:
    cust = (await c.post(C, json={"name": "Concurrent Buyer", "state_code": "33"})).json()["id"]
    ids = []
    for _ in range(n):
        r = await c.post(I, json={"customer_id": cust, "items": [ITEM]})
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    return ids


async def test_twenty_parallel_issues_produce_exactly_one_to_twenty(
    world: World, make_client: ClientFactory, clock: FixedClock
) -> None:
    clock.set(datetime(2026, 11, 2, 4, 0, tzinfo=UTC))
    fin = await login_as(make_client, world.a["finance"])
    ids = await _drafts(fin, 20)
    results = await asyncio.gather(*[fin.post(f"{I}/{i}/issue") for i in ids])
    assert [r.status_code for r in results] == [200] * 20
    numbers = sorted(r.json()["number"] for r in results)
    assert numbers == [f"INV/26-27/{n:06d}" for n in range(1, 21)]  # no gaps, no duplicates


async def test_parallel_issue_of_the_same_invoice_allocates_one_number(
    world: World, make_client: ClientFactory, clock: FixedClock
) -> None:
    clock.set(datetime(2026, 11, 2, 4, 0, tzinfo=UTC))
    fin = await login_as(make_client, world.a["finance"])
    (only,) = await _drafts(fin, 1)
    results = await asyncio.gather(*[fin.post(f"{I}/{only}/issue") for _ in range(10)])
    assert [r.status_code for r in results] == [200] * 10
    assert {r.json()["number"] for r in results} == {"INV/26-27/000001"}
    other = (await _drafts(fin, 1))[0]
    assert (await fin.post(f"{I}/{other}/issue")).json()[
        "number"
    ] == "INV/26-27/000002"  # nothing was burned


async def test_two_orgs_issuing_at_once_keep_independent_counters(
    world: World, make_client: ClientFactory, clock: FixedClock
) -> None:
    clock.set(datetime(2026, 11, 2, 4, 0, tzinfo=UTC))
    a = await login_as(make_client, world.a["finance"], forwarded_for="10.8.0.1")
    b = await login_as(make_client, world.b["finance"], forwarded_for="10.8.0.2")
    ids_a, ids_b = await _drafts(a, 8), await _drafts(b, 8)
    ra, rb = await asyncio.gather(
        asyncio.gather(*[a.post(f"{I}/{i}/issue") for i in ids_a]),
        asyncio.gather(*[b.post(f"{I}/{i}/issue") for i in ids_b]),
    )
    expected = [f"INV/26-27/{n:06d}" for n in range(1, 9)]
    assert sorted(r.json()["number"] for r in ra) == expected
    assert sorted(r.json()["number"] for r in rb) == expected


async def test_mixed_failures_and_successes_leave_no_gaps(
    world: World, make_client: ClientFactory, clock: FixedClock
) -> None:
    """Some issues fail validation (zero total) while others succeed in parallel: the successful numbers stay 1..n."""
    clock.set(datetime(2026, 11, 2, 4, 0, tzinfo=UTC))
    fin = await login_as(make_client, world.a["finance"])
    good = await _drafts(fin, 6)
    cust = (await fin.post(C, json={"name": "Free Buyer", "state_code": "33"})).json()["id"]
    zero = [
        (
            await fin.post(
                I, json={"customer_id": cust, "items": [{**ITEM, "unit_price_paise": 0}]}
            )
        ).json()["id"]
        for _ in range(4)
    ]
    order: list[Any] = [x for pair in zip(good, zero, strict=False) for x in pair] + good[4:]
    results = await asyncio.gather(*[fin.post(f"{I}/{i}/issue") for i in order])
    ok = sorted(r.json()["number"] for r in results if r.status_code == 200)
    assert ok == [f"INV/26-27/{n:06d}" for n in range(1, 7)]
    assert sum(r.status_code == 422 for r in results) == 4
