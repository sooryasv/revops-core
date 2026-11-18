from datetime import date

import pytest

from app.domain import deal_rules, invoice_rules, payment_rules, statutory
from app.domain.errors import InvalidState, ValidationFailed

TODAY = date(2026, 11, 1)


# ---- invoice derived fields
@pytest.mark.parametrize(
    ("total", "paid", "expected"),
    [
        (100, 0, "unpaid"),
        (100, 1, "partially_paid"),
        (100, 99, "partially_paid"),
        (100, 100, "paid"),
        (100, 150, "paid"),
    ],
)
def test_payment_status(total: int, paid: int, expected: str) -> None:
    assert invoice_rules.payment_status(total, paid) == expected


def test_amount_due_and_overpaid() -> None:
    assert invoice_rules.amount_due(100, 30) == 70
    assert invoice_rules.amount_due(100, 130) == 0
    assert invoice_rules.overpaid(100, 130) == 30
    assert invoice_rules.overpaid(100, 100) == 0


def test_due_date_adds_terms() -> None:
    assert invoice_rules.compute_due_date(date(2026, 1, 31), 30) == date(2026, 3, 2)
    with pytest.raises(ValidationFailed):
        invoice_rules.compute_due_date(date(2026, 1, 1), -1)
    with pytest.raises(ValidationFailed):
        invoice_rules.compute_due_date(date(2026, 1, 1), 366)


def test_overdue_boundary_is_strictly_after_due_date() -> None:
    due = date(2026, 11, 1)
    assert not invoice_rules.is_overdue("issued", due, 100, today=date(2026, 11, 1))
    assert invoice_rules.is_overdue("issued", due, 100, today=date(2026, 11, 2))
    assert invoice_rules.days_overdue("issued", due, 100, today=date(2026, 11, 2)) == 1


def test_overdue_requires_issued_and_amount_due() -> None:
    due = date(2026, 1, 1)
    assert not invoice_rules.is_overdue("draft", due, 100, today=TODAY)
    assert not invoice_rules.is_overdue("void", due, 100, today=TODAY)
    assert not invoice_rules.is_overdue("issued", due, 0, today=TODAY)
    assert not invoice_rules.is_overdue("issued", None, 100, today=TODAY)
    assert invoice_rules.days_overdue("issued", due, 0, today=TODAY) == 0


@pytest.mark.parametrize(
    ("days", "bucket"),
    [
        (0, "not_due"),
        (-5, "not_due"),
        (1, "1-30"),
        (30, "1-30"),
        (31, "31-60"),
        (60, "31-60"),
        (61, "61-90"),
        (90, "61-90"),
        (91, "90+"),
    ],
)
def test_aging_buckets(days: int, bucket: str) -> None:
    from datetime import timedelta

    due = TODAY - timedelta(days=days)
    assert invoice_rules.aging_bucket("issued", due, 100, today=TODAY) == bucket


def test_can_edit_only_drafts() -> None:
    invoice_rules.assert_editable("draft")
    for s in ("issued", "void"):
        with pytest.raises(InvalidState):
            invoice_rules.assert_editable(s)


def test_issue_preconditions() -> None:
    invoice_rules.assert_issuable("draft", item_count=1, total_paise=1)
    with pytest.raises(InvalidState):
        invoice_rules.assert_issuable("issued", item_count=1, total_paise=1)
    with pytest.raises(ValidationFailed):
        invoice_rules.assert_issuable("draft", item_count=0, total_paise=0)
    with pytest.raises(ValidationFailed):
        invoice_rules.assert_issuable("draft", item_count=1, total_paise=0)


def test_void_rules() -> None:
    assert (
        invoice_rules.validate_void("issued", paid_paise=0, reason="  duplicate  ") == "duplicate"
    )
    with pytest.raises(InvalidState):
        invoice_rules.validate_void("draft", paid_paise=0, reason="x")
    with pytest.raises(InvalidState):
        invoice_rules.validate_void("issued", paid_paise=1, reason="x")
    with pytest.raises(ValidationFailed):
        invoice_rules.validate_void("issued", paid_paise=0, reason="   ")
    with pytest.raises(ValidationFailed):
        invoice_rules.validate_void("issued", paid_paise=0, reason="x" * 501)


