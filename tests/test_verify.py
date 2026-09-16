"""Verification: the live re-read, and the matcher's last look before a send."""

from datetime import date

from ar_followup.qbo.reads import parse_invoice
from ar_followup.verify import money_for_invoice, short_pay_reasons, verify_invoice

from conftest import FakeQbo, build_sources, deposit_row, invoice_row, payment_row

TODAY = date(2026, 9, 16)


def inv(**kwargs):
    return parse_invoice(invoice_row(**kwargs), 15)


def test_stale_list_is_caught_by_the_live_read(settings):
    """The aging-report failure mode: list says open, invoice says paid."""
    listed = invoice_row(id="5", balance=3000)
    paid_now = invoice_row(id="5", balance=0)
    qbo = FakeQbo(invoices=[listed], live_overrides={"5": paid_now})

    verification = verify_invoice(qbo, parse_invoice(listed, 15), [], settings)

    assert verification.still_open is False
    assert verification.clear_to_propose is False
    assert "zero balance on a live read" in verification.reasons_to_hold[0]
    assert qbo.entity_reads == ["Invoice:5"]


def test_a_clean_invoice_clears_verification(settings):
    row = invoice_row(id="5", balance=3000)
    qbo = FakeQbo(invoices=[row])
    assert verify_invoice(qbo, parse_invoice(row, 15), [], settings).clear_to_propose is True


def test_an_unapplied_payment_blocks_the_send(settings):
    row = invoice_row(id="5", balance=3000)
    qbo = FakeQbo(invoices=[row])
    sources = build_sources(settings, payments=[payment_row(total=3000.0, unapplied=3000.0)])
    verification = verify_invoice(qbo, parse_invoice(row, 15), sources, settings)
    assert verification.clear_to_propose is False
    assert verification.matches


def test_a_split_payment_blocks_the_send(settings):
    """The gap that would have dunned Jatai on the day the second transfer landed."""
    row = invoice_row(id="5", balance=9765.14, total=9765.14)
    qbo = FakeQbo(invoices=[row])
    sources = build_sources(
        settings,
        deposits=[
            deposit_row(id="d1", total=5000.00),
            deposit_row(id="d2", total=4765.14),
        ],
    )
    verification = verify_invoice(qbo, parse_invoice(row, 15), sources, settings)
    assert verification.clear_to_propose is False
    assert verification.matches[0].strategy == "split"


def test_another_customers_money_does_not_block_the_send(settings):
    row = invoice_row(id="5", balance=3000, customer_id="10")
    qbo = FakeQbo(invoices=[row])
    sources = build_sources(
        settings,
        payments=[payment_row(customer_id="99", customer_name="Other Co", total=3000.0, unapplied=3000.0)],
    )
    assert verify_invoice(qbo, parse_invoice(row, 15), sources, settings).clear_to_propose is True


def test_partial_payment_goes_to_review(settings):
    reasons = short_pay_reasons(inv(total=3000, balance=1200), settings)
    assert reasons and "Partially paid" in reasons[0]


def test_money_for_invoice_returns_only_matches_touching_it(settings):
    invoice = inv(id="5", balance=3000)
    sources = build_sources(settings, deposits=[deposit_row(id="d1", total=3000.0)])
    assert len(money_for_invoice(invoice, sources, settings)) == 1
