"""One morning's work: read QBO, decide, and produce the digest payload.

Nothing in this module sends anything to a client or writes anything to QBO. It
ends with a RunResult — a list of proposals — which is exactly what the digest
shows William for approval.

Order matters here:

  1. pull live open invoices (never an aging report)
  2. pull recent payments, bank-feed deposits and open credit memos
  3. per client: validate the invoices against the contract on file
  4. per invoice: decide the cadence stage, then verify before proposing
  5. anything unexplained becomes a flag instead of an email
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable

from .cadence import ESCALATE, NONE, SUPPRESS, decide, due_date_for
from .config import ClientTerms, Config
from .ledger import Ledger, new_digest_id
from .models import (
    Flag,
    Invoice,
    ReminderCandidate,
    RunResult,
    Snapshot,
)
from .qbo.client import QboClient, QboError
from .qbo.reads import (
    credit_memos,
    invoice_share_link,
    open_invoices,
    recent_deposits,
    recent_payments,
)
from .templates import render
from .terms import (
    credit_drawdown_findings,
    overbilling_estimate,
    upcoming_contract_changes,
    validate_invoice,
)
from .verify import verify_invoice

LOG = logging.getLogger("ar_followup.pipeline")
ZERO = Decimal("0.00")


class RefCounter:
    """Hands out the R1 / F1 / M1 handles William approves by."""

    def __init__(self, prefix: str):
        self.prefix = prefix
        self.n = 0

    def next(self) -> str:
        self.n += 1
        return f"{self.prefix}{self.n}"


def _group_by_customer(invoices: Iterable[Invoice]) -> dict[str, list[Invoice]]:
    grouped: dict[str, list[Invoice]] = {}
    for invoice in invoices:
        grouped.setdefault(invoice.customer_id, []).append(invoice)
    return grouped


def _recipient(invoice: Invoice, client: ClientTerms | None) -> str | None:
    """clients.yaml wins over the invoice's BillEmail — AP contacts move."""
    if client and client.billing_contact:
        return client.billing_contact
    return invoice.bill_email or None


def build_snapshot(invoices: list[Invoice], today: date) -> Snapshot:
    past_due = [inv for inv in invoices if inv.due_date < today]
    return Snapshot(
        as_of=today,
        total_ar=sum((inv.balance for inv in invoices), ZERO),
        past_due_ar=sum((inv.balance for inv in past_due), ZERO),
        open_invoice_count=len(invoices),
        past_due_count=len(past_due),
    )


def run_scan(
    qbo: QboClient,
    config: Config,
    ledger: Ledger,
    today: date | None = None,
) -> RunResult:
    settings = config.settings
    today = today or date.today()
    result = RunResult(digest_id=new_digest_id(today), as_of=today)

    reminder_refs = RefCounter("R")
    flag_refs = RefCounter("F")
    match_refs = RefCounter("M")

    invoices = open_invoices(qbo, settings.payment_terms_days)
    LOG.info("%s open invoices carrying a balance", len(invoices))

    since = today - timedelta(days=settings.unapplied_lookback_days)
    payments = recent_payments(qbo, since)
    deposits = recent_deposits(qbo, since, settings.bank_feed_accounts)
    LOG.info("%s payments and %s bank-feed deposits since %s", len(payments), len(deposits), since)

    try:
        memos = credit_memos(qbo)
    except QboError as exc:
        memos = []
        result.errors.append(f"credit memos could not be read: {exc}")
    credit_by_customer: dict[str, Decimal] = {}
    for memo in memos:
        credit_by_customer[memo.customer_id] = (
            credit_by_customer.get(memo.customer_id, ZERO) + memo.remaining
        )

    result.snapshot = build_snapshot(invoices, today)
    result.prior_snapshot = ledger.snapshot_near(today - timedelta(days=7))
    sent_stages = ledger.sent_stages()

    missing_terms: list[str] = []

    for customer_id, customer_invoices in _group_by_customer(invoices).items():
        customer_name = customer_invoices[0].customer_name
        try:
            _process_customer(
                qbo=qbo,
                config=config,
                result=result,
                today=today,
                customer_invoices=customer_invoices,
                customer_name=customer_name,
                customer_id=customer_id,
                payments=payments,
                deposits=deposits,
                qbo_credit=credit_by_customer.get(customer_id, ZERO),
                sent_stages=sent_stages,
                reminder_refs=reminder_refs,
                flag_refs=flag_refs,
                match_refs=match_refs,
                missing_terms=missing_terms,
            )
        except Exception as exc:  # one client's bad data never kills the digest
            LOG.exception("failed while processing %s", customer_name)
            result.errors.append(f"{customer_name}: {exc}")

    _add_contract_change_flags(config, result, today, flag_refs)
    _add_missing_terms_flag(result, missing_terms, flag_refs)

    # Sort first, number second, so the refs William replies with read in order.
    result.flags.sort(key=lambda f: f.sort_key)
    for index, flag in enumerate(result.flags, start=1):
        flag.ref = f"F{index}"
    return result


