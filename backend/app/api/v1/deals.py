from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DealServiceDep, MetaDep, requires
from app.api.v1.invoices import to_detail
from app.core.context import AuthContext
from app.core.permissions import Permission
from app.schemas.common import Limit, Offset, Page
from app.schemas.deals import DealCreate, DealOut, DealPatch, DealTransition, Stage
from app.schemas.invoices import InvoiceDetail

router = APIRouter(prefix="/deals", tags=["deals"])

Reader = Annotated[AuthContext, Depends(requires(Permission.DEALS_READ))]
Writer = Annotated[AuthContext, Depends(requires(Permission.DEALS_WRITE))]
Drafter = Annotated[AuthContext, Depends(requires(Permission.INVOICES_DRAFT))]


@router.get("", response_model=Page[DealOut])
async def list_deals(
    ctx: Reader,
    service: DealServiceDep,
    stage: Stage | None = None,
    customer_id: uuid.UUID | None = None,
    limit: Limit = 25,
    offset: Offset = 0,
) -> Page[DealOut]:
    items, total = await service.search(
        ctx, stage=stage, customer_id=customer_id, limit=limit, offset=offset
    )
    return Page(
        items=[DealOut.model_validate(d) for d in items], total=total, limit=limit, offset=offset
    )


@router.post("", response_model=DealOut, status_code=201)
async def create_deal(
    body: DealCreate, ctx: Writer, service: DealServiceDep, meta: MetaDep
) -> DealOut:
    return DealOut.model_validate(await service.create(ctx, body, meta))


@router.get("/{deal_id}", response_model=DealOut)
async def get_deal(deal_id: uuid.UUID, ctx: Reader, service: DealServiceDep) -> DealOut:
    return DealOut.model_validate(await service.get(ctx, deal_id))


@router.patch("/{deal_id}", response_model=DealOut)
async def patch_deal(
    deal_id: uuid.UUID, body: DealPatch, ctx: Writer, service: DealServiceDep, meta: MetaDep
) -> DealOut:
    return DealOut.model_validate(await service.update(ctx, deal_id, body, meta))


@router.post("/{deal_id}/transition", response_model=DealOut)
async def transition_deal(
    deal_id: uuid.UUID, body: DealTransition, ctx: Writer, service: DealServiceDep, meta: MetaDep
) -> DealOut:
    return DealOut.model_validate(await service.transition(ctx, deal_id, body, meta))


@router.post("/{deal_id}/create-invoice", response_model=InvoiceDetail, status_code=201)
async def create_invoice_from_deal(
    deal_id: uuid.UUID, ctx: Drafter, service: DealServiceDep, meta: MetaDep
) -> InvoiceDetail:
    return to_detail(await service.create_invoice(ctx, deal_id, meta))
