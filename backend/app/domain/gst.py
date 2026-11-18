"""GST maths in integer paise (03 section 4.2). Simplified on purpose: no cess, discount, or invoice round-off."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.domain.errors import FieldError, ValidationFailed
from app.domain.money import Paise, require_int, round_half_up_div

SupplyType = Literal["intra", "inter"]

# 0, 5, 18, 40 percent since 22 Sep 2025 (02 section 2.1). Basis points: 1800 means 18.00 percent.
SUPPORTED_GST_RATES_BP = frozenset({0, 500, 1800, 4000})

MAX_QUANTITY = 1_000_000
MAX_UNIT_PRICE_PAISE = 10**11
MAX_LINE_TAXABLE_PAISE = 10**14  # keeps even 200 lines far below the bigint limit
MAX_LINES = 200


@dataclass(frozen=True, slots=True)
class LineInput:
    quantity: int
    unit_price_paise: Paise
    gst_rate_bp: int


@dataclass(frozen=True, slots=True)
class LineTax:
    taxable_paise: Paise
    cgst_paise: Paise
    sgst_paise: Paise
    igst_paise: Paise
    line_total_paise: Paise


@dataclass(frozen=True, slots=True)
class InvoiceTotals:
    supply_type: SupplyType
    lines: tuple[LineTax, ...]
    subtotal_paise: Paise
    cgst_paise: Paise
    sgst_paise: Paise
    igst_paise: Paise
    total_paise: Paise


def supply_type_for(supplier_state_code: str, place_of_supply_state_code: str) -> SupplyType:
    return "intra" if supplier_state_code == place_of_supply_state_code else "inter"


def compute_line(line: LineInput, supply_type: SupplyType) -> LineTax:
    quantity = require_int("quantity", line.quantity)
    price = require_int("unit_price_paise", line.unit_price_paise)
    rate = require_int("gst_rate_bp", line.gst_rate_bp)
    if not 1 <= quantity <= MAX_QUANTITY:
        raise ValidationFailed(
            "quantity out of range", errors=[FieldError("quantity", f"1..{MAX_QUANTITY}")]
        )
    if not 0 <= price <= MAX_UNIT_PRICE_PAISE:
        raise ValidationFailed(
            "unit price out of range", errors=[FieldError("unit_price_paise", "out of range")]
        )
    if rate not in SUPPORTED_GST_RATES_BP:
        raise ValidationFailed(
            "unsupported GST rate",
            errors=[FieldError("gst_rate_bp", "must be 0, 500, 1800 or 4000")],
        )
    taxable = quantity * price
    if taxable > MAX_LINE_TAXABLE_PAISE:
        raise ValidationFailed(
            "line amount too large", errors=[FieldError("unit_price_paise", "line too large")]
        )

    if supply_type == "intra":
        # Every supported rate is even in basis points, so halving is exact (500 -> 250, 1800 -> 900).
        cgst = round_half_up_div(taxable * (rate // 2), 10_000)
        sgst, igst = cgst, 0
    else:
        cgst = sgst = 0
        igst = round_half_up_div(taxable * rate, 10_000)
    return LineTax(taxable, cgst, sgst, igst, taxable + cgst + sgst + igst)


def compute_invoice(
    supplier_state_code: str, place_of_supply_state_code: str, items: list[LineInput]
) -> InvoiceTotals:
    if not items:
        raise ValidationFailed(
            "an invoice needs at least one line", errors=[FieldError("items", "required")]
        )
    if len(items) > MAX_LINES:
        raise ValidationFailed(
            f"at most {MAX_LINES} lines", errors=[FieldError("items", f"max {MAX_LINES}")]
        )
    supply = supply_type_for(supplier_state_code, place_of_supply_state_code)
    lines = tuple(
        compute_line(item, supply) for item in items
    )  # rounding is per line; totals are plain sums
    return InvoiceTotals(
        supply_type=supply,
        lines=lines,
        subtotal_paise=sum(x.taxable_paise for x in lines),
        cgst_paise=sum(x.cgst_paise for x in lines),
        sgst_paise=sum(x.sgst_paise for x in lines),
        igst_paise=sum(x.igst_paise for x in lines),
        total_paise=sum(x.line_total_paise for x in lines),
    )