def _process_customer(
    *,
    qbo: QboClient,
    config: Config,
    result: RunResult,
    today: date,
    customer_invoices: list[Invoice],
    customer_name: str,
    customer_id: str,
    payments: list,
    deposits: list,
    qbo_credit: Decimal,
    sent_stages: dict[str, set[str]],
    reminder_refs: RefCounter,
    flag_refs: RefCounter,
    match_refs: RefCounter,
    missing_terms: list[str],
) -> None:
    settings = config.settings
    client = config.find_client(customer_id, customer_name)

    # --- contract validation, once per client ------------------------------
    seen_kinds: set[str] = set()
    blocked_invoice_ids: set[str] = set()
    for invoice in customer_invoices:
        for finding in validate_invoice(invoice, client, settings):
            if finding.kind == "no_contract_terms":
                if customer_name not in missing_terms:
                    missing_terms.append(customer_name)
                continue
            if finding.severity == "urgent":
                blocked_invoice_ids.add(invoice.id)
            # Client-level findings (no terms on file, terms not captured) are
            # worth saying once, not once per invoice.
            key = f"{finding.kind}:{invoice.id if finding.severity == 'urgent' else ''}"
            if finding.kind in ("no_contract_terms", "terms_unverified", "terms_gap"):
                key = finding.kind
            if key in seen_kinds:
                continue
            seen_kinds.add(key)
            result.flags.append(
                Flag(
                    ref=flag_refs.next(),
                    kind=finding.kind,
                    customer_name=customer_name,
                    reason=finding.reason,
                    recommended_action=finding.action,
                    invoice_label=invoice.label,
                    amount=finding.amount,
                    severity=finding.severity,
                )
            )

    if client:
        for finding in credit_drawdown_findings(customer_invoices, client):
            result.flags.append(
                Flag(
                    ref=flag_refs.next(),
                    kind=finding.kind,
                    customer_name=customer_name,
                    reason=finding.reason,
                    recommended_action=finding.action,
                    amount=finding.amount,
                    severity=finding.severity,
                )
            )
        overbilled = overbilling_estimate(customer_invoices, client, settings)
        if overbilled > 0:
            result.flags.append(
                Flag(
                    ref=flag_refs.next(),
                    kind="overbilling_total",
                    customer_name=customer_name,
                    reason=(
                        f"${overbilled:,.2f} of flat fees billed across open invoices under "
                        "contract periods that have no flat fee."
                    ),
                    recommended_action="Confirm the credit owed and issue it before chasing anything.",
                    amount=overbilled,
                    severity="urgent",
                )
            )
        for flag in client.flags:
            result.flags.append(
                Flag(
                    ref=flag_refs.next(),
                    kind=str(flag.get("type") or "carry_forward"),
                    customer_name=customer_name,
                    reason=str(flag.get("note") or "Carry-forward flag from clients.yaml."),
                    recommended_action="Human follow-up; automation stays off.",
                    severity="review",
                )
            )

    # A credit sitting in QBO that clients.yaml does not know about still
    # suppresses reminders — the file is a record, not the authority on money.
    credit_suppressed = qbo_credit > 0
    if credit_suppressed:
        result.flags.append(
            Flag(
                ref=flag_refs.next(),
                kind="open_credit_balance",
                customer_name=customer_name,
                reason=(
                    f"{customer_name} has ${qbo_credit:,.2f} in open credit memos in QBO. "
                    "Reminders suppressed until it is applied or exhausted."
                ),
                recommended_action="Apply the credit to the open invoices, then re-run.",
                amount=qbo_credit,
                severity="urgent" if not (client and client.credit_balance) else "review",
            )
        )

    # --- per invoice --------------------------------------------------------
    suppressed: list[Invoice] = []
    suppression_decision = None

    for invoice in customer_invoices:
        decision = decide(
            invoice=invoice,
            client=client,
            settings=settings,
            today=today,
            sent_stage_ids=sent_stages.get(invoice.id, set()),
            client_open_invoices=customer_invoices,
        )

        if decision.action == NONE:
            continue

        if decision.action == SUPPRESS:
            result.suppressed_count += 1
            days = (today - due_date_for(invoice, client, settings)).days
            if days >= settings.stages[0].days_past_due:
                suppressed.append(invoice)
                suppression_decision = decision
            continue

        if decision.action == ESCALATE:
            result.flags.append(
                Flag(
                    ref=flag_refs.next(),
                    kind="escalation_call",
                    customer_name=customer_name,
                    reason=f"Invoice {invoice.label} for ${invoice.balance:,.2f}: {decision.reason}",
                    recommended_action=decision.action_note,
                    invoice_label=invoice.label,
                    amount=invoice.balance,
                    severity="urgent",
                )
            )
            continue

        if invoice.id in blocked_invoice_ids:
            result.flags.append(
                Flag(
                    ref=flag_refs.next(),
                    kind="reminder_held_billing_error",
                    customer_name=customer_name,
                    reason=(
                        f"Invoice {invoice.label} is due a reminder but does not match the "
                        "contract on file (see the flag above)."
                    ),
                    recommended_action="Fix or confirm the invoice, then it re-proposes tomorrow.",
                    invoice_label=invoice.label,
                    amount=invoice.balance,
                    severity="urgent",
                )
            )
            continue

        if credit_suppressed:
            continue  # already flagged above; no reminder while a credit is open

        # --- pre-send verification -----------------------------------------
        verification = verify_invoice(
            qbo=qbo,
            invoice=invoice,
            payments=payments,
            deposits=deposits,
            settings=settings,
            ref_start=match_refs.n + 1,
        )
        fresh = verification.invoice

        for candidate in verification.unapplied:
            match_refs.n += 1
            result.unapplied.append(candidate)

        if verification.unapplied:
            result.flags.append(
                Flag(
                    ref=flag_refs.next(),
                    kind="possible_unapplied_payment",
                    customer_name=customer_name,
                    reason=(
                        f"Invoice {fresh.label} was due a {decision.stage.label} but money "
                        f"matching ${fresh.balance:,.2f} may already have arrived."
                    ),
                    recommended_action="Check section 3 and apply the payment by hand if it matches.",
                    invoice_label=fresh.label,
                    amount=fresh.balance,
                    severity="urgent",
                )
            )
            continue

        if not verification.still_open:
            # The stale-list case: the top-of-run pull said open, the live read
            # says paid. No reminder, and worth saying out loud once.
            result.flags.append(
                Flag(
                    ref=flag_refs.next(),
                    kind="already_paid",
                    customer_name=customer_name,
                    reason=verification.reasons_to_hold[0],
                    recommended_action="No action. Logged so the miss is visible.",
                    invoice_label=fresh.label,
                    severity="review",
                )
            )
            continue

        if verification.reasons_to_hold:
            result.flags.append(
                Flag(
                    ref=flag_refs.next(),
                    kind="short_pay_review",
                    customer_name=customer_name,
                    reason=f"Invoice {fresh.label}: " + " ".join(verification.reasons_to_hold),
                    recommended_action="Confirm whether the short pay is agreed before chasing it.",
                    invoice_label=fresh.label,
                    amount=fresh.balance,
                    severity="review",
                )
            )
            continue

        recipient = _recipient(fresh, client)
        if not recipient:
            result.flags.append(
                Flag(
                    ref=flag_refs.next(),
                    kind="no_billing_contact",
                    customer_name=customer_name,
                    reason=(
                        f"Invoice {fresh.label} is due a {decision.stage.label} but there is no "
                        "billing email on the invoice or in clients.yaml."
                    ),
                    recommended_action="Add billing_contact for this client in config/clients.yaml.",
                    invoice_label=fresh.label,
                    amount=fresh.balance,
                    severity="review",
                )
            )
            continue

        try:
            fresh.share_link = invoice_share_link(qbo, fresh.id)
        except QboError as exc:
            LOG.info("no share link for invoice %s: %s", fresh.id, exc)

        subject, body = render(decision.stage.template, fresh, today, settings)
        result.reminders.append(
            ReminderCandidate(
                ref=reminder_refs.next(),
                invoice=fresh,
                stage_id=decision.stage.id,
                stage_label=decision.stage.label,
                days_past_due=(today - due_date_for(fresh, client, settings)).days,
                to=recipient,
                cc=list(decision.stage.cc),
                subject=subject,
                body=body,
            )
        )

    _add_suppression_flag(result, customer_name, suppressed, suppression_decision, flag_refs)


