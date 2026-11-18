"""Report DTOs. Money is integer paise; DSO is a ratio in days (not money), so it is a plain number."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import ApiModel

AgingBucket = Literal["not_due", "1-30", "31-60", "61-90", "90+"]


class InvoiceStatusCounts(BaseModel):
    draft: int = 0
    issued: int = 0
    void: int = 0


class TopOverdueCustomer(BaseModel):
    customer_id: uuid.UUID
    customer_name: str
    overdue_paise: int
    invoices: int


class RecentPayment(BaseModel):
    payment_id: uuid.UUID
    invoice_id: uuid.UUID
    invoice_number: str
    customer_name: str
    amount_paise: int
    paid_on: date
    provider: Literal["razorpay", "manual"]


class SummaryOut(BaseModel):
    as_of_date: date
    outstanding_paise: int
    overdue_paise: int
    overdue_invoices: int
    open_invoices: int
    billed_last_30d_paise: int
    collected_last_30d_paise: int
    dso_30d: float | None = Field(
        description="outstanding / billed in the last 30 days * 30; null when nothing billed"
    )
    invoices: InvoiceStatusCounts
    top_overdue_customers: list[TopOverdueCustomer]
    recent_payments: list[RecentPayment]
    webhook_problems_7d: int


class AgingRow(BaseModel):
    bucket: AgingBucket
    invoices: int
    due_paise: int


class AgingOut(BaseModel):
    as_of_date: date
    buckets: list[AgingRow]
    total_due_paise: int


class CollectionsRow(BaseModel):
    period: str = Field(description="YYYY-MM-DD for group=day, YYYY-MM for group=month")
    billed_paise: int
    collected_paise: int
    invoices_issued: int
    payments_count: int


class CollectionsOut(BaseModel):
    date_from: date
    date_to: date
    group: Literal["day", "month"]
    rows: list[CollectionsRow]
    note: str = "Served from a materialized view; it is refreshed by a job or POST /admin/reporting/refresh."


class DsoMonth(BaseModel):
    month: str
    billed_paise: int
    collected_paise: int
    outstanding_end_paise: int
    dso: float | None


class DsoOut(BaseModel):
    months: list[DsoMonth]
    current_dso_30d: float | None


class StageTotals(BaseModel):
    count: int
    value_paise: int


class WonTotals(BaseModel):
    count: int
    value_paise: int


class StaleDeal(BaseModel):
    deal_id: uuid.UUID
    title: str
    customer_name: str
    stage: str
    days_in_stage: int


class PipelineOut(BaseModel):
    by_stage: dict[str, StageTotals]
    open_value_paise: int
    won_last_90d: WonTotals
    lost_last_90d_count: int
    win_rate_bp_90d: int | None
    stale_open_deals: list[StaleDeal]


class RefreshOut(BaseModel):
    refreshed_at: datetime


class AuditOut(ApiModel):
    id: uuid.UUID
    actor_type: str
    actor_user_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    request_id: str | None
    created_at: datetime
