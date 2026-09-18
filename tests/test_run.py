"""The approval gate. Nothing reaches a client that William did not name."""

import json
from datetime import date, datetime, timedelta, timezone

from ar_followup.gmail import GmailMessage
from ar_followup.run import scan, send_approved

from conftest import FakeGmail, FakeQbo, deposit_row, invoice_row

TODAY = date(2026, 9, 12)  # a Saturday is not a sending day; this is a Saturday
SENDING_DAY = date(2026, 9, 14)  # Monday
NOW = datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc)


def reply(body, sender="William <william@marketplaceofficer.com>"):
    return GmailMessage(id="r1", thread_id="thread-1", sender=sender, subject="Re: AR digest",
                        body=body, received="")


def scanned(config, ledger, gmail=None, invoices=None, **qbo_kwargs):
    qbo = FakeQbo(invoices=invoices or [invoice_row(id="1", txn_date=date(2026, 8, 20))], **qbo_kwargs)
    gmail = gmail or FakeGmail()
    payload = scan(
        config, ledger, today=SENDING_DAY, qbo=qbo, gmail=gmail, now=NOW - timedelta(hours=1)
    )
    return qbo, gmail, payload


def test_scan_emails_only_william(config, ledger):
    _, gmail, payload = scanned(config, ledger)
    assert len(gmail.sent) == 1
    assert gmail.sent[0]["to"] == "william@marketplaceofficer.com"
    assert payload["thread_id"] == "thread-1"
    assert payload["reminders"][0]["ref"] == "R1"


def test_scan_persists_the_digest_for_later_approval(config, ledger):
    _, _, payload = scanned(config, ledger)
    stored = ledger.load_digest(payload["digest_id"])
    assert stored["reminders"][0]["invoice_id"] == "1"
    assert stored["reminders"][0]["balance_at_proposal"] == "3000.00"


def test_nothing_sends_without_an_approval(config, ledger):
    qbo, gmail, payload = scanned(config, ledger, gmail=FakeGmail(thread_messages=[]))
    outcomes = send_approved(
        config, ledger, digest_id=payload["digest_id"], qbo=qbo, gmail=gmail,
        today=SENDING_DAY, now=NOW,
    )
    assert len(gmail.sent) == 1  # the digest, and nothing else
    assert outcomes[-1].status == "skipped"
    assert "no approval" in outcomes[-1].detail


def test_an_approved_reminder_is_sent(config, ledger):
    gmail = FakeGmail(thread_messages=[reply("approve R1")])
    qbo, gmail, payload = scanned(config, ledger, gmail=gmail)
    outcomes = send_approved(
        config, ledger, digest_id=payload["digest_id"], qbo=qbo, gmail=gmail,
        today=SENDING_DAY, now=NOW,
    )
    assert [o.status for o in outcomes] == ["sent"]
    client_email = gmail.sent[-1]
    assert client_email["to"] == "ap@acmebrands.com"
    assert "Chief Marketplace Officer" in client_email["text"]
    assert client_email["sender"].startswith("Chief Marketplace Officer Billing")


def test_a_held_ref_is_not_sent(config, ledger):
    gmail = FakeGmail(thread_messages=[reply("hold R1")])
    qbo, gmail, payload = scanned(config, ledger, gmail=gmail)
    outcomes = send_approved(
        config, ledger, digest_id=payload["digest_id"], qbo=qbo, gmail=gmail,
        today=SENDING_DAY, now=NOW,
    )
    assert outcomes[0].status == "skipped"
    assert len(gmail.sent) == 1


def test_an_approval_from_someone_else_does_not_count(config, ledger):
    gmail = FakeGmail(thread_messages=[reply("approve all", sender="angie@marketplaceofficer.com")])
    qbo, gmail, payload = scanned(config, ledger, gmail=gmail)
    outcomes = send_approved(
        config, ledger, digest_id=payload["digest_id"], qbo=qbo, gmail=gmail,
        today=SENDING_DAY, now=NOW,
    )
    assert all(o.status != "sent" for o in outcomes)