def _add_suppression_flag(
    result: RunResult,
    customer_name: str,
    invoices: list[Invoice],
    decision,
    flag_refs: RefCounter,
) -> None:
    """One line per client under a hold, listing what is waiting behind it."""
    if not invoices:
        return
    total = sum((inv.balance for inv in invoices), ZERO)
    labels = ", ".join(inv.label for inv in invoices[:6])
    noun = "invoice" if len(invoices) == 1 else "invoices"
    result.flags.append(
        Flag(
            ref=flag_refs.next(),
            kind="reminders_suppressed",
            customer_name=customer_name,
            reason=(
                f"{decision.reason} {len(invoices)} past-due {noun} waiting: {labels}."
            ),
            recommended_action=decision.action_note or "Human outreach.",
            amount=total,
            severity="review",
        )
    )


def _add_missing_terms_flag(
    result: RunResult, missing: list[str], flag_refs: RefCounter
) -> None:
    """One line for every client with no contract on file, not one line each."""
    if not missing:
        return
    shown = ", ".join(sorted(missing)[:12])
    noun = "client" if len(missing) == 1 else "clients"
    more = f" and {len(missing) - 12} more" if len(missing) > 12 else ""
    result.flags.append(
        Flag(
            ref=flag_refs.next(),
            kind="no_contract_terms",
            customer_name=f"{len(missing)} {noun}",
            reason=(
                f"No contract terms on file, so their invoices were not validated: "
                f"{shown}{more}."
            ),
            recommended_action="Add their rate structures to config/clients.yaml as they come up.",
            severity="review",
        )
    )


def _add_contract_change_flags(
    config: Config, result: RunResult, today: date, flag_refs: RefCounter
) -> None:
    changes = upcoming_contract_changes(
        config.clients, today, config.settings.contract_change_lookahead_days
    )
    for client, kind, when in changes:
        days = (when - today).days
        result.flags.append(
            Flag(
                ref=flag_refs.next(),
                kind="contract_change_upcoming",
                customer_name=client.name,
                reason=f"{kind.capitalize()} on {when} — {days} days out.",
                recommended_action=(
                    "Change the billing before the 1st so the next invoice goes out correct."
                ),
                severity="urgent" if days <= 10 else "review",
            )
        )
