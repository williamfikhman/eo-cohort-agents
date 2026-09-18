"""End-to-end: canned QuickBooks rows in, digest payload out. Nothing sent."""

import json
from datetime import date, timedelta

from ar_followup.digest import render_html, render_text, subject
from ar_followup.models import Snapshot, money
from ar_followup.pipeline import run_scan

from conftest import (
    FakeQbo,
    credit_memo_row,
    deposit_row,
    invoice_row,
    line,
    payment_row,
)

TODAY = date(2026, 9, 12)


def kinds(result):
    return {f.kind for f in result.flags}


def test_a_clean_past_due_invoice_becomes_a_proposal(config, ledger):
    qbo = FakeQbo(invoices=[invoice_row(id="1", txn_date=date(2026, 8, 20))])
    result = run_scan(qbo, config, ledger, today=TODAY)

    assert len(result.reminders) == 1
    reminder = result.reminders[0]
    assert reminder.ref == "R1"
    assert reminder.stage_id == "friendly"
    assert reminder.to == "ap@acmebrands.com"
    assert reminder.days_past_due == 8
    assert "qbo.example" in reminder.invoice.share_link


def test_firm_stage_ccs_angie(config, ledger):
    qbo = FakeQbo(invoices=[invoice_row(id="1", txn_date=date(2026, 8, 10))])
    result = run_scan(qbo, config, ledger, today=TODAY)
    assert result.reminders[0].stage_id == "firm"
    assert result.reminders[0].cc == ["angie@marketplaceofficer.com"]


def test_escalation_raises_a_call_and_sends_nothing(config, ledger):
    qbo = FakeQbo(invoices=[invoice_row(id="1", txn_date=date(2026, 7, 1))])
    result = run_scan(qbo, config, ledger, today=TODAY)
    assert result.reminders == []
    assert "escalation_call" in kinds(result)


def test_stale_open_invoice_is_caught_before_it_is_proposed(config, ledger):
    """The list says $3,000 open. The live read says paid. Nobody gets dunned."""
    qbo = FakeQbo(
        invoices=[invoice_row(id="1", txn_date=date(2026, 8, 20), balance=3000)],
        live_overrides={"1": invoice_row(id="1", txn_date=date(2026, 8, 20), balance=0)},
    )
    result = run_scan(qbo, config, ledger, today=TODAY)
    assert result.reminders == []
    assert "already_paid" in kinds(result)


def test_unapplied_chase_deposit_is_matched_not_chased(config, ledger):
    qbo = FakeQbo(
        invoices=[invoice_row(id="1", txn_date=date(2026, 8, 20))],
        deposits=[deposit_row(total=3000.0, account="Chase Business Checking")],
    )
    result = run_scan(qbo, config, ledger, today=TODAY)
    assert result.reminders == []
    assert result.matches[0].ref == "M1"
    assert result.matches[0].strategy == "exact"
    assert result.matches[0].confidence == "high"


def test_unapplied_payment_in_qbo_is_matched_not_chased(config, ledger):
    qbo = FakeQbo(
        invoices=[invoice_row(id="1", txn_date=date(2026, 8, 20))],
        payments=[payment_row(total=3000.0, unapplied=3000.0)],
    )
    result = run_scan(qbo, config, ledger, today=TODAY)
    assert result.reminders == []
    assert result.matches


def test_partial_payment_goes_to_review_not_to_a_reminder(config, ledger):
    qbo = FakeQbo(invoices=[invoice_row(id="1", txn_date=date(2026, 8, 20), balance=800)])
    result = run_scan(qbo, config, ledger, today=TODAY)
    assert result.reminders == []
    assert "short_pay_review" in kinds(result)


def test_stacked_ar_is_a_churn_flag_not_a_reminder(config, ledger):
    qbo = FakeQbo(
        invoices=[
            invoice_row(id="1", doc_number="900", txn_date=date(2026, 7, 1)),
            invoice_row(id="2", doc_number="901", txn_date=date(2026, 8, 1)),
        ]
    )
    result = run_scan(qbo, config, ledger, today=TODAY)
    assert result.reminders == []
    assert "reminders_suppressed" in kinds(result)
    assert any("churn" in f.reason for f in result.flags)


