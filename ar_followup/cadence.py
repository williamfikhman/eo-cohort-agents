"""Which invoice is due for which reminder, and which one automation must not touch.

Cadence, counted from the due date (Net 15 unless the contract says otherwise):

  +3   friendly reminder with a link to the invoice
  +10  firmer follow-up, cc Angie
  +21  escalation — a personal call from Angie or William, automated email stops

Three conditions take an invoice out of automation entirely:

  * the client is in a dispute or a rate renegotiation (Kaaral, Rust Check today)
  * the client carries a credit balance (Rust Check, $6,026.67)
  * the client's AR is stacking across months, which is what silent churn looks
    like from the inside (Labeldaddy, Silko/CocoChoco, BeLAGU)

In all three the invoice still shows up in the digest. Suppressed never means
invisible — it means a human handles it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .config import ClientTerms, Settings, Stage
from .models import Invoice

PROPOSE = "propose"
ESCALATE = "escalate"
SUPPRESS = "suppress"
NONE = "none"


@dataclass
class CadenceDecision:
    action: str
    stage: Stage | None = None
    reason: str = ""
    action_note: str = ""

    @property
    def is_send(self) -> bool:
        return self.action == PROPOSE


def due_date_for(invoice: Invoice, client: ClientTerms | None, settings: Settings) -> date:
    """QBO's due date wins; a contract override only applies when QBO has none."""
    if invoice.due_date:
        return invoice.due_date
    days = (client.payment_terms_days if client and client.payment_terms_days else settings.payment_terms_days)
    from datetime import timedelta

    return invoice.txn_date + timedelta(days=days)


def stacked_ar(open_invoices: list[Invoice], settings: Settings) -> bool:
    """Two or more open invoices, and (by default) across different months."""
    if len(open_invoices) < settings.stacked_ar_threshold:
        return False
    if not settings.stacked_ar_distinct_months:
        return True
    return len({inv.period for inv in open_invoices}) >= settings.stacked_ar_threshold


def stage_for(days_past_due: int, settings: Settings) -> Stage | None:
    """The highest stage this invoice has reached."""
    reached = [s for s in settings.stages if days_past_due >= s.days_past_due]
    return reached[-1] if reached else None


def decide(
    invoice: Invoice,
    client: ClientTerms | None,
    settings: Settings,
    today: date,
    sent_stage_ids: set[str],
    client_open_invoices: list[Invoice],
) -> CadenceDecision:
    """What the agent proposes for one invoice. Nothing here sends anything."""
    if client and client.reminders_suppressed:
        return CadenceDecision(
            SUPPRESS,
            reason=client.suppression_reason or "Reminders are suppressed for this client.",
            action_note="Human outreach only until the status in clients.yaml changes.",
        )

    if client and client.credit_balance and client.credit_balance.amount > 0:
        return CadenceDecision(
            SUPPRESS,
            reason=(
                f"{client.name} carries a ${client.credit_balance.amount:,.2f} credit balance; "
                "reminders stay off until it is exhausted."
            ),
            action_note="Verify the credit is netting against new invoices.",
        )

    if stacked_ar(client_open_invoices, settings):
        months = sorted({f"{y}-{m:02d}" for y, m in (i.period for i in client_open_invoices)})
        total = sum((i.balance for i in client_open_invoices), start=invoice.balance * 0)
        return CadenceDecision(
            SUPPRESS,
            reason=(
                f"{len(client_open_invoices)} open invoices totalling ${total:,.2f} stacked "
                f"across {', '.join(months)} — churn / collection risk, not a dunning case."
            ),
            action_note="Personal outreach from Angie or William before anything automated.",
        )

    days = (today - due_date_for(invoice, client, settings)).days
    if invoice.balance < settings.minimum_reminder_balance:
        return CadenceDecision(NONE, reason="Balance is below the reminder floor.")

    stage = stage_for(days, settings)
    if stage is None:
        return CadenceDecision(NONE, reason=f"Not yet due for a reminder ({days} days past due).")

    if stage.id in sent_stage_ids:
        return CadenceDecision(NONE, reason=f"Stage '{stage.id}' already sent for this invoice.")

    # Once an invoice has escalated, automated email is over for it, even if an
    # earlier stage was somehow never sent.
    escalation_stages = {s.id for s in settings.stages if not s.sends_email}
    if sent_stage_ids & escalation_stages:
        return CadenceDecision(
            NONE, reason="Already escalated; automated email is stopped for this invoice."
        )

    if not stage.sends_email:
        return CadenceDecision(
            ESCALATE,
            stage=stage,
            reason=f"{days} days past due — past the {stage.days_past_due}-day escalation line.",
            action_note="Personal call from Angie or William. Automated email stops here.",
        )

    return CadenceDecision(PROPOSE, stage=stage, reason=f"{days} days past due.")
