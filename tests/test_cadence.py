from datetime import date, timedelta

from ar_followup.cadence import ESCALATE, NONE, PROPOSE, SUPPRESS, decide, stacked_ar, stage_for
from ar_followup.models import Invoice, money

TODAY = date(2026, 9, 12)


def invoice(days_past_due: int, id="1", balance=3000, txn_date=None, customer_id="10"):
    due = TODAY - timedelta(days=days_past_due)
    return Invoice(
        id=id,
        doc_number=f"INV{id}",
        customer_id=customer_id,
        customer_name="Acme Brands",
        txn_date=txn_date or (due - timedelta(days=15)),
        due_date=due,
        total=money(balance),
        balance=money(balance),
    )


def test_stage_boundaries(settings):
    assert stage_for(2, settings) is None
    assert stage_for(3, settings).id == "friendly"
    assert stage_for(9, settings).id == "friendly"
    assert stage_for(10, settings).id == "firm"
    assert stage_for(20, settings).id == "firm"
    assert stage_for(21, settings).id == "escalation"
    assert stage_for(400, settings).id == "escalation"


def test_not_yet_due_proposes_nothing(settings):
    inv = invoice(days_past_due=1)
    assert decide(inv, None, settings, TODAY, set(), [inv]).action == NONE


def test_friendly_reminder_is_proposed(settings):
    inv = invoice(days_past_due=4)
    decision = decide(inv, None, settings, TODAY, set(), [inv])
    assert decision.action == PROPOSE
    assert decision.stage.id == "friendly"


def test_firm_stage_ccs_angie(settings):
    inv = invoice(days_past_due=11)
    decision = decide(inv, None, settings, TODAY, {"friendly"}, [inv])
    assert decision.stage.id == "firm"
    assert decision.stage.cc == ["angie@marketplaceofficer.com"]


def test_escalation_never_emails(settings):
    inv = invoice(days_past_due=25)
    decision = decide(inv, None, settings, TODAY, {"friendly", "firm"}, [inv])
    assert decision.action == ESCALATE
    assert decision.stage.sends_email is False


def test_a_stage_is_never_sent_twice(settings):
    inv = invoice(days_past_due=5)
    assert decide(inv, None, settings, TODAY, {"friendly"}, [inv]).action == NONE


def test_after_escalation_automation_stays_off(settings):
    """Even an unsent earlier stage does not restart email once escalated."""
    inv = invoice(days_past_due=30)
    decision = decide(inv, None, settings, TODAY, {"escalation"}, [inv])
    assert decision.action == NONE


def test_stacked_ar_skips_automation(settings):
    """Two open invoices in different months: churn risk, not a dunning case."""
    august = invoice(days_past_due=40, id="1", txn_date=date(2026, 8, 1))
    september = invoice(days_past_due=5, id="2", txn_date=date(2026, 9, 1))
    decision = decide(august, None, settings, TODAY, set(), [august, september])
    assert decision.action == SUPPRESS
    assert "churn" in decision.reason


def test_two_invoices_in_one_month_are_not_stacked_ar(settings):
    first = invoice(days_past_due=5, id="1", txn_date=date(2026, 9, 1))
    second = invoice(days_past_due=4, id="2", txn_date=date(2026, 9, 2))
    assert stacked_ar([first, second], settings) is False
    assert decide(first, None, settings, TODAY, set(), [first, second]).action == PROPOSE


def test_disputed_client_is_suppressed(config, settings):
    kaaral = config.find_client("999", "Kaaral")
    inv = invoice(days_past_due=30)
    decision = decide(inv, kaaral, settings, TODAY, set(), [inv])
    assert decision.action == SUPPRESS
    assert "renegotiation" in decision.reason.lower() or "dispute" in decision.reason.lower()


def test_credit_balance_suppresses_reminders(config, settings):
    rust_check = config.find_client("998", "Rust Check")
    inv = invoice(days_past_due=30)
    decision = decide(inv, rust_check, settings, TODAY, set(), [inv])
    assert decision.action == SUPPRESS


def test_dust_balance_is_ignored(settings):
    inv = invoice(days_past_due=30, balance=2)
    assert decide(inv, None, settings, TODAY, set(), [inv]).action == NONE
