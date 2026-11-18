"""Reporting queries. Parameterized `text()` with an explicit `org_id` on every statement (03 section 7.1 SQL rule).

`today` is always passed in from the business clock (IST calendar date) so tests can move it; the `ai` views use the
database clock instead and a cross-check test proves the two agree.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.audit import AuditLog
from app.repositories.base import cast_rows

AGING_BUCKETS = ("not_due", "1-30", "31-60", "61-90", "90+")

_SUMMARY = text("""
SELECT coalesce(sum(greatest(total_paise - amount_paid_paise, 0)), 0)::bigint AS outstanding_paise,
       coalesce(sum(greatest(total_paise - amount_paid_paise, 0)) FILTER (WHERE due_date < CAST(:today AS date)), 0)::bigint
           AS overdue_paise,
       coalesce(sum(total_paise) FILTER (WHERE issue_date > CAST(:today AS date) - 30), 0)::bigint AS billed_last_30d_paise,
       count(*) FILTER (WHERE total_paise > amount_paid_paise AND due_date < CAST(:today AS date))::int AS overdue_count,
       count(*) FILTER (WHERE total_paise > amount_paid_paise)::int AS open_count
  FROM invoices
 WHERE org_id = :org_id AND status = 'issued'
""")

_COLLECTED_30D = text("""
SELECT coalesce(sum(amount_paise), 0)::bigint AS collected
  FROM payments
 WHERE org_id = :org_id AND status = 'captured' AND paid_on > CAST(:today AS date) - 30
""")

_INVOICE_STATUS_COUNTS = text("""
SELECT status, count(*)::int AS n FROM invoices WHERE org_id = :org_id GROUP BY status
""")

_AGING = text("""
SELECT bucket, count(*)::int AS invoices, coalesce(sum(due), 0)::bigint AS due_paise
  FROM (
    SELECT greatest(total_paise - amount_paid_paise, 0) AS due,
           CASE WHEN due_date >= CAST(:today AS date) THEN 'not_due'
                WHEN CAST(:today AS date) - due_date <= 30 THEN '1-30'
                WHEN CAST(:today AS date) - due_date <= 60 THEN '31-60'
                WHEN CAST(:today AS date) - due_date <= 90 THEN '61-90'
                ELSE '90+' END AS bucket
      FROM invoices
     WHERE org_id = :org_id AND status = 'issued' AND total_paise > amount_paid_paise
  ) x
 GROUP BY bucket
""")

_COLLECTIONS_DAY = text("""
SELECT day AS period, billed_paise, collected_paise, invoices_issued, payments_count
  FROM mv_collections_daily
 WHERE org_id = :org_id AND day BETWEEN :date_from AND :date_to
 ORDER BY day
""")

_COLLECTIONS_MONTH = text("""
SELECT to_char(date_trunc('month', day), 'YYYY-MM') AS period,
       sum(billed_paise)::bigint AS billed_paise, sum(collected_paise)::bigint AS collected_paise,
       sum(invoices_issued)::int AS invoices_issued, sum(payments_count)::int AS payments_count
  FROM mv_collections_daily
 WHERE org_id = :org_id AND day BETWEEN :date_from AND :date_to
 GROUP BY date_trunc('month', day)
 ORDER BY date_trunc('month', day)
""")

# 02 section 3.2: outstanding(M) = issued totals with issue_date <= M minus captured payments with paid_on <= M.
_DSO_TREND = text("""
SELECT to_char(m.month_start, 'YYYY-MM') AS month,
       m.month_end,
       coalesce((SELECT sum(total_paise) FROM invoices
                  WHERE org_id = :org_id AND status = 'issued' AND issue_date BETWEEN m.month_start AND m.month_end), 0)::bigint
           AS billed_paise,
       coalesce((SELECT sum(amount_paise) FROM payments
                  WHERE org_id = :org_id AND status = 'captured' AND paid_on BETWEEN m.month_start AND m.month_end), 0)::bigint
           AS collected_paise,
       (coalesce((SELECT sum(total_paise) FROM invoices
                   WHERE org_id = :org_id AND status = 'issued' AND issue_date <= m.month_end), 0)
        - coalesce((SELECT sum(amount_paise) FROM payments
                     WHERE org_id = :org_id AND status = 'captured' AND paid_on <= m.month_end), 0))::bigint
           AS outstanding_end_paise,
       (m.month_end - m.month_start + 1)::int AS days_in_month
  FROM (
    SELECT (date_trunc('month', CAST(:today AS date)) - make_interval(months => g))::date AS month_start,
           ((date_trunc('month', CAST(:today AS date)) - make_interval(months => g)) + interval '1 month' - interval '1 day')::date
               AS month_end
      FROM generate_series(0, CAST(:months AS int) - 1) g
  ) m
 ORDER BY m.month_start
""")

_PIPELINE_BY_STAGE = text("""
SELECT stage, count(*)::int AS n, coalesce(sum(amount_estimate_paise), 0)::bigint AS value_paise
  FROM deals WHERE org_id = :org_id GROUP BY stage
""")

_PIPELINE_CLOSED_90D = text("""
SELECT stage, count(*)::int AS n, coalesce(sum(amount_estimate_paise), 0)::bigint AS value_paise
  FROM deals
 WHERE org_id = :org_id AND stage IN ('won', 'lost')
   AND (closed_at AT TIME ZONE 'Asia/Kolkata')::date > CAST(:today AS date) - 90
 GROUP BY stage
