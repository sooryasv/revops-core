from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.deps import client_ip, get_public_pay_service
from app.core.permissions import public
from app.schemas.payments import ConfirmIn, ConfirmOut, PublicInvoiceOut, PublicLine, PublicOrderOut
from app.services.public_pay import PublicPayService

router = APIRouter(prefix="/public/pay", tags=["public-pay"], dependencies=[Depends(public)])


def _service(request: Request) -> PublicPayService:
    return get_public_pay_service(request)


@router.get("/{token}", response_model=PublicInvoiceOut)
async def view_invoice(token: str, request: Request) -> PublicInvoiceOut:
    view = await _service(request).view(token, client_ip(request))
    inv = view.invoice
    assert inv.number is not None
    assert inv.issue_date is not None
    assert inv.due_date is not None
    return PublicInvoiceOut(
        supplier_name=view.supplier_name,
        invoice_number=inv.number,
        issue_date=inv.issue_date,
        due_date=inv.due_date,
        currency=inv.currency,
        items=[PublicLine.model_validate(i, from_attributes=True) for i in view.items],
        subtotal_paise=inv.subtotal_paise,
        cgst_paise=inv.cgst_paise,
        sgst_paise=inv.sgst_paise,
        igst_paise=inv.igst_paise,
        total_paise=inv.total_paise,
        amount_paid_paise=inv.amount_paid_paise,
        amount_due_paise=view.amount_due_paise,
        payment_status=view.payment_status,  # type: ignore[arg-type]
        can_pay=view.can_pay,
    )


@router.post("/{token}/order", response_model=PublicOrderOut)
async def create_order(token: str, request: Request) -> PublicOrderOut:
    result = await _service(request).create_order(token, client_ip(request))
    return PublicOrderOut(
        order_id=result.order_id,
        amount_paise=result.amount_paise,
        currency=result.currency,
        key_id=result.key_id,
    )


@router.post("/{token}/confirm", response_model=ConfirmOut)
async def confirm_payment(token: str, body: ConfirmIn, request: Request) -> ConfirmOut:
    await _service(request).confirm(
        token, client_ip(request), body.order_id, body.payment_id, body.signature
    )
    return ConfirmOut()
