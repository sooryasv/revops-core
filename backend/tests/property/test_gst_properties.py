from hypothesis import given
from hypothesis import strategies as st

from app.domain.gst import SUPPORTED_GST_RATES_BP, LineInput, compute_invoice, compute_line

rates = st.sampled_from(sorted(SUPPORTED_GST_RATES_BP))
lines = st.builds(
    LineInput,
    quantity=st.integers(1, 10_000),
    unit_price_paise=st.integers(0, 10**9),
    gst_rate_bp=rates,
)
states = st.sampled_from(["33", "29", "27", "07", "24", "32"])


@given(lines, st.sampled_from(["intra", "inter"]))
def test_line_invariants(line: LineInput, supply: str) -> None:
    t = compute_line(line, supply)  # type: ignore[arg-type]
    assert t.line_total_paise == t.taxable_paise + t.cgst_paise + t.sgst_paise + t.igst_paise
    assert t.taxable_paise == line.quantity * line.unit_price_paise
    if t.igst_paise > 0:
        assert t.cgst_paise == 0
        assert t.sgst_paise == 0
    if supply == "intra":
        assert t.igst_paise == 0
        assert t.cgst_paise == t.sgst_paise
    else:
        assert t.cgst_paise == t.sgst_paise == 0


@given(lines)
def test_tax_is_within_half_paise_of_exact(line: LineInput) -> None:
    taxable = line.quantity * line.unit_price_paise
    inter = compute_line(line, "inter")
    assert abs(inter.igst_paise * 10_000 - taxable * line.gst_rate_bp) <= 5_000
    intra = compute_line(line, "intra")
    assert abs(intra.cgst_paise * 10_000 - taxable * (line.gst_rate_bp // 2)) <= 5_000


@given(st.lists(lines, min_size=1, max_size=30), states, states)
def test_invoice_totals_reconcile(items: list[LineInput], supplier: str, pos: str) -> None:
    inv = compute_invoice(supplier, pos, items)
    assert inv.subtotal_paise == sum(x.taxable_paise for x in inv.lines)
    assert inv.cgst_paise == sum(x.cgst_paise for x in inv.lines)
    assert inv.sgst_paise == sum(x.sgst_paise for x in inv.lines)
    assert inv.igst_paise == sum(x.igst_paise for x in inv.lines)
    assert inv.total_paise == sum(x.line_total_paise for x in inv.lines)
    assert inv.total_paise == inv.subtotal_paise + inv.cgst_paise + inv.sgst_paise + inv.igst_paise
    assert inv.igst_paise == 0 or (inv.cgst_paise == 0 and inv.sgst_paise == 0)
    assert (inv.supply_type == "intra") == (supplier == pos)


@given(lines)
def test_zero_rate_never_taxes(line: LineInput) -> None:
    free = LineInput(line.quantity, line.unit_price_paise, 0)
    for supply in ("intra", "inter"):
        t = compute_line(free, supply)  # type: ignore[arg-type]
        assert t.cgst_paise == t.sgst_paise == t.igst_paise == 0
