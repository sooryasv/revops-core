from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import MetaDep, ReportServiceDep, requires
from app.core.context import AuthContext
from app.core.permissions import Permission
from app.schemas.common import Limit, Offset, Page
from app.schemas.reports import (
    AgingOut,
    AuditOut,
    CollectionsOut,
    DsoOut,
    PipelineOut,
    RefreshOut,
    SummaryOut,
)

router = APIRouter(tags=["reports"])

Finance = Annotated[AuthContext, Depends(requires(Permission.REPORTS_FINANCE))]
Pipeline = Annotated[AuthContext, Depends(requires(Permission.REPORTS_PIPELINE))]
AuditReader = Annotated[AuthContext, Depends(requires(Permission.AUDIT_READ))]
Jobs = Annotated[AuthContext, Depends(requires(Permission.ADMIN_JOBS))]


@router.get("/reports/summary", response_model=SummaryOut)
async def summary(ctx: Finance, service: ReportServiceDep) -> SummaryOut:
    return await service.summary(ctx)


@router.get("/reports/aging", response_model=AgingOut)
async def aging(ctx: Finance, service: ReportServiceDep) -> AgingOut:
    return await service.aging(ctx)


@router.get("/reports/collections", response_model=CollectionsOut)
async def collections(
    ctx: Finance,
    service: ReportServiceDep,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    group: Literal["day", "month"] = "day",
) -> CollectionsOut:
    return await service.collections(ctx, date_from=date_from, date_to=date_to, group=group)


@router.get("/reports/dso", response_model=DsoOut)
async def dso_trend(
    ctx: Finance, service: ReportServiceDep, months: Annotated[int, Query(ge=1, le=12)] = 6
) -> DsoOut:
    return await service.dso(ctx, months=months)


@router.get("/reports/pipeline", response_model=PipelineOut)
async def pipeline(ctx: Pipeline, service: ReportServiceDep) -> PipelineOut:
    return await service.pipeline(ctx)


@router.get("/audit-logs", response_model=Page[AuditOut])
async def audit_logs(
    ctx: AuditReader,
    service: ReportServiceDep,
    entity_type: Annotated[str | None, Query(max_length=60)] = None,
    entity_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    action: Annotated[str | None, Query(max_length=80)] = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    limit: Limit = 25,
    offset: Offset = 0,
) -> Page[AuditOut]:
    items, total = await service.audit_page(
        ctx, entity_type=entity_type, entity_id=entity_id, actor_user_id=actor_user_id, action=action,
        date_from=date_from, date_to=date_to, limit=limit, offset=offset,
    )  # fmt: skip
    return Page(
        items=[AuditOut.model_validate(r) for r in items], total=total, limit=limit, offset=offset
    )


@router.post("/admin/reporting/refresh", response_model=RefreshOut)
async def refresh_reporting(ctx: Jobs, service: ReportServiceDep, meta: MetaDep) -> RefreshOut:
    return await service.refresh(ctx, meta)
