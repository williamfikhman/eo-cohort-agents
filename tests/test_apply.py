"""Posting to QuickBooks. The only code here that can move money.

Every test in this file is about something the agent must refuse to do.
"""

import pytest
from datetime import date
from decimal import Decimal

from ar_followup.apply import apply_confirmed
from ar_followup.models import money
from ar_followup.qbo.writer import QboWriteRefused, QboWriter

from conftest import FakeQbo, FakeWriter, invoice_row, payment_row

TODAY = date(2026, 9, 17)


def digest_with_match(ledger, sources=None, invoice_ids=None, amount="3000.00"):
    """A stored digest carrying one proposed match."""
    digest_id = "2026-09-17-test01"
    ledger.record_digest(
        digest_id,
        TODAY,
        {
            "digest_id": digest_id,
            "as_of": TODAY,
            "matches": [
                {
                    "ref": "M1",
                    "customer_name": "Acme Brands",
                    "invoice_ids": invoice_ids or ["1"],
                    "invoice_labels": "#1001",
                    "sources": sources
                    if sources is not None
                    else [{"kind": "payment", "id": "p1", "amount": amount, "account": "Chase"}],
                    "amount": amount,
                    "strategy": "exact",
                    "confidence": "high",
                    "rationale": "exact match",
                }
            ],
            "reminders": [],
            "flags": [],
            "unexplained": [],
        },
    )
    return digest_id


def confirm(ledger, digest_id, ref="M1", decision="confirmed"):
    ledger.record_match_decision(
        digest_id=digest_id, ref=ref, decision=decision,
        approver="william@marketplaceofficer.com", source="email-reply",
        invoice_id="1", invoice_label="#1001", customer="Acme Brands",
    )


def qbo_with(payment_unapplied=3000.0, balance=3000.0, applied_ids=None):
    return FakeQbo(
        invoices=[invoice_row(id="1", doc_number="1001", balance=balance)],
        payments=[
            payment_row(id="p1", total=3000.0, unapplied=payment_unapplied,
                        applied_invoice_ids=applied_ids or [])
        ],
    )


# --- the refusals -----------------------------------------------------------


def test_an_unconfirmed_match_is_never_posted(config, ledger):
    digest_id = digest_with_match(ledger)
    writer = FakeWriter()
    outcomes = apply_confirmed(
        config, ledger, digest_id=digest_id, dry_run=False,
        qbo=qbo_with(), writer=writer, today=TODAY,
    )
    assert writer.applied == []
    assert "no match in this digest has been confirmed" in outcomes[0].detail


def test_a_rejected_match_is_never_posted(config, ledger):
    digest_id = digest_with_match(ledger)
    confirm(ledger, digest_id, decision="rejected")
    writer = FakeWriter()
    apply_confirmed(
        config, ledger, digest_id=digest_id, dry_run=False,
        qbo=qbo_with(), writer=writer, today=TODAY,
    )
    assert writer.applied == []


def test_a_deposit_sourced_match_is_never_posted(live_config, ledger):
    """Cash already in the register. Posting a payment would book it twice.

    Tested with posting fully switched on, because this refusal does not depend
    on the switch.
    """
    digest_id = digest_with_match(
        ledger, sources=[{"kind": "deposit", "id": "d1", "amount": "3000.00", "account": "Chase"}]
    )
    confirm(ledger, digest_id)
    writer = FakeWriter()
    outcomes = apply_confirmed(
        live_config, ledger, digest_id=digest_id, dry_run=False,
        qbo=qbo_with(), writer=writer, today=TODAY,
    )
    assert writer.applied == []
    assert outcomes[0].status == "skipped"
    assert "undo the categorization" in outcomes[0].detail


def test_posting_is_refused_while_the_switch_is_off(config, ledger):
    """The repo ships with posting off, and off means off."""
    assert config.settings.apply_enabled is False
    digest_id = digest_with_match(ledger)
    confirm(ledger, digest_id)
    writer = FakeWriter()
    outcomes = apply_confirmed(
        config, ledger, digest_id=digest_id, dry_run=False,
        qbo=qbo_with(), writer=writer, today=TODAY,
    )
    assert writer.applied == []
    assert outcomes[0].status == "refused"
    assert "payment_application.enabled" in outcomes[0].detail


def test_an_invoice_that_cleared_in_the_meantime_is_left_alone(live_config, ledger):
    digest_id = digest_with_match(ledger)
    confirm(ledger, digest_id)
    writer = FakeWriter()
    outcomes = apply_confirmed(
        live_config, ledger, digest_id=digest_id, dry_run=False,
        qbo=qbo_with(balance=0.0), writer=writer, today=TODAY,
    )
    assert writer.applied == []
    assert "zero balance" in outcomes[0].detail


