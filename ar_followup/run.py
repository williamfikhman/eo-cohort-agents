"""The two things the agent actually does.

`scan` runs every morning: reconcile the money against the invoices first, then
report AR on whatever that leaves, and email William the digest. It sends no
client email and writes nothing to QBO, so it is safe on a cron.

`send-approved` runs after William replies. It records his decisions on the
proposed matches, then re-verifies every approved reminder against live
QuickBooks data one more time and sends the ones that still check out. An
approval is permission to send, not proof the invoice is still unpaid — money
that arrived overnight still wins, and so does a match confirmed in the same
reply.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import requests

from .approvals import ApprovalSet, approvals_from_thread, within_ttl
from .config import Config
from .digest import render_html, render_text, subject as digest_subject
from .gmail import GmailClient, GmailError
from .ledger import Ledger
from .matching import collect_sources
from .models import money
from .pipeline import run_scan
from .qbo.auth import QboAuth
from .qbo.client import QboClient, QboError
from .qbo.reads import credit_memos, recent_deposits, recent_payments, refresh_invoice
from .templates import render
from .verify import money_for_invoice

LOG = logging.getLogger("ar_followup.run")


def build_qbo(session: requests.Session | None = None) -> QboClient:
    return QboClient(QboAuth.from_env(session=session), session=session)


def _digest_payload(result, config: Config) -> dict[str, Any]:
    """What gets persisted so a later send can be re-verified against it."""
    stage_templates = {s.id: s.template for s in config.settings.stages}
    return {
        "digest_id": result.digest_id,
        "as_of": result.as_of,
        "matches": [
            {
                "ref": m.ref,
                "customer_name": m.customer_name,
                "invoice_ids": [inv.id for inv in m.invoices],
                "invoice_labels": m.invoice_labels,
                "sources": [
                    {
                        "kind": s.kind,
                        "id": s.id,
                        "date": s.txn_date,
                        "amount": s.amount,
                        "account": s.account,
                        "posted_to_income": s.posted_to_income,
                    }
                    for s in m.sources
                ],
                "amount": m.amount,
                "strategy": m.strategy,
                "confidence": m.confidence,
                "rationale": m.rationale,
                "double_counted": m.double_counted,
            }
            for m in result.matches
        ],
        "unexplained": [
            {
                "ref": u.ref,
                "customer_name": u.source.customer_name,
                "kind": u.source.kind,
                "source_id": u.source.id,
                "date": u.source.txn_date,
                "amount": u.source.amount,
                "account": u.source.account,
                "reason": u.reason,
            }
            for u in result.unexplained
        ],
        "reminders": [
            {
                "ref": r.ref,
                "invoice_id": r.invoice.id,
                "doc_number": r.invoice.doc_number,
                "customer_id": r.invoice.customer_id,
                "customer_name": r.invoice.customer_name,
                "balance_at_proposal": r.invoice.balance,
                "stage_id": r.stage_id,
                "stage_label": r.stage_label,
                "template": stage_templates.get(r.stage_id, r.stage_id),
                "days_past_due": r.days_past_due,
                "to": r.to,
                "cc": r.cc,
                "subject": r.subject,
                "body": r.body,
            }
            for r in result.reminders
        ],
        "flags": [
            {
                "ref": f.ref,
                "kind": f.kind,
                "customer_name": f.customer_name,
                "reason": f.reason,
                "action": f.recommended_action,
                "invoice_label": f.invoice_label,
                "amount": f.amount,
                "severity": f.severity,
            }
            for f in result.flags
        ],
        "errors": result.errors,
        "thread_id": None,
        "sent_at": None,
    }


def scan(
    config: Config,
    ledger: Ledger,
    today: date | None = None,
    email: bool = True,
    qbo: QboClient | None = None,
    gmail: GmailClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The morning run. Returns the digest payload; emails it unless told not to."""
    today = today or date.today()
    qbo = qbo or build_qbo()
    result = run_scan(qbo, config, ledger, today=today)

    payload = _digest_payload(result, config)
    text = render_text(result, config.settings)
    html_body = render_html(result, config.settings)
    subject_line = digest_subject(result, config.settings)

    if result.snapshot:
        ledger.record_snapshot(result.snapshot)

    if email:
        gmail = gmail or GmailClient.from_env()
        sent = gmail.send(
            sender=config.settings.billing_from,
            to=config.settings.digest_to,
            subject=subject_line,
            text=text,
            html_body=html_body,
            reply_to=config.settings.billing_reply_to,
        )
        payload["thread_id"] = sent.get("threadId")
        payload["sent_at"] = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        LOG.info("digest %s emailed to %s", result.digest_id, config.settings.digest_to)

    ledger.record_digest(result.digest_id, result.as_of, payload)
    payload["_text"] = text
    payload["_html"] = html_body
    payload["_subject"] = subject_line
    return payload


# --- sending approved reminders --------------------------------------------


