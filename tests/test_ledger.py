import json
from datetime import date

from ar_followup.ledger import new_digest_id
from ar_followup.models import Snapshot, money


def test_sent_stages_group_by_invoice(ledger):
    ledger.record_send("d1", "1", "friendly", "Acme", "ap@acme.com", [], "s", "m1", "william")
    ledger.record_send("d1", "1", "firm", "Acme", "ap@acme.com", [], "s", "m2", "william")
    ledger.record_send("d2", "2", "friendly", "Other", "ap@other.com", [], "s", "m3", "william")
    assert ledger.sent_stages() == {"1": {"friendly", "firm"}, "2": {"friendly"}}


def test_a_later_approval_overrides_an_earlier_one(ledger):
    ledger.record_approval("d1", "R1", "approved", "william", "email-reply")
    ledger.record_approval("d1", "R1", "held", "william", "email-reply")
    assert ledger.approvals_for("d1")["R1"]["decision"] == "held"


def test_a_torn_line_does_not_break_the_run(ledger):
    ledger.record_send("d1", "1", "friendly", "Acme", "ap@acme.com", [], "s", "m1", "william")
    with ledger.path.open("a") as handle:
        handle.write('{"type": "send", "invoice_id": "2"\n')  # power cut mid-write
    assert ledger.sent_stages() == {"1": {"friendly"}}


def test_snapshots_round_trip(ledger):
    ledger.record_snapshot(Snapshot(date(2026, 9, 5), money(40000), money(12000), 14, 5))
    stored = ledger.snapshots()[0]
    assert stored.total_ar == money(40000)
    assert stored.as_of == date(2026, 9, 5)


def test_week_over_week_tolerates_a_missed_morning(ledger):
    ledger.record_snapshot(Snapshot(date(2026, 9, 6), money(1), money(1), 1, 1))
    assert ledger.snapshot_near(date(2026, 9, 5)).as_of == date(2026, 9, 6)
    assert ledger.snapshot_near(date(2026, 8, 1)) is None


def test_digests_are_stored_and_found(ledger):
    digest_id = new_digest_id(date(2026, 9, 12))
    ledger.record_digest(digest_id, date(2026, 9, 12), {"reminders": [{"ref": "R1"}]})
    assert ledger.latest_digest_id() == digest_id
    assert ledger.load_digest(digest_id)["reminders"][0]["ref"] == "R1"


def test_money_is_written_as_a_string_not_a_float(ledger):
    ledger.append("test", amount=money("6026.67"))
    record = json.loads(ledger.path.read_text().splitlines()[-1])
    assert record["amount"] == "6026.67"
