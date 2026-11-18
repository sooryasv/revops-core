"""Reporting use cases. All numbers come from SQL (repositories/reports.py); this layer only shapes and validates."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from app.core.clock import Clock
from app.core.context import AuthContext, RequestMeta
from app.db.models.audit import AuditLog
from app.db.uow import UnitOfWorkFactory
from app.domain.errors import FieldError, ValidationFailed
from app.repositories.audit import record_audit
from app.repositories.reports import AGING_BUCKETS, AuditReadRepository, ReportRepository
from app.schemas.reports import (
    AgingOut,
    AgingRow,
    CollectionsOut,
    CollectionsRow,
    DsoMonth,
    DsoOut,
    InvoiceStatusCounts,
    PipelineOut,
    RecentPayment,
    RefreshOut,
    StageTotals,
    StaleDeal,
    SummaryOut,
    TopOverdueCustomer,
    WonTotals,
)

_OPEN_STAGES = ("lead", "qualified", "proposal")
_MAX_RANGE_DAYS = 800


def dso(outstanding_paise: int, billed_paise: int, days: int) -> float | None:
    """Ratio method (02 section 3.1/3.2): outstanding / billed * days, two decimals, half-up; null when nothing billed.
    Integer arithmetic via Decimal so the rounding matches PostgreSQL's numeric `round`."""
    if billed_paise <= 0:
        return None
    value = (Decimal(outstanding_paise) * days / Decimal(billed_paise)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return float(value)


class ReportService:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def summary(self, ctx: AuthContext) -> SummaryOut:
        today = self._clock.today_ist()
        async with self._uow_factory() as uow:
            repo = ReportRepository(uow.session, ctx.org_id)
            data = await repo.summary(today)
            extras = await repo.dashboard_extras(today)
        counts = data["status_counts"]
        return SummaryOut(
            as_of_date=today,
            outstanding_paise=data["outstanding_paise"],
            overdue_paise=data["overdue_paise"],
            overdue_invoices=data["overdue_count"],
            open_invoices=data["open_count"],
            billed_last_30d_paise=data["billed_last_30d_paise"],
            collected_last_30d_paise=data["collected_last_30d_paise"],
            dso_30d=dso(data["outstanding_paise"], data["billed_last_30d_paise"], 30),
            invoices=InvoiceStatusCounts(
                **{k: counts.get(k, 0) for k in ("draft", "issued", "void")}
            ),
            top_overdue_customers=[
                TopOverdueCustomer(**r) for r in extras["top_overdue_customers"]
            ],
            recent_payments=[RecentPayment(**r) for r in extras["recent_payments"]],
            webhook_problems_7d=extras["webhook_problems_7d"],
        )

    async def aging(self, ctx: AuthContext) -> AgingOut:
        today = self._clock.today_ist()
        async with self._uow_factory() as uow:
            rows = await ReportRepository(uow.session, ctx.org_id).aging(today)
        by_bucket = {r["bucket"]: r for r in rows}
        buckets = [
            AgingRow(
                bucket=b,  # type: ignore[arg-type]
                invoices=by_bucket.get(b, {}).get("invoices", 0),
                due_paise=by_bucket.get(b, {}).get("due_paise", 0),
            )
            for b in AGING_BUCKETS
        ]
        return AgingOut(
            as_of_date=today, buckets=buckets, total_due_paise=sum(b.due_paise for b in buckets)
        )

    async def collections(
        self,
        ctx: AuthContext,
        *,
        date_from: date | None,
        date_to: date | None,
        group: Literal["day", "month"],
    ) -> CollectionsOut:
        today = self._clock.today_ist()
        end = date_to or today
        start = date_from or (end - timedelta(days=89))
        if start > end:
            raise ValidationFailed(
                "from must not be after to", errors=[FieldError("from", "after to")]
            )
        if (end - start).days > _MAX_RANGE_DAYS:
            raise ValidationFailed(
                f"the range may not exceed {_MAX_RANGE_DAYS} days",
                errors=[FieldError("from", "range too long")],
            )
        async with self._uow_factory() as uow:
            rows = await ReportRepository(uow.session, ctx.org_id).collections(
                start, end, monthly=group == "month"
            )
        return CollectionsOut(
            date_from=start,
            date_to=end,
            group=group,
            rows=[
                CollectionsRow(
                    **{
                        **r,
                        "period": r["period"].isoformat()
                        if hasattr(r["period"], "isoformat")
                        else r["period"],
                    }
                )
                for r in rows
            ],
        )

    async def dso(self, ctx: AuthContext, *, months: int) -> DsoOut:
        today = self._clock.today_ist()
        async with self._uow_factory() as uow:
            repo = ReportRepository(uow.session, ctx.org_id)
            rows = await repo.dso_trend(today, months)
            summary = await repo.summary(today)
        return DsoOut(
            months=[
                DsoMonth(
                    month=r["month"],
                    billed_paise=r["billed_paise"],
                    collected_paise=r["collected_paise"],
                    outstanding_end_paise=r["outstanding_end_paise"],
                    dso=dso(r["outstanding_end_paise"], r["billed_paise"], r["days_in_month"]),
                )
                for r in rows
            ],
            current_dso_30d=dso(summary["outstanding_paise"], summary["billed_last_30d_paise"], 30),
        )

    async def pipeline(self, ctx: AuthContext) -> PipelineOut:
        today = self._clock.today_ist()
        async with self._uow_factory() as uow:
            data = await ReportRepository(uow.session, ctx.org_id).pipeline(today)
        by_stage = {
            stage: StageTotals(count=0, value_paise=0) for stage in (*_OPEN_STAGES, "won", "lost")
        }
        for row in data["by_stage"]:
            by_stage[row["stage"]] = StageTotals(count=row["n"], value_paise=row["value_paise"])
        closed = {r["stage"]: r for r in data["closed_90d"]}
        won, lost = closed.get("won"), closed.get("lost")
        won_count = won["n"] if won else 0
        lost_count = lost["n"] if lost else 0
        win_rate = (
            None
            if won_count + lost_count == 0
            else (won_count * 10000 * 2 + (won_count + lost_count))
            // (2 * (won_count + lost_count))
        )  # integer half-up
        return PipelineOut(
            by_stage=by_stage,
            open_value_paise=sum(by_stage[s].value_paise for s in _OPEN_STAGES),
            won_last_90d=WonTotals(count=won_count, value_paise=won["value_paise"] if won else 0),
            lost_last_90d_count=lost_count,
            win_rate_bp_90d=win_rate,
            stale_open_deals=[StaleDeal(**r) for r in data["stale"]],
        )

    async def refresh(self, ctx: AuthContext, meta: RequestMeta) -> RefreshOut:
        async with self._uow_factory() as uow:
            await ReportRepository(uow.session, ctx.org_id).refresh_collections()
            record_audit(
                uow.session, org_id=ctx.org_id, action="reporting.refreshed", entity_type="reporting",
                entity_id=None, actor=ctx, meta=meta,
            )  # fmt: skip
        return RefreshOut(refreshed_at=self._clock.now_utc())

    async def audit_page(
        self,
        ctx: AuthContext,
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
        async with self._uow_factory() as uow:
            return await AuditReadRepository(uow.session, ctx.org_id).page(
                entity_type=entity_type, entity_id=entity_id, actor_user_id=actor_user_id, action=action,
                date_from=date_from, date_to=date_to, limit=limit, offset=offset,
            )  # fmt: skip
