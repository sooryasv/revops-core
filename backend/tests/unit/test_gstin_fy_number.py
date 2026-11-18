from datetime import UTC, date, datetime

import pytest

from app.domain.errors import ValidationFailed
from app.domain.fiscal_year import fiscal_year_bounds, fy_label_for, fy_label_for_instant
from app.domain.gstin import STATE_CODES, make_test_gstin, validate_gstin, validate_state_code
from app.domain.invoice_number import MAX_COUNTER, format_invoice_number, is_valid_serial


def test_state_codes_contain_tamil_nadu_and_special_codes() -> None:
    assert STATE_CODES["33"] == "Tamil Nadu"
    assert {"97", "99", "38", "36"} <= STATE_CODES.keys()


def test_validate_state_code() -> None:
    assert validate_state_code("33") == "33"
    for bad in ("3", "333", "00", "abc", "40"):
        with pytest.raises(ValidationFailed):
            validate_state_code(bad)


def test_generated_gstin_passes_and_matches_state() -> None:
    g = make_test_gstin("33", seed=1)
    assert len(g) == 15
    assert g.startswith("33")
    assert validate_gstin(g, "33") == g


def test_gstin_state_prefix_must_match() -> None:
    g = make_test_gstin("29", seed=2)
    with pytest.raises(ValidationFailed, match="state"):
        validate_gstin(g, "33")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "33ABCDE1234F1Z",
        "33abcde1234f1z5",
        "33ABCDE1234F1X5",
        "33ABCDE12345Z5Z",
        "XX" + "A" * 13,
    ],
)
def test_malformed_gstins_rejected(bad: str) -> None:
    with pytest.raises(ValidationFailed):
        validate_gstin(bad, "33")


def test_test_gstins_are_deterministic_and_distinct_per_seed() -> None:
    assert make_test_gstin("33", seed=5) == make_test_gstin("33", seed=5)
    assert make_test_gstin("33", seed=5) != make_test_gstin("33", seed=6)


def test_fy_label_boundaries() -> None:
    assert fy_label_for(date(2027, 3, 31)) == "26-27"
    assert fy_label_for(date(2027, 4, 1)) == "27-28"
    assert fy_label_for(date(2026, 4, 1)) == "26-27"
    assert fy_label_for(date(2026, 3, 31)) == "25-26"
    assert fy_label_for(date(2099, 6, 1)) == "99-00"


def test_fy_boundary_at_ist_midnight_not_utc_midnight() -> None:
    # 31 Mar 23:59:59 IST is still the old FY; 1 Apr 00:00:00 IST starts the new one.
    assert fy_label_for_instant(datetime(2027, 3, 31, 18, 29, 59, tzinfo=UTC)) == "26-27"
    assert fy_label_for_instant(datetime(2027, 3, 31, 18, 30, 0, tzinfo=UTC)) == "27-28"
    # 1 Apr 02:00 UTC is already 1 Apr IST, but 31 Mar 20:00 UTC is 1 Apr IST too (UTC date differs).
    assert fy_label_for_instant(datetime(2027, 3, 31, 20, 0, tzinfo=UTC)) == "27-28"


def test_fiscal_year_bounds() -> None:
    assert fiscal_year_bounds("26-27") == (date(2026, 4, 1), date(2027, 3, 31))
    with pytest.raises(ValidationFailed):
        fiscal_year_bounds("26-28")


def test_invoice_number_format_and_length() -> None:
    n = format_invoice_number("26-27", 1)
    assert n == "INV/26-27/000001"
    assert len(n) == 16
    assert is_valid_serial(format_invoice_number("26-27", MAX_COUNTER))


def test_invoice_number_overflow_and_underflow() -> None:
    for bad in (0, -1, MAX_COUNTER + 1):
        with pytest.raises(ValidationFailed):
            format_invoice_number("26-27", bad)


def test_serial_rule_46b_charset_and_length() -> None:
    assert is_valid_serial("INV/26-27/000001")
    assert not is_valid_serial("INV/26-27/0000012")  # 17 chars
    assert not is_valid_serial("INV 26-27")  # space
    assert not is_valid_serial("INV_26")  # underscore
    assert not is_valid_serial("")