# ---- deals
def test_deal_forward_transitions() -> None:
    for a, b in [("lead", "qualified"), ("qualified", "proposal"), ("proposal", "won")]:
        assert deal_rules.validate_transition(a, b, None) is None


@pytest.mark.parametrize("stage", ["lead", "qualified", "proposal"])
def test_any_open_stage_can_be_lost_with_reason(stage: str) -> None:
    assert deal_rules.validate_transition(stage, "lost", " budget cut ") == "budget cut"


def test_lost_requires_reason() -> None:
    for reason in (None, "", "   "):
        with pytest.raises(ValidationFailed):
            deal_rules.validate_transition("lead", "lost", reason)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("lead", "won"),
        ("lead", "proposal"),
        ("qualified", "won"),
        ("proposal", "lead"),
        ("won", "lost"),
        ("lost", "lead"),
        ("won", "won"),
    ],
)
def test_illegal_transitions(a: str, b: str) -> None:
    with pytest.raises(InvalidState):
        deal_rules.validate_transition(a, b, "r")


def test_unknown_stage_is_validation_error() -> None:
    with pytest.raises(ValidationFailed):
        deal_rules.validate_transition("lead", "bogus", None)


def test_terminal_stages() -> None:
    assert deal_rules.is_terminal("won")
    assert deal_rules.is_terminal("lost")
    assert not deal_rules.is_terminal("lead")


def test_only_won_deals_can_create_invoices() -> None:
    deal_rules.assert_can_invoice("won")
    with pytest.raises(InvalidState):
        deal_rules.assert_can_invoice("proposal")


# ---- payments
def test_status_merge_is_monotonic() -> None:
    m = payment_rules.merge_payment_status
    assert m(None, "authorized") == "authorized"
    assert m("authorized", "captured") == "captured"
    assert m("captured", "authorized") == "captured"
    assert m("captured", "failed") == "captured"
    assert m("authorized", "failed") == "failed"
    assert m("failed", "authorized") == "failed"


def test_captured_total_sums_only_captured() -> None:
    rows = [("captured", 100), ("authorized", 500), ("failed", 700), ("captured", 50)]
    assert payment_rules.captured_total(rows) == 150


def test_amount_and_currency_check() -> None:
    assert payment_rules.amount_matches(1000, 1000, "INR")
    assert payment_rules.amount_matches(
        1000, 1000, ""
    )  # docs sample shows empty currency (SPEC_ISSUES S6)
    assert not payment_rules.amount_matches(1000, 999, "INR")
    assert not payment_rules.amount_matches(1000, 1000, "USD")


def test_manual_payment_validation() -> None:
    payment_rules.validate_manual_payment("issued", amount_paise=100, amount_due_paise=100)
    with pytest.raises(InvalidState):
        payment_rules.validate_manual_payment("draft", amount_paise=1, amount_due_paise=1)
    with pytest.raises(ValidationFailed):
        payment_rules.validate_manual_payment("issued", amount_paise=0, amount_due_paise=10)
    with pytest.raises(ValidationFailed):
        payment_rules.validate_manual_payment("issued", amount_paise=11, amount_due_paise=10)


def test_overpayment_detection() -> None:
    assert payment_rules.is_overpaid(100, 101)
    assert not payment_rules.is_overpaid(100, 100)


# ---- statutory
@pytest.mark.parametrize(
    ("cat", "agreement", "limit"),
    [
        ("micro", True, 45),
        ("small", True, 45),
        ("micro", False, 15),
        ("small", False, 15),
        ("medium", True, None),
        ("none", False, None),
    ],
)
def test_statutory_limit(cat: str, agreement: bool, limit: int | None) -> None:
    assert statutory.statutory_limit_days(cat, agreement) == limit


def test_terms_exceed_statutory() -> None:
    assert statutory.terms_exceed_statutory(15, 30)
    assert not statutory.terms_exceed_statutory(45, 45)
    assert not statutory.terms_exceed_statutory(None, 365)