def test_payment_arriving_after_approval_still_wins(config, ledger):
    """Approved at 8am, paid at 9am. The 10am send does not go out."""
    gmail = FakeGmail(thread_messages=[reply("approve R1")])
    qbo, gmail, payload = scanned(config, ledger, gmail=gmail)
    qbo.live_overrides["1"] = invoice_row(id="1", txn_date=date(2026, 8, 20), balance=0)

    outcomes = send_approved(
        config, ledger, digest_id=payload["digest_id"], qbo=qbo, gmail=gmail,
        today=SENDING_DAY, now=NOW,
    )
    assert outcomes[0].status == "held"
    assert "paid in full" in outcomes[0].detail
    assert len(gmail.sent) == 1


def test_a_balance_that_moved_holds_the_send(config, ledger):
    gmail = FakeGmail(thread_messages=[reply("approve R1")])
    qbo, gmail, payload = scanned(config, ledger, gmail=gmail)
    qbo.live_overrides["1"] = invoice_row(id="1", txn_date=date(2026, 8, 20), balance=1500)

    outcomes = send_approved(
        config, ledger, digest_id=payload["digest_id"], qbo=qbo, gmail=gmail,
        today=SENDING_DAY, now=NOW,
    )
    assert outcomes[0].status == "held"
    assert "balance moved" in outcomes[0].detail


def test_an_unapplied_payment_appearing_later_holds_the_send(config, ledger):
    gmail = FakeGmail(thread_messages=[reply("approve R1")])
    qbo, gmail, payload = scanned(config, ledger, gmail=gmail)
    qbo.rows["Deposit"] = [deposit_row(total=3000.0, account="Chase Business Checking")]

    outcomes = send_approved(
        config, ledger, digest_id=payload["digest_id"], qbo=qbo, gmail=gmail,
        today=SENDING_DAY, now=NOW,
    )
    assert outcomes[0].status == "held"
    assert "money already received" in outcomes[0].detail


def test_a_stale_approval_expires(config, ledger):
    gmail = FakeGmail(thread_messages=[reply("approve R1")])
    qbo, gmail, payload = scanned(config, ledger, gmail=gmail)
    late = NOW + timedelta(hours=config.settings.approval_ttl_hours + 1)

    outcomes = send_approved(
        config, ledger, digest_id=payload["digest_id"], qbo=qbo, gmail=gmail,
        today=SENDING_DAY, now=late,
    )
    assert "approval window" in outcomes[0].detail
    assert len(gmail.sent) == 1


def test_weekends_do_not_send(config, ledger):
    gmail = FakeGmail(thread_messages=[reply("approve R1")])
    qbo, gmail, payload = scanned(config, ledger, gmail=gmail)
    outcomes = send_approved(
        config, ledger, digest_id=payload["digest_id"], qbo=qbo, gmail=gmail,
        today=date(2026, 9, 19), now=NOW,
    )
    assert "not a sending day" in outcomes[0].detail


def test_the_same_stage_is_never_sent_twice(config, ledger):
    gmail = FakeGmail(thread_messages=[reply("approve R1")])
    qbo, gmail, payload = scanned(config, ledger, gmail=gmail)
    kwargs = dict(
        digest_id=payload["digest_id"], qbo=qbo, gmail=gmail, today=SENDING_DAY, now=NOW
    )
    assert send_approved(config, ledger, **kwargs)[0].status == "sent"
    assert send_approved(config, ledger, **kwargs)[0].status == "skipped"
    assert len(gmail.sent) == 2  # digest + one reminder


def test_cli_approval_works_without_gmail_replies(config, ledger):
    qbo, gmail, payload = scanned(config, ledger)
    outcomes = send_approved(
        config, ledger, digest_id=payload["digest_id"], approve_refs=["r1"],
        qbo=qbo, gmail=gmail, today=SENDING_DAY, now=NOW,
    )
    assert outcomes[0].status == "sent"


