from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import CustomerServiceDep, MetaDep, requires
from app.core.context import AuthContext
from app.core.permissions import Permission
from app.schemas.common import Limit, Offset, Page
from app.schemas.customers import CustomerCreate, CustomerOut, CustomerPatch

router = APIRouter(prefix="/customers", tags=["customers"])

Reader = Annotated[AuthContext, Depends(requires(Permission.CUSTOMERS_READ))]
Writer = Annotated[AuthContext, Depends(requires(Permission.CUSTOMERS_WRITE))]


@router.get("", response_model=Page[CustomerOut])
async def list_customers(
    ctx: Reader,
    service: CustomerServiceDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    is_active: bool | None = None,
    limit: Limit = 25,
    offset: Offset = 0,
) -> Page[CustomerOut]:
    items, total = await service.search(ctx, q=q, is_active=is_active, limit=limit, offset=offset)
    return Page(
        items=[CustomerOut.model_validate(c) for c in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=CustomerOut, status_code=201)
async def create_customer(
    body: CustomerCreate, ctx: Writer, service: CustomerServiceDep, meta: MetaDep
) -> CustomerOut:
    return CustomerOut.model_validate(await service.create(ctx, body, meta))


@router.get("/{customer_id}", response_model=CustomerOut)
async def get_customer(
    customer_id: uuid.UUID, ctx: Reader, service: CustomerServiceDep
) -> CustomerOut:
    return CustomerOut.model_validate(await service.get(ctx, customer_id))


@router.patch("/{customer_id}", response_model=CustomerOut)
async def patch_customer(
    customer_id: uuid.UUID,
    body: CustomerPatch,
    ctx: Writer,
    service: CustomerServiceDep,
    meta: MetaDep,
) -> CustomerOut:
    return CustomerOut.model_validate(await service.update(ctx, customer_id, body, meta))
