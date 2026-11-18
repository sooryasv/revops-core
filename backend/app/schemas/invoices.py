from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import Field, StrictInt

from app.domain.gst import MAX_LINES, MAX_QUANTITY, MAX_UNIT_PRICE_PAISE
from app.schemas.common import ApiModel, StrictModel

InvoiceStatus = Literal["draft", "issued", "void"]
PaymentStatus = Literal["unpaid", "partially_paid", "paid"]
AgingBucket = Literal["not_due", "1-30", "31-60", "61-90", "90+"]
GstRateBp = Literal[0, 500, 1800, 4000]
SupplyTypeOut = Literal["intra", "inter"]

# Sortable fields for GET /invoices ("-field" sorts descending). The repository maps each to a column; a test keeps
# the two lists identical.
SORT_FIELDS = ("created_at", "issue_date", "due_date", "total_paise", "number")


class ItemIn(StrictModel):
    description: str = Field(min_length=1, max_length=500)
    hsn_sac: str | None = Field(default=None, pattern=r"^\d{4,8}$")
    quantity: StrictInt = Field(ge=1, le=MAX_QUANTITY)
    unit_price_paise: StrictInt = Field(ge=0, le=MAX_UNIT_PRICE_PAISE)
    gst_rate_bp: GstRateBp


class InvoiceCreate(StrictModel):
    customer_id: uuid.UUID
    deal_id: uuid.UUID | None = None
    payment_terms_days: StrictInt | None = Field(
        default=None, ge=0, le=365
    )  # None: the customer's default
    place_of_supply_state_code: str | None = Field(
        default=None, min_length=2, max_length=2
    )  # None: customer's state
    items: list[ItemIn] = Field(min_length=1, max_length=MAX_LINES)


class InvoiceReplace(StrictModel):
    """PUT body: the whole editable document plus the version the client last saw (optimistic lock)."""

    version: StrictInt = Field(ge=1)
    payment_terms_days: StrictInt = Field(ge=0, le=365)
    place_of_supply_state_code: str = Field(min_length=2, max_length=2)
    items: list[ItemIn] = Field(min_length=1, max_length=MAX_LINES)


class InvoicePreviewRequest(StrictModel):
    customer_id: uuid.UUID | None = None
    place_of_supply_state_code: str | None = Field(default=None, min_length=2, max_length=2)
    items: list[ItemIn] = Field(min_length=1, max_length=MAX_LINES)


class VoidRequest(StrictModel):
    reason: str = Field(min_length=1, max_length=500)


class ItemOut(ApiModel):
    line_no: int
    description: str
    hsn_sac: str | None
    quantity: int
    unit_price_paise: int
    gst_rate_bp: int
    taxable_paise: int
    cgst_paise: int
    sgst_paise: int
    igst_paise: int
    line_total_paise: int


class InvoiceSummary(ApiModel):
    id: uuid.UUID
    number: str | None
    status: InvoiceStatus
    customer_id: uuid.UUID
    customer_name: str
    deal_id: uuid.UUID | None
    issue_date: date | None
    due_date: date | None
    payment_terms_days: int
    place_of_supply_state_code: str
    supply_type: SupplyTypeOut | None
    subtotal_paise: int
    cgst_paise: int
    sgst_paise: int
    igst_paise: int
    total_paise: int
    amount_paid_paise: int
    void_reason: str | None
    version: int
    currency: str
    created_at: datetime
    updated_at: datetime
    # derived, never stored
    payment_status: PaymentStatus
    amount_due_paise: int
    overpaid_paise: int
    is_overdue: bool
    days_overdue: int
    aging_bucket: AgingBucket
    statutory_limit_days: int | None
    terms_exceed_statutory: bool
    pay_link_active: bool


class InvoiceDetail(InvoiceSummary):
    items: list[ItemOut]


class PreviewLine(ApiModel):
    taxable_paise: int
    cgst_paise: int
    sgst_paise: int
    igst_paise: int
    line_total_paise: int


class InvoicePreview(ApiModel):
    supply_type: SupplyTypeOut
    lines: list[PreviewLine]
    subtotal_paise: int
    cgst_paise: int
    sgst_paise: int
    igst_paise: int
    total_paise: int


class PayLinkOut(ApiModel):
    url: str
    expires_at: datetime