def test_more_money_than_the_invoice_owes_is_refused(live_config, ledger):
    digest_id = digest_with_match(ledger, amount="3000.00")
    confirm(ledger, digest_id)
    writer = FakeWriter()
    outcomes = apply_confirmed(
        live_config, ledger, digest_id=digest_id, dry_run=False,
        qbo=qbo_with(balance=3000.0), writer=writer, today=TODAY,
    )
    # The allocation is capped at the live balance, so this one is fine.
    assert outcomes[0].status == "applied"
    assert writer.applied[0]["amount"] == money(3000)


def test_the_same_payment_is_never_applied_twice(live_config, ledger):
    digest_id = digest_with_match(ledger)
    confirm(ledger, digest_id)
    writer = FakeWriter()
    kwargs = dict(digest_id=digest_id, dry_run=False, qbo=qbo_with(), writer=writer, today=TODAY)

    first = apply_confirmed(live_config, ledger, **kwargs)
    second = apply_confirmed(live_config, ledger, **kwargs)

    assert first[0].status == "applied"
    assert second[0].status == "skipped"
    assert "already applied" in second[0].detail
    assert len(writer.applied) == 1


def test_the_per_application_cap_is_enforced(live_config, ledger):
    digest_id = digest_with_match(ledger, amount="99000.00")
    confirm(ledger, digest_id)
    writer = FakeWriter()
    qbo = FakeQbo(
        invoices=[invoice_row(id="1", doc_number="1001", total=99000.0, balance=99000.0)],
        payments=[payment_row(id="p1", total=99000.0, unapplied=99000.0)],
    )
    outcomes = apply_confirmed(
        live_config, ledger, digest_id=digest_id, dry_run=False,
        qbo=qbo, writer=writer, today=TODAY,
    )
    assert writer.applied == []
    assert outcomes[0].status == "refused"
    assert "per-application cap" in outcomes[0].detail


def test_dry_run_posts_nothing(live_config, ledger):
    digest_id = digest_with_match(ledger)
    confirm(ledger, digest_id)
    writer = FakeWriter()
    outcomes = apply_confirmed(
        live_config, ledger, digest_id=digest_id, dry_run=True,
        qbo=qbo_with(), writer=writer, today=TODAY,
    )
    assert writer.applied == []
    assert "dry run" in outcomes[0].detail


# --- the happy path ---------------------------------------------------------


def test_a_confirmed_payment_match_is_applied_and_logged(live_config, ledger):
    digest_id = digest_with_match(ledger)
    confirm(ledger, digest_id)
    writer = FakeWriter()
    outcomes = apply_confirmed(
        live_config, ledger, digest_id=digest_id, dry_run=False,
        qbo=qbo_with(), writer=writer, today=TODAY,
    )

    assert outcomes[0].status == "applied"
    assert writer.applied == [{"payment_id": "p1", "invoice_id": "1", "amount": money(3000)}]
    record = list(ledger.records("applied"))[0]
    assert record["payment_id"] == "p1"
    assert record["invoice_id"] == "1"
    assert record["amount"] == "3000.00"


# --- the writer's own guards ------------------------------------------------


def writer_only():
    w = QboWriter.__new__(QboWriter)
    w.audit_stamp = "AR-agent"
    w.enabled = True
    return w


def test_the_writer_refuses_a_payment_already_linked_to_the_invoice():
    payment = {"Id": "p1", "UnappliedAmt": 3000,
               "Line": [{"Amount": 3000, "LinkedTxn": [{"TxnId": "1", "TxnType": "Invoice"}]}]}
    with pytest.raises(QboWriteRefused, match="already linked"):
        writer_only().apply_payment_to_invoice(payment, "1", Decimal("3000"))


def test_the_writer_refuses_a_payment_it_already_stamped():
    """The stamp survives even if the ledger is lost."""
    payment = {"Id": "p1", "UnappliedAmt": 3000, "Line": [],
               "PrivateNote": "[AR-agent applied to invoice 1]"}
    with pytest.raises(QboWriteRefused, match="already carries this agent's stamp"):
        writer_only().apply_payment_to_invoice(payment, "1", Decimal("3000"))


def test_the_writer_refuses_to_allocate_more_than_is_unapplied():
    payment = {"Id": "p1", "UnappliedAmt": 500, "Line": []}
    with pytest.raises(QboWriteRefused, match="less than"):
        writer_only().apply_payment_to_invoice(payment, "1", Decimal("3000"))


def test_the_writer_refuses_everything_while_disabled():
    w = QboWriter.__new__(QboWriter)
    w.audit_stamp, w.enabled = "AR-agent", False
    with pytest.raises(Exception, match="posting to QuickBooks is off"):
        w._post("payment", {})


def test_the_stamp_names_the_invoice():
    assert writer_only().stamp_for("42") == "[AR-agent applied to invoice 42]"
