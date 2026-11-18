from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import InvoiceServiceDep, MetaDep, requires
from app.core.context import AuthContext
from app.core.permissions import Permission
from app.domain.errors import FieldError, ValidationFailed
from app.schemas.common import Limit, Offset, Page
from app.schemas.invoices import (
    SORT_FIELDS,
    InvoiceCreate,
    InvoiceDetail,
    InvoicePreview,
    InvoicePreviewRequest,
    InvoiceReplace,
    InvoiceStatus,
    InvoiceSummary,
    ItemOut,
    PayLinkOut,
    PaymentStatus,
    PreviewLine,
    VoidRequest,
)
from app.services.invoices import InvoiceView

router = APIRouter(prefix="/invoices", tags=["invoices"])

Reader = Annotated[AuthContext, Depends(requires(Permission.INVOICES_READ))]
Drafter = Annotated[AuthContext, Depends(requires(Permission.INVOICES_DRAFT))]
Issuer = Annotated[AuthContext, Depends(requires(Permission.INVOICES_ISSUE))]
Voider = Annotated[AuthContext, Depends(requires(Permission.INVOICES_VOID))]
PayLinker = Annotated[AuthContext, Depends(requires(Permission.INVOICES_PAYLINK))]


def _summary_fields(view: InvoiceView) -> dict[str, object]:
    fields = {
        name: getattr(view.invoice, name)
        for name in InvoiceSummary.model_fields
        if hasattr(view.invoice, name)
    }
    return {**fields, "customer_name": view.customer_name, **view.derived}


def to_summary(view: InvoiceView) -> InvoiceSummary:
    return InvoiceSummary.model_validate(_summary_fields(view))


def to_detail(view: InvoiceView) -> InvoiceDetail:
    items = [ItemOut.model_validate(i) for i in view.items or []]
    return InvoiceDetail.model_validate({**_summary_fields(view), "items": items})


def _validated_sort(sort: str) -> str:
    if sort.lstrip("-") not in SORT_FIELDS:
        raise ValidationFailed(
            "unsupported sort field",
            errors=[FieldError("sort", f"one of {', '.join(SORT_FIELDS)}")],
        )
    return sort


@router.get("", response_model=Page[InvoiceSummary])
async def list_invoices(
    ctx: Reader,
    service: InvoiceServiceDep,
    status: InvoiceStatus | None = None,
    payment_status: PaymentStatus | None = None,
    overdue: bool | None = None,
    customer_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    issue_from: date | None = None,
    issue_to: date | None = None,
    sort: Annotated[str, Query(max_length=20)] = "-created_at",
    limit: Limit = 25,
    offset: Offset = 0,
) -> Page[InvoiceSummary]:
    views, total = await service.search(
        ctx, status=status, payment_status=payment_status, overdue=overdue, customer_id=customer_id, q=q,
        issue_from=issue_from, issue_to=issue_to, sort=_validated_sort(sort), limit=limit, offset=offset,
    )  # fmt: skip
    return Page(items=[to_summary(v) for v in views], total=total, limit=limit, offset=offset)


@router.post("", response_model=InvoiceDetail, status_code=201)
async def create_invoice(
    body: InvoiceCreate, ctx: Drafter, service: InvoiceServiceDep, meta: MetaDep
) -> InvoiceDetail:
    return to_detail(await service.create_draft(ctx, body, meta))


@router.post("/preview", response_model=InvoicePreview)
async def preview_invoice(
    body: InvoicePreviewRequest, ctx: Drafter, service: InvoiceServiceDep
) -> InvoicePreview:
    totals = await service.preview(ctx, body)
    return InvoicePreview(
        supply_type=totals.supply_type,
        lines=[PreviewLine.model_validate(line, from_attributes=True) for line in totals.lines],
        subtotal_paise=totals.subtotal_paise,
        cgst_paise=totals.cgst_paise,
        sgst_paise=totals.sgst_paise,
        igst_paise=totals.igst_paise,
        total_paise=totals.total_paise,
    )


@router.get("/{invoice_id}", response_model=InvoiceDetail)
async def get_invoice(
    invoice_id: uuid.UUID, ctx: Reader, service: InvoiceServiceDep
) -> InvoiceDetail:
    return to_detail(await service.get(ctx, invoice_id))


@router.put("/{invoice_id}", response_model=InvoiceDetail)
async def replace_invoice(
    invoice_id: uuid.UUID,
    body: InvoiceReplace,
    ctx: Drafter,
    service: InvoiceServiceDep,
    meta: MetaDep,
) -> InvoiceDetail:
    return to_detail(await service.replace_draft(ctx, invoice_id, body, meta))


@router.post("/{invoice_id}/issue", response_model=InvoiceDetail)
async def issue_invoice(
    invoice_id: uuid.UUID, ctx: Issuer, service: InvoiceServiceDep, meta: MetaDep
) -> InvoiceDetail:
    return to_detail(await service.issue(ctx, invoice_id, meta))


@router.post("/{invoice_id}/void", response_model=InvoiceDetail)
async def void_invoice(
    invoice_id: uuid.UUID, body: VoidRequest, ctx: Voider, service: InvoiceServiceDep, meta: MetaDep
) -> InvoiceDetail:
    return to_detail(await service.void(ctx, invoice_id, body.reason, meta))


@router.post("/{invoice_id}/pay-link", response_model=PayLinkOut, status_code=201)
async def create_pay_link(
    invoice_id: uuid.UUID, ctx: PayLinker, service: InvoiceServiceDep, meta: MetaDep
) -> PayLinkOut:
    url, expires = await service.create_pay_link(ctx, invoice_id, meta)
    return PayLinkOut(url=url, expires_at=expires)
