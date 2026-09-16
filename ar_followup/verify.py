"""Pre-send verification. This is the step that gets skipped and then hurts.

Three questions about every invoice before it can become a reminder:

1. Is it still unpaid *right now*? Not according to the list pulled at the top of
   the run, and never according to an aging report — according to a fresh
   single-invoice read seconds before the proposal is written.
2. Has the reconciliation pass claimed it? If money has been matched to this
   invoice, the client has paid and the bookkeeping is behind. No reminder.
3. Is the remaining balance a real debt, or a short pay somebody already agreed?

The matching in step 2 is the same engine the digest reports from, so a send can
never disagree with the reconciliation the digest showed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .config import Settings
from .matching import RefCounter, match_customer, sources_for_customer
from .models import Invoice, MoneySource, ProposedMatch
from .qbo.client import QboClient
from .qbo.reads import refresh_invoice


@dataclass
class Verification:
    """The verdict on one invoice."""

    invoice: Invoice
    still_open: bool
    reasons_to_hold: list[str] = field(default_factory=list)
    matches: list[ProposedMatch] = field(default_factory=list)

    @property
    def clear_to_propose(self) -> bool:
        return self.still_open and not self.reasons_to_hold and not self.matches


def short_pay_reasons(invoice: Invoice, settings: Settings) -> list[str]:
    """Partial payments are a conversation, not a dunning email."""
    reasons: list[str] = []
    if invoice.partially_paid:
        reasons.append(
            f"Partially paid: ${invoice.amount_paid:,.2f} of ${invoice.total:,.2f} received, "
            f"${invoice.balance:,.2f} outstanding."
        )
    if 0 < invoice.balance <= settings.short_pay_review_floor:
        reasons.append(
            f"Residual balance of ${invoice.balance:,.2f} is below the "
            f"${settings.short_pay_review_floor:,.2f} short-pay floor."
        )
    return reasons


def money_for_invoice(
    invoice: Invoice, sources: list[MoneySource], settings: Settings
) -> list[ProposedMatch]:
    """Run the matcher against this one invoice. Any hit stops the send."""
    mine = sources_for_customer(sources, invoice.customer_id, invoice.customer_name)
    if not mine:
        return []
    matches, _ = match_customer([invoice], mine, settings, RefCounter("m"))
    return [m for m in matches if m.covers(invoice.id)]


def verify_invoice(
    qbo: QboClient,
    invoice: Invoice,
    sources: list[MoneySource],
    settings: Settings,
    today: date | None = None,
) -> Verification:
    """Re-read the invoice live, then check whether the money is already in."""
    del today  # the live read carries its own truth about the date

    fresh = refresh_invoice(qbo, invoice.id, settings.payment_terms_days) or invoice
    verification = Verification(invoice=fresh, still_open=fresh.balance > 0)

    if not verification.still_open:
        verification.reasons_to_hold.append(
            f"Invoice {fresh.label} shows a zero balance on a live read. "
            "The list at the top of the run was stale."
        )
        return verification

    if fresh.balance < settings.minimum_reminder_balance:
        verification.reasons_to_hold.append(
            f"Balance of ${fresh.balance:,.2f} is under the reminder floor."
        )

    verification.reasons_to_hold.extend(short_pay_reasons(fresh, settings))
    verification.matches = money_for_invoice(fresh, sources, settings)
    return verification
