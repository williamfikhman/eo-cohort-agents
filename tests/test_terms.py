from datetime import date
from decimal import Decimal

from ar_followup.config import ClientTerms, CreditBalance, TermPeriod
from ar_followup.models import Invoice, InvoiceLine, money
from ar_followup.terms import (
    credit_drawdown_findings,
    implied_commission_pct,
    overbilling_estimate,
    upcoming_contract_changes,
    validate_invoice,
)

TODAY = date(2026, 9, 12)


def stepped_down_client(**overrides) -> ClientTerms:
    """$3,000/mo + 8% until April 30, then 10% commission only. The real case."""
    defaults = dict(
        name="Example Brand",
        qbo_customer_id="10",
        aliases=[],
        billing_contact="ap@example.com",
        payment_terms_days=None,
        currency=None,
        fx_rate=None,
        terms=[
            TermPeriod(date(2026, 1, 1), date(2026, 4, 30), money(3000), money(8), "net sales", ""),
            TermPeriod(date(2026, 5, 1), None, money(0), money(10), "net sales", ""),
        ],
        sunset_date=None,
        step_down_dates=[date(2026, 5, 1)],
        expected_total_range=None,
        reminders_suppressed=False,
        suppression_reason="",
        credit_balance=None,
        flags=[],
    )
    defaults.update(overrides)
    return ClientTerms(**defaults)


def invoice(lines, txn_date=date(2026, 9, 1), total=None, **kwargs):
    total = total if total is not None else sum(l.amount for l in lines)
    return Invoice(
        id=kwargs.pop("id", "1"),
        doc_number=kwargs.pop("doc_number", "1042"),
        customer_id="10",
        customer_name="Example Brand",
        txn_date=txn_date,
        due_date=kwargs.pop("due_date", date(2026, 9, 16)),
        total=money(total),
        balance=money(kwargs.pop("balance", total)),
        lines=lines,
        **kwargs,
    )


def fee_line(amount=3000):
    return InvoiceLine("Monthly retainer", "Retainer", money(amount))


def commission_line(amount=1200, pct="8"):
    return InvoiceLine(f"Commission at {pct}% of net sales", "Commission", money(amount))


def test_flat_fee_after_step_down_is_urgent(settings):
    """The $7,328 case: a flat fee billed under a commission-only contract."""
    inv = invoice([fee_line(3000), commission_line(1200, "10")])
    findings = validate_invoice(inv, stepped_down_client(), settings)
    kinds = {f.kind: f for f in findings}
    assert "flat_fee_after_step_down" in kinds
    assert kinds["flat_fee_after_step_down"].severity == "urgent"
    assert kinds["flat_fee_after_step_down"].amount == money(3000)


def test_commission_rate_mismatch_is_urgent(settings):
    inv = invoice([commission_line(1200, "8")])
    findings = validate_invoice(inv, stepped_down_client(), settings)
    assert any(f.kind == "commission_rate_mismatch" and f.severity == "urgent" for f in findings)


def test_invoice_matching_current_terms_is_clean(settings):
    inv = invoice([commission_line(1500, "10")])
    assert validate_invoice(inv, stepped_down_client(), settings) == []


def test_invoice_under_the_old_period_is_judged_on_the_old_terms(settings):
    inv = invoice([fee_line(3000), commission_line(1200, "8")], txn_date=date(2026, 3, 1))
    assert validate_invoice(inv, stepped_down_client(), settings) == []


def test_no_client_entry_is_flagged_not_silently_reminded(settings):
    findings = validate_invoice(invoice([fee_line()]), None, settings)
    assert [f.kind for f in findings] == ["no_contract_terms"]


def test_invoice_outside_every_contract_period(settings):
    client = stepped_down_client(
        terms=[TermPeriod(date(2026, 1, 1), date(2026, 4, 30), money(3000), money(8), "", "")],
        step_down_dates=[],
    )
    findings = validate_invoice(invoice([fee_line()]), client, settings)
    assert findings[0].kind == "terms_gap"


def test_unpriced_terms_are_not_validated(settings):
    client = stepped_down_client(
        terms=[TermPeriod(date(2026, 1, 1), None, None, None, "", "not captured")],
        step_down_dates=[],
    )
    findings = validate_invoice(invoice([fee_line()]), client, settings)
    assert findings[0].kind == "terms_unverified"


def test_currency_and_fx_mismatches(settings):
    client = stepped_down_client(currency="USD", fx_rate=Decimal("1.35"))
    inv = invoice([commission_line(1500, "10")])
    inv.currency = "CAD"
    inv.exchange_rate = Decimal("1.41")
    kinds = {f.kind for f in validate_invoice(inv, client, settings)}
    assert {"currency_mismatch", "fx_rate_mismatch"} <= kinds


def test_total_outside_the_expected_band(settings):
    client = stepped_down_client(expected_total_range=(money(500), money(2000)))
    findings = validate_invoice(invoice([commission_line(9000, "10")]), client, settings)
    assert any(f.kind == "total_out_of_band" for f in findings)


def test_overbilling_adds_up_across_months(settings):
    client = stepped_down_client()
    invoices = [
        invoice([fee_line(3000), commission_line(500, "10")], txn_date=date(2026, m, 1), id=str(m))
        for m in (6, 7, 8, 9)
    ]
    assert overbilling_estimate(invoices, client, settings) == money(12000)


def test_overbilling_ignores_months_the_fee_was_contracted(settings):
    client = stepped_down_client()
    invoices = [invoice([fee_line(3000)], txn_date=date(2026, 3, 1))]
    assert overbilling_estimate(invoices, client, settings) == money(0)


def test_implied_commission_pct_from_description():
    assert implied_commission_pct(InvoiceLine("Commission at 8% of net sales", "", money(1))) == money(8)


def test_implied_commission_pct_from_a_fractional_rate():
    line = InvoiceLine("Commission", "Commission", money(800), qty=Decimal("10000"), rate=Decimal("0.08"))
    assert implied_commission_pct(line) == money(8)


def test_implied_commission_pct_from_amount_over_base():
    line = InvoiceLine("Commission", "Commission", money(800), qty=Decimal("10000"))
    assert implied_commission_pct(line) == money(8)


def test_credit_must_draw_down(settings):
    client = stepped_down_client(
        credit_balance=CreditBalance(money("6026.67"), date(2026, 9, 1), "carried forward")
    )
    findings = credit_drawdown_findings([invoice([commission_line(1500, "10")])], client)
    assert findings[0].kind == "credit_not_drawing_down"
    assert findings[0].severity == "urgent"


def test_credit_applied_raises_nothing(settings):
    client = stepped_down_client(
        credit_balance=CreditBalance(money("6026.67"), date(2026, 9, 1), "")
    )
    inv = invoice([commission_line(1500, "10")])
    inv.linked_txns = [("CreditMemo", "cm1")]
    assert credit_drawdown_findings([inv], client) == []


def test_upcoming_step_down_and_sunset_are_alerted():
    client = stepped_down_client(
        step_down_dates=[date(2026, 10, 1)], sunset_date=date(2026, 9, 30)
    )
    upcoming = upcoming_contract_changes([client], TODAY, 30)
    assert {kind for _, kind, _ in upcoming} == {"rate step-down", "contract sunset"}


def test_contract_change_beyond_the_window_is_quiet():
    client = stepped_down_client(step_down_dates=[date(2026, 12, 1)])
    assert upcoming_contract_changes([client], TODAY, 30) == []