def test_open_credit_memo_suppresses_the_reminder(config, ledger):
    qbo = FakeQbo(
        invoices=[invoice_row(id="1", txn_date=date(2026, 8, 20))],
        credit_memos=[credit_memo_row(remaining=500.0)],
    )
    result = run_scan(qbo, config, ledger, today=TODAY)
    assert result.reminders == []
    assert "open_credit_balance" in kinds(result)


def test_client_in_dispute_is_never_reminded(config, ledger):
    qbo = FakeQbo(
        invoices=[
            invoice_row(id="1", customer_id="77", customer_name="Kaaral", txn_date=date(2026, 8, 1))
        ]
    )
    result = run_scan(qbo, config, ledger, today=TODAY)
    assert result.reminders == []
    assert "reminders_suppressed" in kinds(result)


def test_missing_billing_contact_is_flagged(config, ledger):
    qbo = FakeQbo(invoices=[invoice_row(id="1", txn_date=date(2026, 8, 20), email=None)])
    result = run_scan(qbo, config, ledger, today=TODAY)
    assert result.reminders == []
    assert "no_billing_contact" in kinds(result)


def test_unknown_client_is_flagged_but_still_reminded(config, ledger):
    """No contract on file is a note to William, not a reason to skip the invoice."""
    qbo = FakeQbo(invoices=[invoice_row(id="1", txn_date=date(2026, 8, 20))])
    result = run_scan(qbo, config, ledger, today=TODAY)
    assert "no_contract_terms" in kinds(result)
    assert len(result.reminders) == 1


def test_idempotency_across_runs(config, ledger):
    qbo = FakeQbo(invoices=[invoice_row(id="1", txn_date=date(2026, 8, 20))])
    first = run_scan(qbo, config, ledger, today=TODAY)
    assert len(first.reminders) == 1

    ledger.record_send(
        digest_id=first.digest_id,
        invoice_id="1",
        stage_id="friendly",
        customer="Acme Brands",
        to="ap@acmebrands.com",
        cc=[],
        subject="s",
        message_id="m1",
        approver="william@marketplaceofficer.com",
    )
    second = run_scan(qbo, config, ledger, today=TODAY)
    assert second.reminders == []


def test_no_aging_report_is_ever_queried(config, ledger):
    qbo = FakeQbo(invoices=[invoice_row(id="1", txn_date=date(2026, 8, 20))])
    run_scan(qbo, config, ledger, today=TODAY)
    assert all("report" not in q.lower() for q in qbo.queries)
    assert "Invoice Balance > '0'" in qbo.queries


def test_snapshot_and_week_over_week(config, ledger):
    ledger.record_snapshot(
        Snapshot(date(2026, 9, 5), money(40000), money(12000), 14, 5)
    )
    qbo = FakeQbo(
        invoices=[
            invoice_row(id="1", txn_date=date(2026, 8, 20)),
            invoice_row(id="2", doc_number="1002", customer_id="11", customer_name="Other Co",
                        txn_date=date(2026, 9, 10), total=1000.0),
        ]
    )
    result = run_scan(qbo, config, ledger, today=TODAY)
    assert result.snapshot.total_ar == money(4000)
    assert result.snapshot.past_due_ar == money(3000)
    assert result.prior_snapshot.past_due_ar == money(12000)
    text = render_text(result, config.settings)
    assert "Week-over-week past due: down $9,000.00" in text