def test_dry_run_sends_nothing(config, ledger):
    qbo, gmail, payload = scanned(config, ledger)
    outcomes = send_approved(
        config, ledger, digest_id=payload["digest_id"], approve_refs=["R1"], dry_run=True,
        qbo=qbo, gmail=gmail, today=SENDING_DAY, now=NOW,
    )
    assert outcomes[0].detail == "dry run"
    assert len(gmail.sent) == 1


def test_every_decision_is_logged(config, ledger):
    gmail = FakeGmail(thread_messages=[reply("approve R1")])
    qbo, gmail, payload = scanned(config, ledger, gmail=gmail)
    send_approved(
        config, ledger, digest_id=payload["digest_id"], qbo=qbo, gmail=gmail,
        today=SENDING_DAY, now=NOW,
    )
    approvals = list(ledger.records("approval"))
    sends = list(ledger.records("send"))
    assert approvals[0]["decision"] == "approved"
    assert approvals[0]["approver"] == "william@marketplaceofficer.com"
    assert sends[0]["to"] == "ap@acmebrands.com"
    assert sends[0]["stage_id"] == "firm"  # 10 days past due on the sending day


def test_an_ambiguous_reply_is_reported_and_sends_nothing(config, ledger):
    gmail = FakeGmail(thread_messages=[reply("approve all except R1")])
    qbo, gmail, payload = scanned(config, ledger, gmail=gmail)
    outcomes = send_approved(
        config, ledger, digest_id=payload["digest_id"], qbo=qbo, gmail=gmail,
        today=SENDING_DAY, now=NOW,
    )
    assert "could not read" in outcomes[0].detail
    assert len(gmail.sent) == 1


# --- confirming matches -----------------------------------------------------


def matched_scan(config, ledger, gmail=None):
    """A digest carrying one split-payment match and one ordinary reminder."""
    qbo = FakeQbo(
        invoices=[
            invoice_row(id="1", doc_number="1883", customer_id="10", customer_name="JATAI",
                        txn_date=date(2026, 8, 1), total=9765.14),
            invoice_row(id="2", doc_number="1901", customer_id="11", customer_name="Owes Co",
                        txn_date=date(2026, 8, 20), total=1000.0, email="ap@owes.co"),
        ],
        deposits=[
            deposit_row(id="d1", txn_date=date(2026, 9, 8), total=5000.00,
                        customer_names=["JATAI"]),
            deposit_row(id="d2", txn_date=date(2026, 9, 11), total=4765.14,
                        customer_names=["JATAI"]),
        ],
    )
    gmail = gmail or FakeGmail()
    payload = scan(
        config, ledger, today=SENDING_DAY, qbo=qbo, gmail=gmail, now=NOW - timedelta(hours=1)
    )
    return qbo, gmail, payload


def test_the_digest_carries_the_matches(config, ledger):
    _, _, payload = matched_scan(config, ledger)
    assert payload["matches"][0]["ref"] == "M1"
    assert payload["matches"][0]["strategy"] == "split"
    assert payload["matches"][0]["customer_name"] == "JATAI"
    assert len(payload["matches"][0]["sources"]) == 2
    # The matched invoice never becomes a reminder.
    assert [r["customer_name"] for r in payload["reminders"]] == ["Owes Co"]


def test_confirming_a_match_is_logged_and_applies_nothing(config, ledger):
    gmail = FakeGmail(thread_messages=[reply("confirm M1")])
    qbo, gmail, payload = matched_scan(config, ledger, gmail=gmail)
    outcomes = send_approved(
        config, ledger, digest_id=payload["digest_id"], qbo=qbo, gmail=gmail,
        today=SENDING_DAY, now=NOW,
    )

    confirmed = [o for o in outcomes if o.status == "confirmed"]
    assert confirmed and confirmed[0].ref == "M1"
    record = list(ledger.records("match"))[0]
    assert record["decision"] == "confirmed"
    assert record["invoice_id"] == "1"
    assert record["approver"] == "william@marketplaceofficer.com"
    # Confirming touched nothing in QuickBooks and sent nothing to anyone.
    assert qbo.entity_reads.count("Invoice:1") <= 1
    assert len(gmail.sent) == 1


