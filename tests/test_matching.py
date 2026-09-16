"""The reconciliation engine, including the case that started all this.

Jatai paid a $9,765.14 invoice in two transfers. Neither transfer equals the
invoice, so QuickBooks matched nothing and the invoice sat open at its full
balance while the cash sat in Chase.
"""

from datetime import date

from ar_followup.matching import (
    RefCounter,
    match_customer,
    pending_by_invoice,
    reconcile,
    sources_for_customer,
)
from ar_followup.models import money
from ar_followup.qbo.reads import parse_invoice

from conftest import build_sources, credit_memo_row, deposit_row, invoice_row, payment_row

TODAY = date(2026, 9, 16)


def inv(**kwargs):
    return parse_invoice(invoice_row(**kwargs), 15)


def run(invoices, sources, settings):
    return match_customer(invoices, sources, settings, RefCounter("M"))


# --- the split payment ------------------------------------------------------


def test_two_deposits_adding_up_to_one_invoice(settings):
    """The Jatai case. Neither deposit matches; together they are exact."""
    invoice = inv(id="1", doc_number="1883", total=9765.14)
    sources = build_sources(
        settings,
        deposits=[
            deposit_row(id="d1", txn_date=date(2026, 9, 8), total=5000.00),
            deposit_row(id="d2", txn_date=date(2026, 9, 11), total=4765.14),
        ],
    )
    matches, leftover = run([invoice], sources, settings)

    assert len(matches) == 1
    match = matches[0]
    assert match.strategy == "split"
    assert match.confidence == "high"
    assert match.amount == money("9765.14")
    assert len(match.sources) == 2
    assert match.fully_covers
    assert leftover == []


def test_a_single_deposit_that_falls_short_is_not_a_split(settings):
    invoice = inv(id="1", total=9765.14)
    sources = build_sources(settings, deposits=[deposit_row(id="d1", total=5000.00)])
    matches, leftover = run([invoice], sources, settings)
    assert matches == []
    assert len(leftover) == 1


def test_three_payments_adding_up(settings):
    invoice = inv(id="1", total=3000.00)
    sources = build_sources(
        settings,
        deposits=[
            deposit_row(id="d1", total=1000.00),
            deposit_row(id="d2", total=1200.00),
            deposit_row(id="d3", total=800.00),
        ],
    )
    matches, _ = run([invoice], sources, settings)
    assert len(matches) == 1
    assert len(matches[0].sources) == 3


def test_a_split_in_more_pieces_than_configured_is_left_for_a_human(settings):
    """Four-way splits are a reconciliation job, not a guess."""
    invoice = inv(id="1", total=4000.00)
    sources = build_sources(
        settings,
        deposits=[deposit_row(id=f"d{i}", total=1000.00) for i in range(1, 5)],
    )
    matches, leftover = run([invoice], sources, settings)
    assert matches == []
    assert len(leftover) == 4


# --- the other shapes -------------------------------------------------------


def test_one_payment_exactly_matching_one_invoice(settings):
    invoice = inv(id="1", total=3084.20)
    sources = build_sources(settings, payments=[payment_row(total=3084.20, unapplied=3084.20)])
    matches, _ = run([invoice], sources, settings)
    assert matches[0].strategy == "exact"
    assert matches[0].confidence == "high"


def test_a_payment_naming_the_invoice_wins_first(settings):
    invoice = inv(id="1", doc_number="1042", total=3000.00)
    sources = build_sources(
        settings,
        payments=[payment_row(total=3000.00, unapplied=3000.00, reference="1042")],
    )
    matches, _ = run([invoice], sources, settings)
    assert matches[0].strategy == "referenced"


def test_one_payment_clearing_two_invoices(settings):
    first = inv(id="1", doc_number="900", total=2000.00)
    second = inv(id="2", doc_number="901", total=1500.00)
    sources = build_sources(settings, payments=[payment_row(total=3500.00, unapplied=3500.00)])
    matches, _ = run([first, second], sources, settings)
    assert len(matches) == 1
    assert matches[0].strategy == "lump"
    assert len(matches[0].invoices) == 2


def test_a_near_miss_is_medium_confidence_not_high(settings):
    invoice = inv(id="1", total=3000.00)
    sources = build_sources(settings, deposits=[deposit_row(id="d1", total=2985.00)])
    matches, _ = run([invoice], sources, settings)
    assert matches[0].strategy == "near"
    assert matches[0].confidence == "medium"


def test_money_too_far_off_matches_nothing(settings):
    invoice = inv(id="1", total=3000.00)
    sources = build_sources(settings, deposits=[deposit_row(id="d1", total=1500.00)])
    matches, _ = run([invoice], sources, settings)
    assert matches == []


def test_a_credit_memo_is_money_like_any_other(settings):
    invoice = inv(id="1", total=500.00)
    sources = build_sources(settings, memos=[credit_memo_row(total=500.0, remaining=500.0)])
    matches, _ = run([invoice], sources, settings)
    assert matches[0].sources[0].kind == "credit_memo"


# --- not spending the same dollar twice -------------------------------------


