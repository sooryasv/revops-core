from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import MetaDep, PaymentServiceDep, requires
from app.core.context import AuthContext
from app.core.permissions import Permission
from app.schemas.payments import ManualPaymentIn, PaymentOut

router = APIRouter(prefix="/invoices", tags=["payments"])

Reader = Annotated[AuthContext, Depends(requires(Permission.PAYMENTS_READ))]
Recorder = Annotated[AuthContext, Depends(requires(Permission.PAYMENTS_MANUAL))]


@router.get("/{invoice_id}/payments", response_model=list[PaymentOut])
async def list_payments(
    invoice_id: uuid.UUID, ctx: Reader, service: PaymentServiceDep
) -> list[PaymentOut]:
    return [PaymentOut.model_validate(p) for p in await service.list_for_invoice(ctx, invoice_id)]


@router.post("/{invoice_id}/payments/manual", response_model=PaymentOut, status_code=201)
async def record_manual_payment(
    invoice_id: uuid.UUID,
    body: ManualPaymentIn,
    ctx: Recorder,
    service: PaymentServiceDep,
    meta: MetaDep,
) -> PaymentOut:
    return PaymentOut.model_validate(await service.record_manual(ctx, invoice_id, body, meta))
