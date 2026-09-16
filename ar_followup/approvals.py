"""Reading William's approval out of a reply, conservatively.

The rule the whole build rests on: nothing reaches a client unless a human named
it. So this parser is deliberately dumb and deliberately strict.

  APPROVE ALL        approves every reminder in that digest
  APPROVE R1 R3      approves exactly those reminders
  HOLD R2            holds one (a hold always beats an approve in the same reply)
  CONFIRM M1 M2      accepts those proposed matches
  CONFIRM ALL        accepts every match in that digest
  REJECT M3          says the match is wrong

R refs are reminders and M refs are matches, so the verb barely matters: the
prefix decides which list a ref belongs to, and a positive verb on an M ref is a
confirmation however it is phrased. Confirming a match never applies anything in
QuickBooks. It records that a person agreed with it and will go and do it.

Two traps handled here. First, the digest itself contains the literal text
"APPROVE R1 R2" as instructions — so quoted text is stripped before parsing, or
every reply that quotes the digest would approve everything in it. Second, only
replies from the digest recipient count; a forwarded thread cannot approve a
send.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

REF_RE = re.compile(r"\b([RM])(\d+)\b", re.IGNORECASE)
APPROVE_ALL_RE = re.compile(r"\b(approve|confirm)\s+all\b", re.IGNORECASE)
APPROVE_RE = re.compile(
    r"\b(?:approve[ds]?|confirm(?:ed)?|appl(?:y|ied)|match(?:ed)?|ok|send|yes)\b",
    re.IGNORECASE,
)
HOLD_RE = re.compile(
    r"\b(?:hold|skip|stop|reject(?:ed)?|wrong|no|don'?t|do not)\b", re.IGNORECASE
)
# "approve all except R3" is a sentence we refuse to interpret. Carving an
# exception out of an approve-all is exactly where a wrong send comes from.
EXCLUSION_RE = re.compile(
    r"\b(?:except|excluding|but not|other than|besides|apart from)\b", re.IGNORECASE
)
QUOTE_MARKERS = (
    re.compile(r"^\s*>", re.MULTILINE),
    re.compile(r"^\s*On .+ wrote:\s*$", re.MULTILINE),
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^\s*From:\s.+$", re.MULTILINE),
    re.compile(r"^AR digest — ", re.MULTILINE),
)


@dataclass
class ApprovalSet:
    approve_all: bool = False
    confirm_all: bool = False
    approved: set[str] = field(default_factory=set)
    held: set[str] = field(default_factory=set)
    confirmed: set[str] = field(default_factory=set)
    rejected: set[str] = field(default_factory=set)
    # Lines we would not risk guessing at. Reported back rather than acted on.
    ambiguous: list[str] = field(default_factory=list)
    source_message_id: str = ""
    approver: str = ""

    @property
    def says_anything(self) -> bool:
        return bool(
            self.approve_all or self.confirm_all
            or self.approved or self.held or self.confirmed or self.rejected
        )

    def decision_for(self, ref: str, known_refs: set[str]) -> str:
        """'approved' | 'held' | 'not_mentioned'. Default is never 'approved'."""
        if ref in self.held:
            return "held"
        if ref in self.approved:
            return "approved"
        if self.approve_all and ref in known_refs:
            return "approved"
        return "not_mentioned"

    def match_decision_for(self, ref: str, known_refs: set[str]) -> str:
        """'confirmed' | 'rejected' | 'not_mentioned'."""
        if ref in self.rejected:
            return "rejected"
        if ref in self.confirmed:
            return "confirmed"
        if self.confirm_all and ref in known_refs:
            return "confirmed"
        return "not_mentioned"


def strip_quoted(body: str) -> str:
    """Everything above the first quote marker. The rest is our own digest."""
    cut = len(body)
    for marker in QUOTE_MARKERS:
        match = marker.search(body)
        if match and match.start() < cut:
            cut = match.start()
    return body[:cut].strip()


def parse_reply(body: str) -> ApprovalSet:
    """Read one reply. Line by line, so an approve and a hold can coexist."""
    result = ApprovalSet()
    text = strip_quoted(body)
    if not text:
        return result

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        found = [(m.group(1).upper(), m.group(2)) for m in REF_RE.finditer(line)]
        reminders = {f"R{n}" for prefix, n in found if prefix == "R"}
        matches = {f"M{n}" for prefix, n in found if prefix == "M"}
        holding = bool(HOLD_RE.search(line))
        approving = bool(APPROVE_RE.search(line))

        blanket = APPROVE_ALL_RE.search(line)
        if blanket:
            # A blanket approval qualified by anything at all is not blanket.
            if holding or reminders or matches or EXCLUSION_RE.search(line):
                result.ambiguous.append(line)
                continue
            if blanket.group(1).lower() == "confirm":
                result.confirm_all = True
            else:
                result.approve_all = True
            continue

        if not reminders and not matches:
            continue
        if holding:
            result.held |= reminders
            result.rejected |= matches
        elif approving:
            result.approved |= reminders
            result.confirmed |= matches

    # A negative always wins over a positive for the same ref, in the same reply
    # or across lines. Cheaper to miss one than to do one we were told not to.
    result.approved -= result.held
    result.confirmed -= result.rejected
    return result


def merge(replies: list[ApprovalSet]) -> ApprovalSet:
    """Fold replies oldest-first; a later reply can flip an earlier decision."""
    merged = ApprovalSet()
    for reply in replies:
        if reply.approve_all:
            merged.approve_all = True
        if reply.confirm_all:
            merged.confirm_all = True
        merged.ambiguous.extend(reply.ambiguous)
        merged.approved = (merged.approved - reply.held) | reply.approved
        merged.held = (merged.held - reply.approved) | reply.held
        merged.confirmed = (merged.confirmed - reply.rejected) | reply.confirmed
        merged.rejected = (merged.rejected - reply.confirmed) | reply.rejected
        if reply.says_anything:
            merged.source_message_id = reply.source_message_id or merged.source_message_id
            merged.approver = reply.approver or merged.approver
    merged.approved -= merged.held
    merged.confirmed -= merged.rejected
    return merged


def within_ttl(sent_at: datetime, now: datetime, ttl_hours: int) -> bool:
    return now - sent_at <= timedelta(hours=ttl_hours)


def message_datetime(raw_date: str) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(raw_date)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def approvals_from_thread(messages, approver_email: str) -> ApprovalSet:
    """Collect decisions from every reply the digest recipient wrote."""
    replies: list[ApprovalSet] = []
    needle = approver_email.lower().strip()
    for message in messages:
        if message.sender_email != needle:
            continue
        parsed = parse_reply(message.body)
        parsed.source_message_id = message.id
        parsed.approver = message.sender_email
        replies.append(parsed)
    return merge(replies)