""")

_PIPELINE_STALE = text("""
SELECT d.id AS deal_id, d.title, c.name AS customer_name, d.stage,
       (CAST(:today AS date) - (d.stage_changed_at AT TIME ZONE 'Asia/Kolkata')::date)::int AS days_in_stage
  FROM deals d
  JOIN customers c ON c.org_id = d.org_id AND c.id = d.customer_id
 WHERE d.org_id = :org_id AND d.stage IN ('lead', 'qualified', 'proposal')
   AND CAST(:today AS date) - (d.stage_changed_at AT TIME ZONE 'Asia/Kolkata')::date > 30
 ORDER BY days_in_stage DESC, d.id
 LIMIT 10
""")

_TOP_OVERDUE = text("""
SELECT c.id AS customer_id, c.name AS customer_name,
       sum(greatest(i.total_paise - i.amount_paid_paise, 0))::bigint AS overdue_paise, count(*)::int AS invoices
  FROM invoices i
  JOIN customers c ON c.org_id = i.org_id AND c.id = i.customer_id
 WHERE i.org_id = :org_id AND i.status = 'issued' AND i.total_paise > i.amount_paid_paise
   AND i.due_date < CAST(:today AS date)
 GROUP BY c.id, c.name
 ORDER BY overdue_paise DESC, c.name, c.id
 LIMIT 5
""")

_RECENT_PAYMENTS = text("""
SELECT p.id AS payment_id, p.invoice_id, i.number AS invoice_number, c.name AS customer_name,
       p.amount_paise, p.paid_on, p.provider
  FROM payments p
  JOIN invoices i ON i.org_id = p.org_id AND i.id = p.invoice_id
  JOIN customers c ON c.org_id = i.org_id AND c.id = i.customer_id
 WHERE p.org_id = :org_id AND p.status = 'captured'
 ORDER BY p.captured_at DESC, p.id DESC
 LIMIT 5
""")

_WEBHOOK_PROBLEMS = text("""
SELECT count(*)::int AS n FROM webhook_events
 WHERE org_id = :org_id AND processing_status IN ('failed', 'quarantined')
   AND received_at >= now() - interval '7 days'
""")

_REFRESH = text("SELECT public.refresh_collections_mv()")


class ReportRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def _rows(self, stmt: Any, **params: Any) -> list[dict[str, Any]]:
        result = await self._session.execute(stmt, {"org_id": self._org_id, **params})
        return cast_rows(result.mappings().all())

    async def summary(self, today: date) -> dict[str, Any]:
        (row,) = await self._rows(_SUMMARY, today=today)
        (collected,) = await self._rows(_COLLECTED_30D, today=today)
        statuses = {r["status"]: r["n"] for r in await self._rows(_INVOICE_STATUS_COUNTS)}
        return {
            **row,
            "collected_last_30d_paise": collected["collected"],
            "status_counts": statuses,
        }

    async def dashboard_extras(self, today: date) -> dict[str, Any]:
        (problems,) = await self._rows(_WEBHOOK_PROBLEMS)
        return {
            "top_overdue_customers": await self._rows(_TOP_OVERDUE, today=today),
            "recent_payments": await self._rows(_RECENT_PAYMENTS),
            "webhook_problems_7d": problems["n"],
        }

    async def aging(self, today: date) -> list[dict[str, Any]]:
        return await self._rows(_AGING, today=today)

    async def collections(
        self, date_from: date, date_to: date, *, monthly: bool
    ) -> list[dict[str, Any]]:
        stmt = _COLLECTIONS_MONTH if monthly else _COLLECTIONS_DAY
        return await self._rows(stmt, date_from=date_from, date_to=date_to)

    async def dso_trend(self, today: date, months: int) -> list[dict[str, Any]]:
        return await self._rows(_DSO_TREND, today=today, months=months)

    async def pipeline(self, today: date) -> dict[str, Any]:
        return {
            "by_stage": await self._rows(_PIPELINE_BY_STAGE),
            "closed_90d": await self._rows(_PIPELINE_CLOSED_90D, today=today),
            "stale": await self._rows(_PIPELINE_STALE, today=today),
        }

    async def refresh_collections(self) -> None:
        """Runs the SECURITY DEFINER function (07 section 1.2): the app role is not the materialized view's owner."""
        await self._session.execute(_REFRESH)


class AuditReadRepository:
    """Reads the append-only audit trail for one tenant."""

    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def page(
        self,
        *,
        entity_type: str | None,
        entity_id: uuid.UUID | None,
        actor_user_id: uuid.UUID | None,
        action: str | None,
        date_from: date | None,
        date_to: date | None,
        limit: int,
        offset: int,
    ) -> tuple[list[AuditLog], int]:
        ist_day = func.timezone("Asia/Kolkata", AuditLog.created_at)
        conditions = [AuditLog.org_id == self._org_id]
        if entity_type:
            conditions.append(AuditLog.entity_type == entity_type)
        if entity_id:
            conditions.append(AuditLog.entity_id == entity_id)
        if actor_user_id:
            conditions.append(AuditLog.actor_user_id == actor_user_id)
        if action:
            conditions.append(AuditLog.action == action)
        if date_from:
            conditions.append(func.date(ist_day) >= date_from)
        if date_to:
            conditions.append(func.date(ist_day) <= date_to)
        total = int(
            (
                await self._session.execute(
                    select(func.count()).select_from(AuditLog).where(*conditions)
                )
            ).scalar_one()
        )
        rows = (
            (
                await self._session.execute(
                    select(AuditLog)
                    .where(*conditions)
                    .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        return list(rows), total