def test_rejecting_a_match_is_logged(config, ledger):
    gmail = FakeGmail(thread_messages=[reply("reject M1")])
    qbo, gmail, payload = matched_scan(config, ledger, gmail=gmail)
    send_approved(
        config, ledger, digest_id=payload["digest_id"], qbo=qbo, gmail=gmail,
        today=SENDING_DAY, now=NOW,
    )
    assert list(ledger.records("match"))[0]["decision"] == "rejected"


def test_a_reply_that_only_confirms_matches_still_counts(config, ledger):
    """'confirm M1' is a real instruction, not silence."""
    gmail = FakeGmail(thread_messages=[reply("confirm M1")])
    qbo, gmail, payload = matched_scan(config, ledger, gmail=gmail)
    outcomes = send_approved(
        config, ledger, digest_id=payload["digest_id"], qbo=qbo, gmail=gmail,
        today=SENDING_DAY, now=NOW,
    )
    assert not any("no approval found" in o.detail for o in outcomes)


def test_confirm_all_confirms_every_match_and_sends_no_reminder(config, ledger):
    gmail = FakeGmail(thread_messages=[reply("confirm all")])
    qbo, gmail, payload = matched_scan(config, ledger, gmail=gmail)
    outcomes = send_approved(
        config, ledger, digest_id=payload["digest_id"], qbo=qbo, gmail=gmail,
        today=SENDING_DAY, now=NOW,
    )
    assert [o.status for o in outcomes if o.ref == "M1"] == ["confirmed"]
    assert len(gmail.sent) == 1  # the digest only


def test_a_confirmed_match_beats_an_approval_in_the_same_reply(config, ledger):
    """If he confirms the money and approves the reminder, the money wins."""
    gmail = FakeGmail(thread_messages=[reply("confirm M1\napprove all")])
    qbo, gmail, payload = matched_scan(config, ledger, gmail=gmail)
    # Put the matched invoice into the reminder list as if a race had proposed it.
    stored = ledger.load_digest(payload["digest_id"])
    stored["reminders"].append(
        {
            "ref": "R9", "invoice_id": "1", "doc_number": "1883", "customer_id": "10",
            "customer_name": "JATAI", "balance_at_proposal": "9765.14", "stage_id": "firm",
            "stage_label": "Firmer follow-up", "template": "firm", "days_past_due": 30,
            "to": "ap@jatai.net", "cc": [],
            "subject": "s", "body": "b",
        }
    )
    (ledger.dir / "digests" / f"{payload['digest_id']}.json").write_text(json.dumps(stored))

    outcomes = send_approved(
        config, ledger, digest_id=payload["digest_id"], qbo=qbo, gmail=gmail,
        today=SENDING_DAY, now=NOW,
    )
    held = next(o for o in outcomes if o.ref == "R9")
    assert held.status == "held"
    assert "confirmed in the same reply" in held.detail
    # The other client's reminder still goes, but JATAI hears nothing.
    assert [o.status for o in outcomes if o.ref == "R1"] == ["sent"]
    assert not any(sent["to"] == "ap@jatai.net" for sent in gmail.sent)


def test_confirming_from_the_command_line(config, ledger):
    qbo, gmail, payload = matched_scan(config, ledger)
    outcomes = send_approved(
        config, ledger, digest_id=payload["digest_id"], confirm_refs=["m1"],
        qbo=qbo, gmail=gmail, today=SENDING_DAY, now=NOW,
    )
    assert [o.status for o in outcomes if o.ref == "M1"] == ["confirmed"]
    assert len(gmail.sent) == 1  # no reminder was approved, so none went out