@dataclass
class SendOutcome:
    ref: str
    customer: str
    invoice_label: str
    status: str  # sent | held | skipped | failed
    detail: str = ""


def _collect_approvals(
    digest: dict[str, Any], config: Config, gmail: GmailClient
) -> ApprovalSet:
    thread_id = digest.get("thread_id")
    if not thread_id:
        raise GmailError(
            "this digest has no Gmail thread id, so there is no reply to read. "
            "Approve on the command line with --approve instead."
        )
    messages = gmail.thread(thread_id)
    return approvals_from_thread(messages, config.settings.digest_to)


def _approval_is_fresh(digest: dict[str, Any], config: Config, now: datetime) -> tuple[bool, str]:
    sent_at = digest.get("sent_at")
    if not sent_at:
        return True, ""
    try:
        stamp = datetime.fromisoformat(sent_at)
    except ValueError:
        return True, ""
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    ttl = config.settings.approval_ttl_hours
    if within_ttl(stamp, now, ttl):
        return True, ""
    return False, (
        f"digest is older than the {ttl}h approval window; re-run the scan so the "
        "proposals are re-verified against today's data"
    )


def send_approved(
    config: Config,
    ledger: Ledger,
    digest_id: str | None = None,
    approve_refs: list[str] | None = None,
    confirm_refs: list[str] | None = None,
    dry_run: bool = False,
    today: date | None = None,
    qbo: QboClient | None = None,
    gmail: GmailClient | None = None,
    now: datetime | None = None,
) -> list[SendOutcome]:
    """Re-verify, then send, only what a human explicitly approved."""
    settings = config.settings
    today = today or date.today()
    now = now or datetime.now(timezone.utc)

    digest_id = digest_id or ledger.latest_digest_id()
    if not digest_id:
        return [SendOutcome("-", "-", "-", "skipped", "no digest has been produced yet")]
    digest = ledger.load_digest(digest_id)
    if digest is None:
        return [SendOutcome("-", "-", "-", "skipped", f"digest {digest_id} not found")]

    reminders = digest.get("reminders") or []
    # A digest can carry matches and no reminders at all — that is a good
    # morning, not an empty one, and those confirmations still need recording.
    if not reminders and not (digest.get("matches") or []):
        return [SendOutcome("-", "-", "-", "skipped", f"digest {digest_id} proposed nothing")]

    fresh, why = _approval_is_fresh(digest, config, now)
    if not fresh:
        return [SendOutcome("-", "-", "-", "skipped", why)]

    if today.weekday() not in settings.send_weekdays:
        return [SendOutcome("-", "-", "-", "skipped", "today is not a sending day")]

    known_refs = {str(r["ref"]) for r in reminders}
    if approve_refs or confirm_refs:
        approvals = ApprovalSet(
            approved={r.upper() for r in (approve_refs or [])},
            confirmed={r.upper() for r in (confirm_refs or [])},
            approver="cli",
            source_message_id="cli",
        )
        source = "cli"
    else:
        gmail = gmail or GmailClient.from_env()
        approvals = _collect_approvals(digest, config, gmail)
        source = "email-reply"

    outcomes: list[SendOutcome] = []
    if approvals.ambiguous:
        # We do not guess at "approve all except R3". Say so and move on.
        outcomes.append(
            SendOutcome(
                "-",
                "-",
                "-",
                "skipped",
                "could not read: "
                + "; ".join(f'"{line}"' for line in approvals.ambiguous[:3])
                + ". Reply with explicit refs, e.g. APPROVE R1 R2.",
            )
        )

    if not approvals.says_anything:
        return outcomes + [SendOutcome("-", "-", "-", "skipped", "no approval found; nothing sent")]

    qbo = qbo or build_qbo()
    gmail = gmail or GmailClient.from_env()
    sent_stages = ledger.sent_stages()

    since = today - timedelta(days=settings.match_lookback_days)
    payments = recent_payments(qbo, since)
    deposits = recent_deposits(qbo, since, settings.bank_feed_accounts)
    try:
        memos = credit_memos(qbo)
    except QboError:
        memos = []
    sources = collect_sources(payments, deposits, memos, settings)

    # --- record what he said about the matches, before any reminder goes out.
    # A match confirmed in this reply takes its invoice out of the send list,
    # even if the same reply approved the reminder for it.
    confirmed_invoice_ids = _record_match_decisions(digest, ledger, approvals, source)
    outcomes.extend(
        SendOutcome(ref, customer, label, status, detail)
        for ref, customer, label, status, detail in _match_outcomes(digest, approvals)
    )

    for proposal in reminders:
        ref = str(proposal["ref"])
        customer = str(proposal.get("customer_name") or "")
        label = f"#{proposal.get('doc_number') or proposal.get('invoice_id')}"
        decision = approvals.decision_for(ref, known_refs)

        if decision != "approved":
            ledger.record_approval(
                digest_id, ref, decision, approvals.approver or "-", source
            )
            outcomes.append(SendOutcome(ref, customer, label, "skipped", f"not approved ({decision})"))
            continue

        ledger.record_approval(digest_id, ref, "approved", approvals.approver or "cli", source)

        invoice_id = str(proposal["invoice_id"])
        stage_id = str(proposal["stage_id"])
        if invoice_id in confirmed_invoice_ids:
            outcomes.append(
                SendOutcome(
                    ref, customer, label, "held",
                    "a match on this invoice was confirmed in the same reply",
                )
            )
            continue
        if stage_id in sent_stages.get(invoice_id, set()):
            outcomes.append(
                SendOutcome(ref, customer, label, "skipped", f"stage '{stage_id}' already sent")
            )
            continue

        # --- verification, again, seconds before the send -------------------
        invoice = refresh_invoice(qbo, invoice_id, settings.payment_terms_days)
        if invoice is None:
            outcomes.append(SendOutcome(ref, customer, label, "held", "invoice could not be re-read"))
            continue
        if invoice.balance <= 0:
            outcomes.append(
                SendOutcome(ref, customer, invoice.label, "held", "invoice is paid in full now")
            )
            continue

        proposed_balance = money(proposal.get("balance_at_proposal") or Decimal("0"))
        if invoice.balance != proposed_balance:
            outcomes.append(
                SendOutcome(
                    ref,
                    customer,
                    invoice.label,
                    "held",
                    f"balance moved from ${proposed_balance:,.2f} to ${invoice.balance:,.2f} "
                    "since the digest; re-run the scan",
                )
            )
            continue

        found = money_for_invoice(invoice, sources, settings)
        if found:
            outcomes.append(
                SendOutcome(
                    ref,
                    customer,
                    invoice.label,
                    "held",
                    f"money already received matches this invoice ({found[0].rationale})",
                )
            )
            continue

        template = str(proposal.get("template") or stage_id)
        subject_line, body = render(template, invoice, today, settings)
        to = str(proposal["to"])
        cc = [str(c) for c in (proposal.get("cc") or [])]

        if dry_run:
            outcomes.append(SendOutcome(ref, customer, invoice.label, "skipped", "dry run"))
            continue

        try:
            sent = gmail.send(
                sender=settings.billing_from,
                to=to,
                subject=subject_line,
                text=body,
                cc=cc,
                reply_to=settings.billing_reply_to,
            )
        except GmailError as exc:
            outcomes.append(SendOutcome(ref, customer, invoice.label, "failed", str(exc)))
            continue

        ledger.record_send(
            digest_id=digest_id,
            invoice_id=invoice_id,
            stage_id=stage_id,
            customer=customer,
            to=to,
            cc=cc,
            subject=subject_line,
            message_id=str(sent.get("id") or ""),
            approver=approvals.approver or "cli",
        )
        sent_stages.setdefault(invoice_id, set()).add(stage_id)
        outcomes.append(SendOutcome(ref, customer, invoice.label, "sent", to))

    return outcomes


