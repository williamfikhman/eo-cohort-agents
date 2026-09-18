from ar_followup.approvals import ApprovalSet, approvals_from_thread, merge, parse_reply, strip_quoted
from ar_followup.gmail import GmailMessage

DIGEST_QUOTE = """
On Sat, Sep 12, 2026 at 7:00 AM AR agent <billing@marketplaceofficer.com> wrote:
> 1. REMINDERS QUEUED FOR APPROVAL
> Reply: APPROVE R1 R2   |   APPROVE ALL   |   HOLD R3
"""


def message(body, sender="William <william@marketplaceofficer.com>", id="m1"):
    return GmailMessage(id=id, thread_id="t1", sender=sender, subject="AR digest", body=body, received="")


def test_quoted_digest_text_never_approves_anything():
    """The digest quotes its own instructions back. Parsing them would send everything."""
    assert parse_reply(DIGEST_QUOTE).approved == set()
    assert parse_reply(DIGEST_QUOTE).approve_all is False


def test_approve_specific_refs():
    result = parse_reply("approve R1 R4" + DIGEST_QUOTE)
    assert result.approved == {"R1", "R4"}


def test_approve_all():
    result = parse_reply("Approve all" + DIGEST_QUOTE)
    assert result.approve_all is True
    assert result.decision_for("R9", {"R9"}) == "approved"


def test_hold_beats_approve_for_the_same_ref():
    result = parse_reply("approve R1 R2\nhold R2")
    assert result.approved == {"R1"}
    assert result.held == {"R2"}


def test_an_ambiguous_line_approves_nothing():
    """'approve all except R3' is not a grammar we parse. Safer to send nothing."""
    result = parse_reply("approve all except R3")
    assert result.approved == set()
    assert result.approve_all is False


def test_silence_approves_nothing():
    result = parse_reply("thanks, looking now")
    assert result.approved == set() and result.approve_all is False


def test_a_later_reply_can_flip_an_earlier_one():
    merged = merge([parse_reply("approve R1 R2"), parse_reply("hold R2")])
    assert merged.approved == {"R1"}
    assert merged.held == {"R2"}


def test_only_the_digest_recipient_can_approve():
    thread = [
        message("approve all", sender="Angie <angie@marketplaceofficer.com>", id="m1"),
        message("approve R1", sender="William <william@marketplaceofficer.com>", id="m2"),
    ]
    result = approvals_from_thread(thread, "william@marketplaceofficer.com")
    assert result.approve_all is False
    assert result.approved == {"R1"}


def test_decision_defaults_to_not_mentioned():
    empty = ApprovalSet()
    assert empty.decision_for("R1", {"R1"}) == "not_mentioned"


def test_strip_quoted_keeps_the_new_text_only():
    assert strip_quoted("approve R1\n\n> old digest text") == "approve R1"
