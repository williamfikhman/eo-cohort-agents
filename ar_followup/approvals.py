"""Reading William's approval out of a reply, conservatively.

The rule the whole build rests on: nothing reaches a client unless a human named
it. So this parser is deliberately dumb and deliberately strict.

  APPROVE ALL        approves every reminder in that digest
  APPROVE R1 R3      approves exactly those
  HOLD R2            holds one (a hold always beats an approve in the same reply)

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

REF_RE = re.compile(r"\bR(\d+)\b", re.IGNORECASE)
APPROVE_ALL_RE = re.compile(r"\bapprove\s+all\b", re.IGNORECASE)
APPROVE_RE = re.compile(r"\b(?:approve[ds]?|ok|send|yes)\b", re.IGNORECASE)
HOLD_RE = re.compile(r"\b(?:hold|skip|stop|no|don'?t|do not)\b", re.IGNORECASE)
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
    approved: set[str] = field(default_factory=set)
    held: set[str] = field(default_factory=set)
    # Lines we would not risk guessing at. Reported back rather than acted on.
    ambiguous: list[str] = field(default_factory=list)
    source_message_id: str = ""
    approver: str = ""

    def decision_for(self, ref: str, known_refs: set[str]) -> str:
        """'approved' | 'held' | 'not_mentioned'. Default is never 'approved'."""
        if ref in self.held:
            return "held"
        if ref in self.approved:
            return "approved"
        if self.approve_all and ref in known_refs:
            return "approved"
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
        refs = {f"R{m.group(1)}" for m in REF_RE.finditer(line)}
        holding = bool(HOLD_RE.search(line))
        approving = bool(APPROVE_RE.search(line))

        if APPROVE_ALL_RE.search(line):
            # An approve-all qualified by anything at all is not an approve-all.
            if holding or refs or EXCLUSION_RE.search(line):
                result.ambiguous.append(line)
                continue
            result.approve_all = True
            continue
        if not refs:
            continue
        if holding:
            result.held |= refs
        elif approving:
            result.approved |= refs

    # A hold always wins over an approve for the same ref, in the same reply or
    # across lines. Cheaper to miss a send than to make one we were told not to.
    result.approved -= result.held
    return result


def merge(replies: list[ApprovalSet]) -> ApprovalSet:
    """Fold replies oldest-first; a later reply can flip an earlier decision."""
    merged = ApprovalSet()
    for reply in replies:
        if reply.approve_all:
            merged.approve_all = True
        merged.ambiguous.extend(reply.ambiguous)
        merged.approved = (merged.approved - reply.held) | reply.approved
        merged.held = (merged.held - reply.approved) | reply.held
        if reply.approved or reply.held or reply.approve_all:
            merged.source_message_id = reply.source_message_id or merged.source_message_id
            merged.approver = reply.approver or merged.approver
    merged.approved -= merged.held
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