def test_billing_error_holds_the_reminder(config, ledger, tmp_path):
    """An invoice that disagrees with its contract is never chased."""
    clients_yaml = """
clients:
  - name: Acme Brands
    qbo_customer_id: "10"
    billing_contact: ap@acmebrands.com
    terms:
      - effective_from: 2026-01-01
        effective_to: 2026-04-30
        flat_fee_monthly: 3000
        commission_pct: 8
      - effective_from: 2026-05-01
        effective_to: null
        flat_fee_monthly: 0
        commission_pct: 10
"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "clients.yaml").write_text(clients_yaml)
    (config_dir / "settings.yaml").write_text(
        (config.config_dir / "settings.yaml").read_text()
    )

    from ar_followup.config import load_config

    scoped = load_config(config_dir)
    qbo = FakeQbo(
        invoices=[
            invoice_row(
                id="1",
                txn_date=date(2026, 8, 20),
                total=4200.0,
                lines=[
                    line(3000, "Monthly retainer", item="Retainer"),
                    line(1200, "Commission at 10% of net sales", item="Commission"),
                ],
            )
        ]
    )
    result = run_scan(qbo, scoped, ledger, today=TODAY)
    assert result.reminders == []
    assert "flat_fee_after_step_down" in kinds(result)
    assert "reminder_held_billing_error" in kinds(result)


def test_digest_renders_in_both_formats(config, ledger):
    qbo = FakeQbo(
        invoices=[
            invoice_row(id="1", txn_date=date(2026, 8, 20)),
            invoice_row(id="2", doc_number="1002", customer_id="11", customer_name="Silko",
                        txn_date=date(2026, 6, 1), total=5400.0),
        ],
        deposits=[deposit_row(id="d9", total=5400.0, account="Chase Business Checking")],
    )
    result = run_scan(qbo, config, ledger, today=TODAY)
    text = render_text(result, config.settings)
    html = render_html(result, config.settings)

    assert "1. MATCH THESE FIRST" in text
    assert "2. REMINDERS QUEUED FOR APPROVAL" in text
    assert "3. FLAGGED" in text
    assert "4. MONEY WITH NO HOME" in text
    assert "5. WHERE AR STANDS" in text
    assert "APPROVE ALL" in text
    assert "CONFIRM M1 M2" in text
    # Matching comes before reminders, so an approval can never dun a payer.
    assert text.index("1. MATCH THESE FIRST") < text.index("2. REMINDERS")
    assert "<h1" in html and "Match these first" in html
    assert "AR digest" in subject(result, config.settings)


def test_one_bad_client_does_not_kill_the_digest(config, ledger, monkeypatch):
    qbo = FakeQbo(
        invoices=[
            invoice_row(id="1", customer_id="10", txn_date=date(2026, 8, 20)),
            invoice_row(id="2", customer_id="11", customer_name="Boom Co", txn_date=date(2026, 8, 20)),
        ]
    )
    real_verify = __import__("ar_followup.pipeline", fromlist=["verify_invoice"]).verify_invoice

    def exploding(qbo, invoice, *args, **kwargs):
        if invoice.customer_id == "11":
            raise RuntimeError("QBO hiccup")
        return real_verify(qbo, invoice, *args, **kwargs)

    monkeypatch.setattr("ar_followup.pipeline.verify_invoice", exploding)
    result = run_scan(qbo, config, ledger, today=TODAY)

    assert len(result.reminders) == 1
    assert any("QBO hiccup" in e for e in result.errors)


# --- reconcile first, then report ------------------------------------------


def test_a_split_payment_is_reconciled_before_any_reminder(config, ledger):
    """End to end on the Jatai shape: two transfers, one invoice, no dunning."""
    qbo = FakeQbo(
        invoices=[invoice_row(id="1", doc_number="1883", txn_date=date(2026, 8, 1), total=9765.14)],
        deposits=[
            deposit_row(id="d1", txn_date=date(2026, 9, 8), total=5000.00),
            deposit_row(id="d2", txn_date=date(2026, 9, 11), total=4765.14),
        ],
    )
    result = run_scan(qbo, config, ledger, today=TODAY)

    assert result.reminders == []
    assert len(result.matches) == 1
    assert result.matches[0].strategy == "split"
    # And AR says the client owes nothing, because the client owes nothing.
    assert result.snapshot.total_ar == money("9765.14")
    assert result.snapshot.pending_application == money("9765.14")
    assert result.snapshot.net_ar == money(0)
    assert result.snapshot.past_due_ar == money(0)


def test_ar_separates_gross_from_what_is_really_owed(config, ledger):
    qbo = FakeQbo(
        invoices=[
            invoice_row(id="1", doc_number="900", customer_id="10", customer_name="Paid Co",
                        txn_date=date(2026, 8, 20), total=3000.0),
            invoice_row(id="2", doc_number="901", customer_id="11", customer_name="Owes Co",
                        txn_date=date(2026, 8, 20), total=1000.0),
        ],
        deposits=[deposit_row(id="d1", total=3000.0, customer_names=["Paid Co"])],
    )
    result = run_scan(qbo, config, ledger, today=TODAY)
    snapshot = result.snapshot

    assert snapshot.total_ar == money(4000)
    assert snapshot.pending_application == money(3000)
    assert snapshot.net_ar == money(1000)
    assert snapshot.past_due_count == 1
    assert [r.invoice.customer_name for r in result.reminders] == ["Owes Co"]


def test_money_booked_to_income_is_flagged_as_counted_twice(config, ledger):
    qbo = FakeQbo(
        invoices=[invoice_row(id="1", txn_date=date(2026, 8, 20), total=3000.0)],
        deposits=[
            deposit_row(id="d1", total=3000.0, line_accounts=["Consulting Income"]),
        ],
    )
    result = run_scan(qbo, config, ledger, today=TODAY)
    assert "double_counted_income" in kinds(result)
    flag = next(f for f in result.flags if f.kind == "double_counted_income")
    assert flag.severity == "urgent"
    assert flag.amount == money(3000)


def test_money_matching_nothing_is_reported_not_buried(config, ledger):
    qbo = FakeQbo(
        invoices=[invoice_row(id="1", txn_date=date(2026, 8, 20), total=3000.0)],
        deposits=[
            deposit_row(id="d1", total=3000.0),
            deposit_row(id="d2", total=812.55),
        ],
    )
    result = run_scan(qbo, config, ledger, today=TODAY)
    assert [u.source.amount for u in result.unexplained] == [money("812.55")]
    assert result.unexplained[0].ref == "U1"


def _write_match_record(ledger, when: date, **fields) -> None:
    """Append a match record stamped on a chosen day, to age a confirmation."""
    record = {
        "ts": f"{when.isoformat()}T08:00:00+00:00",
        "type": "match",
        "digest_id": "d-old",
        "ref": "M1",
        "decision": "confirmed",
        "approver": "william@marketplaceofficer.com",
        "source": "email-reply",
        **fields,
    }
    with ledger.path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def test_a_confirmed_match_nobody_applied_gets_chased(config, ledger):
    """Confirming is a promise to go apply it. A promise nobody kept is a bug."""
    _write_match_record(
        ledger, TODAY - timedelta(days=5),
        invoice_id="1", invoice_label="#1001", customer="Acme Brands",
    )
    qbo = FakeQbo(invoices=[invoice_row(id="1", txn_date=date(2026, 8, 20))])
    result = run_scan(qbo, config, ledger, today=TODAY)

    flag = next(f for f in result.flags if f.kind == "confirmed_match_not_applied")
    assert flag.severity == "urgent"
    assert "#1001" in flag.reason


def test_a_confirmation_from_yesterday_is_given_time(config, ledger):
    _write_match_record(
        ledger, TODAY - timedelta(days=1),
        invoice_id="1", invoice_label="#1001", customer="Acme Brands",
    )
    qbo = FakeQbo(invoices=[invoice_row(id="1", txn_date=date(2026, 8, 20))])
    result = run_scan(qbo, config, ledger, today=TODAY)
    assert "confirmed_match_not_applied" not in kinds(result)


def test_an_applied_match_is_not_chased(config, ledger):
    """The invoice cleared, so it is no longer in the open set. Nothing to chase."""
    _write_match_record(
        ledger, TODAY - timedelta(days=10),
        invoice_id="999", invoice_label="#999", customer="Gone Co",
    )
    qbo = FakeQbo(invoices=[invoice_row(id="1", txn_date=date(2026, 8, 20))])
    result = run_scan(qbo, config, ledger, today=TODAY)
    assert "confirmed_match_not_applied" not in kinds(result)


def test_a_rejected_match_is_not_chased(config, ledger):
    _write_match_record(
        ledger, TODAY - timedelta(days=10), decision="rejected",
        invoice_id="1", invoice_label="#1001", customer="Acme Brands",
    )
    qbo = FakeQbo(invoices=[invoice_row(id="1", txn_date=date(2026, 8, 20))])
    result = run_scan(qbo, config, ledger, today=TODAY)
    assert "confirmed_match_not_applied" not in kinds(result)
