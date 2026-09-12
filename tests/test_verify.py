from datetime import date

from ar_followup.models import money
from ar_followup.qbo.reads import parse_deposit, parse_invoice, parse_payment
from ar_followup.verify import (
    amounts_match,
    short_pay_reasons,
    unapplied_payment_candidates,
    verify_invoice,
)

from conftest import FakeQbo, deposit_row, invoice_row, payment_row

TODAY = date(2026, 9, 12)


def inv(**kwargs):
    return parse_invoice(invoice_row(**kwargs), 15)


def test_exact_and_near_matches(settings):
    assert amounts_match(money(3000), money(3000), settings) == "exact"
    assert amounts_match(money(3000), money(2990), settings) == "near"
    assert amounts_match(money(3000), money(1500), settings) is None


def test_unapplied_payment_on_account_is_caught(settings):
    """Troomy: the money is in QBO, just not on the invoice."""
    invoice = inv(balance=3000)
    payment = parse_payment(payment_row(total=3000, unapplied=3000))
    matches = unapplied_payment_candidates(invoice, [payment], [], settings)
    assert len(matches) == 1
    assert matches[0].confidence == "exact"
    assert matches[0].source == "payment"


def test_bank_deposit_matching_the_balance_is_caught(settings):
    """OcuSoft: the money is in Chase, not yet in QBO at all."""
    invoice = inv(balance=3000)
    deposit = parse_deposit(deposit_row(total=3000, account="Chase Business Checking"))
    matches = unapplied_payment_candidates(invoice, [], [deposit], settings)
    assert len(matches) == 1
    assert matches[0].source == "deposit"
    assert "Chase" in matches[0].account


def test_deposit_line_for_a_different_customer_is_ignored(settings):
    invoice = inv(balance=3000, customer_name="Acme Brands")
    deposit = parse_deposit(deposit_row(total=3000, customer_names=["Someone Else LLC"]))
    assert unapplied_payment_candidates(invoice, [], [deposit], settings) == []


def test_payment_already_applied_to_this_invoice_is_not_a_candidate(settings):
    invoice = inv(id="7", balance=3000)
    payment = parse_payment(payment_row(total=3000, applied_invoice_ids=["7"]))
    assert unapplied_payment_candidates(invoice, [payment], [], settings) == []


def test_payment_referencing_the_wrong_invoice_number_is_a_review_item(settings):
    invoice = inv(id="7", doc_number="1042", balance=3000)
    payment = parse_payment(
        payment_row(total=999, applied_invoice_ids=["8"], reference="1042")
    )
    matches = unapplied_payment_candidates(invoice, [payment], [], settings)
    assert len(matches) == 1
    assert "references invoice #1042" in matches[0].note


def test_another_customers_payment_is_ignored(settings):
    invoice = inv(balance=3000, customer_id="10")
    payment = parse_payment(payment_row(customer_id="99", total=3000, unapplied=3000))
    assert unapplied_payment_candidates(invoice, [payment], [], settings) == []


def test_partial_payment_goes_to_review(settings):
    invoice = inv(total=3000, balance=1200)
    reasons = short_pay_reasons(invoice, settings)
    assert reasons and "Partially paid" in reasons[0]


def test_stale_list_is_caught_by_the_live_read(settings):
    """The aging-report failure mode, reproduced: list says open, invoice says paid."""
    listed = invoice_row(id="5", balance=3000)
    paid_now = invoice_row(id="5", balance=0)
    qbo = FakeQbo(invoices=[listed], live_overrides={"5": paid_now})

    verification = verify_invoice(qbo, parse_invoice(listed, 15), [], [], settings)

    assert verification.still_open is False
    assert verification.clear_to_propose is False
    assert "zero balance on a live read" in verification.reasons_to_hold[0]
    assert qbo.entity_reads == ["Invoice:5"]


def test_clean_invoice_clears_verification(settings):
    row = invoice_row(id="5", balance=3000)
    qbo = FakeQbo(invoices=[row])
    verification = verify_invoice(qbo, parse_invoice(row, 15), [], [], settings)
    assert verification.clear_to_propose is True


def test_unapplied_candidate_blocks_the_send(settings):
    row = invoice_row(id="5", balance=3000)
    qbo = FakeQbo(invoices=[row])
    payment = parse_payment(payment_row(total=3000, unapplied=3000))
    verification = verify_invoice(qbo, parse_invoice(row, 15), [payment], [], settings)
    assert verification.clear_to_propose is False
    assert verification.unapplied