def _record_match_decisions(
    digest: dict[str, Any], ledger: Ledger, approvals: ApprovalSet, source: str
) -> set[str]:
    """Log every match decision and return the invoices a confirmation covers.

    Confirming a match changes nothing in QuickBooks. It records that a person
    read the proposal and agreed, which keeps the invoice out of the cadence and
    starts the clock on actually applying it.
    """
    proposals = digest.get("matches") or []
    known = {str(m["ref"]) for m in proposals}
    covered: set[str] = set()

    for proposal in proposals:
        ref = str(proposal["ref"])
        decision = approvals.match_decision_for(ref, known)
        if decision == "not_mentioned":
            continue
        invoice_ids = [str(i) for i in (proposal.get("invoice_ids") or [])]
        for invoice_id in invoice_ids or [""]:
            ledger.record_match_decision(
                digest_id=str(digest.get("digest_id") or ""),
                ref=ref,
                decision=decision,
                approver=approvals.approver or "cli",
                source=source,
                invoice_id=invoice_id,
                invoice_label=str(proposal.get("invoice_labels") or ""),
                customer=str(proposal.get("customer_name") or ""),
                amount=proposal.get("amount"),
                detail=str(proposal.get("rationale") or ""),
            )
        if decision == "confirmed":
            covered.update(invoice_ids)
    return covered


def _match_outcomes(digest: dict[str, Any], approvals: ApprovalSet):
    """One reportable line per match decision, for the command-line summary."""
    proposals = digest.get("matches") or []
    known = {str(m["ref"]) for m in proposals}
    for proposal in proposals:
        ref = str(proposal["ref"])
        decision = approvals.match_decision_for(ref, known)
        if decision == "not_mentioned":
            continue
        status, detail = (
            ("confirmed", "apply it in QBO; the agent will chase it if it stays open")
            if decision == "confirmed"
            else ("rejected", "will only be re-proposed if the money still matches")
        )
        yield (
            ref,
            str(proposal.get("customer_name") or ""),
            str(proposal.get("invoice_labels") or ""),
            status,
            detail,
        )
