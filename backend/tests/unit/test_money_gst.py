import pytest

from app.domain.errors import ValidationFailed
from app.domain.gst import (
    SUPPORTED_GST_RATES_BP,
    LineInput,
    compute_invoice,
    compute_line,
    supply_type_for,
)
from app.domain.money import round_half_up_div


@pytest.mark.parametrize(
    ("n", "d", "expected"),
    [(0, 10, 0), (4, 10, 0), (5, 10, 1), (6, 10, 1), (14, 10, 1), (15, 10, 2), (10_000, 10_000, 1)],
)
def test_round_half_up_div(n: int, d: int, expected: int) -> None:
    assert round_half_up_div(n, d) == expected


@pytest.mark.parametrize(("n", "d"), [(-1, 10), (1, 0), (1, -5)])
def test_round_half_up_div_rejects_bad_input(n: int, d: int) -> None:
    with pytest.raises(ValueError, match="non-negative"):
        round_half_up_div(n, d)


def test_supported_rates_are_exactly_the_post_2025_slabs() -> None:
    assert frozenset({0, 500, 1800, 4000}) == SUPPORTED_GST_RATES_BP


def test_supply_type_intra_when_states_equal() -> None:
    assert supply_type_for("33", "33") == "intra"
    assert supply_type_for("33", "29") == "inter"


def test_intra_line_splits_tax_evenly() -> None:
    t = compute_line(LineInput(quantity=2, unit_price_paise=50_000, gst_rate_bp=1800), "intra")
    assert (t.taxable_paise, t.cgst_paise, t.sgst_paise, t.igst_paise) == (100_000, 9_000, 9_000, 0)
    assert t.line_total_paise == 118_000


def test_inter_line_uses_igst_only() -> None:
    t = compute_line(LineInput(quantity=1, unit_price_paise=100_000, gst_rate_bp=1800), "inter")
    assert (t.cgst_paise, t.sgst_paise, t.igst_paise) == (0, 0, 18_000)


def test_rounding_is_half_up_per_line() -> None:
    # taxable 10 paise at 5%: exact tax 0.5 paise, intra halves to 0.25 each -> 0; inter -> 0.5 -> 1
    one = LineInput(quantity=1, unit_price_paise=10, gst_rate_bp=500)
    assert compute_line(one, "intra").cgst_paise == 0
    assert compute_line(one, "inter").igst_paise == 1


def test_zero_rate_has_no_tax() -> None:
    t = compute_line(LineInput(quantity=3, unit_price_paise=999, gst_rate_bp=0), "inter")
    assert t.igst_paise == 0
    assert t.line_total_paise == 2_997


@pytest.mark.parametrize(
    "bad",
    [
        LineInput(quantity=0, unit_price_paise=1, gst_rate_bp=0),
        LineInput(quantity=1, unit_price_paise=-1, gst_rate_bp=0),
        LineInput(quantity=1, unit_price_paise=1, gst_rate_bp=1200),
        LineInput(quantity=1, unit_price_paise=1, gst_rate_bp=2800),
        LineInput(quantity=10**9, unit_price_paise=1, gst_rate_bp=0),
        LineInput(quantity=1_000_000, unit_price_paise=10**11, gst_rate_bp=0),
    ],
)
def test_invalid_lines_are_rejected(bad: LineInput) -> None:
    with pytest.raises(ValidationFailed):
        compute_line(bad, "intra")


def test_non_integer_inputs_are_rejected() -> None:
    with pytest.raises(ValidationFailed):
        compute_line(LineInput(quantity=1.5, unit_price_paise=1, gst_rate_bp=0), "intra")  # type: ignore[arg-type]


def test_invoice_totals_are_sums_of_lines() -> None:
    lines = [
        LineInput(quantity=1, unit_price_paise=10_000, gst_rate_bp=500),
        LineInput(quantity=3, unit_price_paise=3_333, gst_rate_bp=1800),
    ]
    inv = compute_invoice("33", "33", lines)
    assert inv.supply_type == "intra"
    assert inv.subtotal_paise == sum(x.taxable_paise for x in inv.lines)
    assert inv.cgst_paise == sum(x.cgst_paise for x in inv.lines)
    assert inv.total_paise == inv.subtotal_paise + inv.cgst_paise + inv.sgst_paise + inv.igst_paise


def test_invoice_requires_at_least_one_line_and_a_cap() -> None:
    with pytest.raises(ValidationFailed):
        compute_invoice("33", "33", [])
    too_many = [LineInput(quantity=1, unit_price_paise=1, gst_rate_bp=0)] * 201
    with pytest.raises(ValidationFailed):
        compute_invoice("33", "33", too_many)
