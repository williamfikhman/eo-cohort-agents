"""Pre-send verification. This is the step that gets skipped and then hurts.

Two questions are asked about every invoice before it can become a reminder:

1. Is it still unpaid *right now*? Not according to the list pulled at the top of
   the run, and never according to an aging report — according to a fresh
   single-invoice read seconds before the proposal is written.
2. Did the money already arrive without being applied? A Chase bank-feed deposit
   or a QuickBooks Payments auto-post can sit unapplied for days while the
   invoice still shows a balance. We came within one run of dunning Troomy and
   OcuSoft on the day they paid.

Any candidate match at all means the invoice goes to the review queue as a
possible unapplied payment. It never means "send anyway", and it never means
"apply the payment" — deposit-to-invoice matching has real double-payment risk,
so a human applies it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .config import Settings
from .models import Deposit, Invoice, Payment, UnappliedCandidate
from .qbo.client import QboClient
from .qbo.reads import refresh_invoice


@dataclass
class Verification:
    """The verdict on one invoice."""

    invoice: Invoice
    still_open: bool
    reasons_to_hold: list[str] = field(default_factory=list)
    unapplied: list[UnappliedCandidate] = field(default_factory=list)

    @property
    def clear_to_propose(self) -> bool:
        return self.still_open and not self.reasons_to_hold and not self.unapplied


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def amounts_match(left: Decimal, right: Decimal, settings: Settings) -> str | None:
    """'exact', 'near', or None. Tolerances are wide on purpose."""
    if left <= 0 or right <= 0:
        return None
    if left == right:
        return "exact"
    gap = abs(left - right)
    pct_allowance = (max(left, right) * settings.unapplied_tolerance_pct) / Decimal("100")
    if gap <= max(pct_allowance, settings.unapplied_tolerance_abs):
        return "near"
    return None


def _mentions_invoice(text: str, invoice: Invoice) -> bool:
    doc = (invoice.doc_number or "").strip()
    return bool(doc) and doc.lower() in text.lower()


def unapplied_payment_candidates(
    invoice: Invoice,
    payments: list[Payment],
    deposits: list[Deposit],
    settings: Settings,
    ref_start: int = 1,
) -> list[UnappliedCandidate]:
    """Money that looks like it was meant for this invoice but is not on it."""
    candidates: list[UnappliedCandidate] = []
    counter = ref_start

    def add(**kwargs) -> None:
        nonlocal counter
        candidates.append(UnappliedCandidate(ref=f"M{counter}", invoice=invoice, **kwargs))
        counter += 1

    for payment in payments:
        if payment.customer_id and payment.customer_id != invoice.customer_id:
            continue
        if invoice.id in payment.applied_invoice_ids:
            continue

        # Unapplied money sitting on the customer's account.
        if payment.unapplied > 0:
            confidence = amounts_match(payment.unapplied, invoice.balance, settings)
            if confidence:
                add(
                    source="payment",
                    source_id=payment.id,
                    source_date=payment.txn_date,
                    amount=payment.unapplied,
                    account=payment.deposit_account or "unapplied on account",
                    confidence=confidence,
                    note=(
                        f"Payment {payment.reference or payment.id} dated {payment.txn_date} "
                        f"has ${payment.unapplied:,.2f} unapplied, {_article(confidence)} "
                        f"{confidence} match for this balance."
                    ),
                )
                continue

        # Right amount, applied somewhere else, or referencing this invoice by number.
        confidence = amounts_match(payment.total, invoice.balance, settings)
        if confidence and not payment.applied_invoice_ids:
            add(
                source="payment",
                source_id=payment.id,
                source_date=payment.txn_date,
                amount=payment.total,
                account=payment.deposit_account or "unknown account",
                confidence=confidence,
                note=(
                    f"Payment {payment.reference or payment.id} of ${payment.total:,.2f} on "
                    f"{payment.txn_date} is not applied to any invoice."
                ),
            )
        elif _mentions_invoice(payment.haystack, invoice) and invoice.id not in payment.applied_invoice_ids:
            add(
                source="payment",
                source_id=payment.id,
                source_date=payment.txn_date,
                amount=payment.total,
                account=payment.deposit_account or "unknown account",
                confidence="near",
                note=(
                    f"Payment {payment.reference or payment.id} references invoice "
                    f"{invoice.label} but is applied elsewhere."
                ),
            )

    customer_key = (invoice.customer_name or "").lower()
    for deposit in deposits:
        named = [n.lower() for n in deposit.customer_names]
        # A deposit line already tied to a different customer is not our money.
        if named and customer_key and not any(customer_key in n or n in customer_key for n in named):
            continue

        amounts = [deposit.total, *deposit.line_amounts]
        best = None
        for amount in amounts:
            confidence = amounts_match(amount, invoice.balance, settings)
            if confidence == "exact":
                best = (amount, confidence)
                break
            if confidence and best is None:
                best = (amount, confidence)
        if not best:
            continue

        amount, confidence = best
        add(
            source="deposit",
            source_id=deposit.id,
            source_date=deposit.txn_date,
            amount=amount,
            account=deposit.account_name,
            confidence=confidence,
            note=(
                f"{deposit.account_name or 'Bank'} deposit on {deposit.txn_date} includes "
                f"${amount:,.2f}, {_article(confidence)} {confidence} match for this balance"
                + (f" (line names {', '.join(deposit.customer_names[:3])})" if deposit.customer_names else "")
                + "."
            ),
        )

    return candidates


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


def verify_invoice(
    qbo: QboClient,
    invoice: Invoice,
    payments: list[Payment],
    deposits: list[Deposit],
    settings: Settings,
    ref_start: int = 1,
    today: date | None = None,
) -> Verification:
    """Re-read the invoice live, then look for money that already arrived."""
    del today  # kept for signature stability; the live read carries the date

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
    verification.unapplied = unapplied_payment_candidates(
        fresh, payments, deposits, settings, ref_start=ref_start
    )
    return verification