def test_one_deposit_cannot_settle_two_invoices(settings):
    first = inv(id="1", doc_number="900", total=3000.00)
    second = inv(id="2", doc_number="901", total=3000.00)
    sources = build_sources(settings, deposits=[deposit_row(id="d1", total=3000.00)])
    matches, _ = run([first, second], sources, settings)
    assert len(matches) == 1
    assert len(matches[0].invoices) == 1


def test_a_deposit_already_linked_to_a_payment_is_not_loose(settings):
    """A correctly matched deposit is money QBO has already put through AR."""
    invoice = inv(id="1", total=3000.00)
    sources = build_sources(
        settings, deposits=[deposit_row(id="d1", total=3000.00, linked_to_payment=True)]
    )
    assert sources == []
    matches, _ = run([invoice], sources, settings)
    assert matches == []


def test_a_payment_already_applied_is_not_loose(settings):
    sources = build_sources(
        settings, payments=[payment_row(total=3000.00, unapplied=0.0, applied_invoice_ids=["1"])]
    )
    assert sources == []


def test_a_payment_applied_to_nothing_is_surfaced_anyway(settings):
    """QBO says nothing unapplied, yet the payment is on no invoice. Surface it."""
    sources = build_sources(
        settings, payments=[payment_row(total=3000.00, unapplied=0.0, applied_invoice_ids=[])]
    )
    assert len(sources) == 1
    assert sources[0].amount == money(3000)


# --- whose money is it ------------------------------------------------------


def test_another_customers_payment_is_never_offered(settings):
    sources = build_sources(
        settings,
        payments=[payment_row(customer_id="99", customer_name="Other Co", total=3000.00, unapplied=3000.00)],
    )
    mine = sources_for_customer(sources, "10", "Acme Brands")
    assert mine == []


def test_an_unattributed_deposit_stays_in_the_pool(settings):
    """A bank line with no customer on it is decided by amount, not by name."""
    sources = build_sources(settings, deposits=[deposit_row(id="d1", total=3000.00)])
    assert len(sources_for_customer(sources, "10", "Acme Brands")) == 1


def test_a_deposit_naming_another_customer_is_excluded(settings):
    sources = build_sources(
        settings, deposits=[deposit_row(id="d1", total=3000.00, customer_names=["Someone Else"])]
    )
    assert sources_for_customer(sources, "10", "Acme Brands") == []


# --- the double count -------------------------------------------------------


def test_a_deposit_booked_to_income_is_marked(settings):
    """Revenue on the deposit and AR on the invoice: the same dollars twice."""
    invoice = inv(id="1", total=3000.00)
    sources = build_sources(
        settings,
        deposits=[deposit_row(id="d1", total=3000.00, line_accounts=["Consulting Income"])],
    )
    assert sources[0].posted_to_income is True
    matches, _ = run([invoice], sources, settings)
    assert matches[0].double_counted == money(3000)


def test_a_deposit_to_undeposited_funds_is_not_an_income_posting(settings):
    sources = build_sources(
        settings, deposits=[deposit_row(id="d1", total=3000.00, line_accounts=["Undeposited Funds"])]
    )
    assert sources[0].posted_to_income is False


# --- the whole pass ---------------------------------------------------------


def test_reconcile_across_customers(settings):
    jatai = inv(id="1", doc_number="1883", customer_id="10", customer_name="JATAI", total=9765.14)
    viderm = inv(id="2", doc_number="1853", customer_id="11", customer_name="VI DERM", total=3084.20)
    sources = build_sources(
        settings,
        payments=[
            payment_row(id="p1", customer_id="11", customer_name="VI DERM",
                        total=3084.20, unapplied=3084.20)
        ],
        deposits=[
            deposit_row(id="d1", total=5000.00, customer_names=["JATAI"]),
            deposit_row(id="d2", total=4765.14, customer_names=["JATAI"]),
            deposit_row(id="d9", total=777.00),
        ],
    )
    matches, unexplained = reconcile(
        {"10": [jatai], "11": [viderm]}, sources, settings, TODAY
    )

    by_customer = {m.customer_name: m for m in matches}
    assert by_customer["JATAI"].strategy == "split"
    assert by_customer["VI DERM"].strategy == "exact"
    assert [u.source.amount for u in unexplained] == [money(777)]
    assert matches[0].ref == "M1" and matches[1].ref == "M2"


def test_loose_change_is_not_reported_as_unexplained(settings):
    sources = build_sources(settings, deposits=[deposit_row(id="d1", total=4.00)])
    _, unexplained = reconcile({"10": [inv(id="1", total=3000.0)]}, sources, settings, TODAY)
    assert unexplained == []


def test_pending_is_allocated_across_the_invoices_a_lump_covers(settings):
    first = inv(id="1", doc_number="900", total=2000.00)
    second = inv(id="2", doc_number="901", total=1500.00)
    sources = build_sources(settings, payments=[payment_row(total=3500.00, unapplied=3500.00)])
    matches, _ = run([first, second], sources, settings)
    pending = pending_by_invoice(matches)
    assert pending == {"1": money(2000), "2": money(1500)}


def test_pending_never_exceeds_the_money_that_arrived(settings):
    invoice = inv(id="1", total=3000.00)
    sources = build_sources(settings, deposits=[deposit_row(id="d1", total=2985.00)])
    matches, _ = run([invoice], sources, settings)
    assert pending_by_invoice(matches)["1"] == money("2985.00")
