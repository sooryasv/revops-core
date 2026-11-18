from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import Field, StrictInt

from app.schemas.common import ApiModel, StrictModel
from app.schemas.invoices import PaymentStatus

ManualMethod = Literal["cash", "bank_transfer", "upi", "cheque", "other"]
MAX_PAYMENT_PAISE = 10**13


class ManualPaymentIn(StrictModel):
    amount_paise: StrictInt = Field(ge=1, le=MAX_PAYMENT_PAISE)
    method: ManualMethod
    paid_on: date
    reference: str | None = Field(default=None, max_length=200)


class PaymentOut(ApiModel):
    id: uuid.UUID
    invoice_id: uuid.UUID
    provider: Literal["razorpay", "manual"]
    status: Literal["authorized", "captured", "failed"]
    amount_paise: int
    method: str | None
    paid_on: date | None
    captured_at: datetime | None
    failure_code: str | None
    reference: str | None
    created_at: datetime


class PublicLine(ApiModel):
    description: str
    quantity: int
    unit_price_paise: int
    gst_rate_bp: int
    line_total_paise: int


class PublicInvoiceOut(ApiModel):
    """What a payer sees. No internal ids, no tenant id, no contact details."""

    supplier_name: str
    invoice_number: str
    issue_date: date
    due_date: date
    currency: str
    items: list[PublicLine]
    subtotal_paise: int
    cgst_paise: int
    sgst_paise: int
    igst_paise: int
    total_paise: int
    amount_paid_paise: int
    amount_due_paise: int
    payment_status: PaymentStatus
    can_pay: bool


class PublicOrderOut(ApiModel):
    order_id: str
    amount_paise: int
    currency: str
    key_id: str


class ConfirmIn(StrictModel):
    order_id: str = Field(min_length=1, max_length=100)
    payment_id: str = Field(min_length=1, max_length=100)
    signature: str = Field(min_length=1, max_length=200)


class ConfirmOut(ApiModel):
    status: Literal["pending_confirmation"] = "pending_confirmation"
