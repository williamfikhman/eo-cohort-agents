"""Post confirmed matches to QuickBooks.

This runs after William replies CONFIRM on a match. It is the only path in the
build that changes anything in QuickBooks, and it is deliberately narrow: it
allocates a Payment that already exists to the invoice it already belongs to.

What it refuses to do, every time:

  * apply a match William has not confirmed
  * apply a match sourced from a bank deposit rather than a Payment, because
    creating a Payment for cash already sitting in the register books it twice
  * apply anything if the invoice balance or the payment's unapplied amount has
    moved since the match was proposed
  * apply the same money twice, checked against the ledger and against a stamp
    written into the Payment itself

Every skip is reported with the reason and, where there is one, the manual fix.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from .config import Config
from .ledger import Ledger
from .models import money
from .qbo.auth import QboAuth
from .qbo.client import QboClient, QboError
from .qbo.reads import refresh_invoice
from .qbo.writer import QboWriter, QboWriteRefused

LOG = logging.getLogger("ar_followup.apply")

APPLIED = "applied"
SKIPPED = "skipped"
REFUSED = "refused"
FAILED = "failed"

DEPOSIT_FIX = (
    "bank-feed deposit with no payment behind it. Fix it in QuickBooks: "
    "Transactions, Bank transactions, find the deposit, undo the categorization, "
    "then Find match against the invoice."
)


@dataclass
class ApplyOutcome:
    ref: str
    customer: str
    invoice_label: str
    status: str
    detail: str
    amount: Decimal | None = None


def build_writer(config: Config, auth: QboAuth | None = None) -> QboWriter:
    settings = config.settings
    return QboWriter(
        auth=auth or QboAuth.from_env(),
        enabled=settings.apply_enabled,
        audit_stamp=settings.audit_stamp,
    )


def _payment_sources(match: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """Split a match's money into (payments, everything else)."""
    payments, other = [], []
    for source in match.get("sources") or []:
        (payments if source.get("kind") == "payment" else other).append(source)
    return payments, other


def apply_confirmed(
    config: Config,
    ledger: Ledger,
    digest_id: str | None = None,
    dry_run: bool = True,
    qbo: QboClient | None = None,
    writer: QboWriter | None = None,
    today: date | None = None,
) -> list[ApplyOutcome]:
    """Apply every confirmed, payment-sourced match in one digest."""
    settings = config.settings
    today = today or date.today()

    digest_id = digest_id or ledger.latest_digest_id()
    if not digest_id:
        return [ApplyOutcome("-", "-", "-", SKIPPED, "no digest has been produced yet")]
    digest = ledger.load_digest(digest_id)
    if digest is None:
        return [ApplyOutcome("-", "-", "-", SKIPPED, f"digest {digest_id} not found")]

    matches = digest.get("matches") or []
    if not matches:
        return [ApplyOutcome("-", "-", "-", SKIPPED, f"digest {digest_id} proposed no matches")]

    decisions = ledger.match_decisions(digest_id)
    confirmed = [
        m for m in matches
        if decisions.get(str(m["ref"]), {}).get("decision") == "confirmed"
    ]
    if not confirmed:
        return [
            ApplyOutcome(
                "-", "-", "-", SKIPPED,
                "no match in this digest has been confirmed; nothing to post",
            )
        ]

    if not settings.apply_enabled and not dry_run:
        return [
            ApplyOutcome(
                "-", "-", "-", REFUSED,
                "posting is off. Set payment_application.enabled to true in "
                "config/settings.yaml when you are ready.",
            )
        ]

    qbo = qbo or QboClient(QboAuth.from_env())
    writer = writer or build_writer(config)
    already = ledger.applied_pairs()

    outcomes: list[ApplyOutcome] = []
    applied_count = 0

    for match in confirmed:
        ref = str(match["ref"])
        customer = str(match.get("customer_name") or "")
        labels = str(match.get("invoice_labels") or "")
        payments, other = _payment_sources(match)

        if other:
            kinds = ", ".join(sorted({str(s.get("kind")) for s in other}))
            detail = DEPOSIT_FIX if "deposit" in kinds else f"{kinds} sources are applied by hand"
            outcomes.append(ApplyOutcome(ref, customer, labels, SKIPPED, detail))
            continue

        for invoice_id, amount in _allocation(match, qbo, settings):
            if amount is None:
                outcomes.append(
                    ApplyOutcome(ref, customer, labels, SKIPPED, str(invoice_id))
                )
                continue

            invoice = refresh_invoice(qbo, str(invoice_id), settings.payment_terms_days)
            if invoice is None:
                outcomes.append(
                    ApplyOutcome(ref, customer, labels, FAILED, "invoice could not be re-read")
                )
                continue
            if invoice.balance <= 0:
                outcomes.append(
                    ApplyOutcome(
                        ref, customer, invoice.label, SKIPPED,
                        "invoice already shows a zero balance; someone applied it",
                    )
                )
                continue
            if amount > invoice.balance:
                outcomes.append(
                    ApplyOutcome(
                        ref, customer, invoice.label, REFUSED,
                        f"would allocate ${amount:,.2f} against a ${invoice.balance:,.2f} "
                        "balance; the invoice changed since the match was proposed",
                    )
                )
                continue
            if amount > settings.max_amount_per_application:
                outcomes.append(
                    ApplyOutcome(
                        ref, customer, invoice.label, REFUSED,
                        f"${amount:,.2f} is over the "
                        f"${settings.max_amount_per_application:,.2f} per-application cap",
                        amount,
                    )
                )
                continue

            for source in payments:
                payment_id = str(source["id"])
                if (payment_id, str(invoice_id)) in already:
                    outcomes.append(
                        ApplyOutcome(
                            ref, customer, invoice.label, SKIPPED,
                            f"payment {payment_id} was already applied to this invoice",
                        )
                    )
                    continue

                if applied_count >= settings.max_applications_per_run:
                    outcomes.append(
                        ApplyOutcome(
                            ref, customer, invoice.label, REFUSED,
                            f"hit the {settings.max_applications_per_run}-application cap "
                            "for one run; re-run to continue",
                        )
                    )
                    return outcomes

                share = min(amount, money(source["amount"]))
                if dry_run:
                    outcomes.append(
                        ApplyOutcome(
                            ref, customer, invoice.label, SKIPPED,
                            f"dry run: would apply ${share:,.2f} of payment {payment_id}",
                            share,
                        )
                    )
                    continue

                try:
                    live = qbo.read_entity("Payment", payment_id)
                    if live is None:
                        raise QboError(f"payment {payment_id} could not be re-read")
                    result = writer.apply_payment_to_invoice(live, str(invoice_id), share)
                except QboWriteRefused as exc:
                    outcomes.append(
                        ApplyOutcome(ref, customer, invoice.label, REFUSED, str(exc), share)
                    )
                    continue
                except QboError as exc:
                    outcomes.append(
                        ApplyOutcome(ref, customer, invoice.label, FAILED, str(exc), share)
                    )
                    continue

                applied_count += 1
                already.add((payment_id, str(invoice_id)))
                ledger.record_application(
                    digest_id=digest_id,
                    ref=ref,
                    payment_id=payment_id,
                    invoice_id=str(invoice_id),
                    invoice_label=invoice.label,
                    customer=customer,
                    amount=share,
                    qbo_response_id=str((result.get("Payment") or {}).get("Id") or payment_id),
                )
                outcomes.append(
                    ApplyOutcome(
                        ref, customer, invoice.label, APPLIED,
                        f"${share:,.2f} of payment {payment_id} applied in QuickBooks",
                        share,
                    )
                )
                amount -= share
                if amount <= 0:
                    break

    return outcomes


def _allocation(match: dict[str, Any], qbo: QboClient, settings) -> list[tuple[str, Decimal | None]]:
    """How much of this match goes to each invoice it covers, oldest first."""
    invoice_ids = [str(i) for i in (match.get("invoice_ids") or [])]
    if not invoice_ids:
        return [("match names no invoice", None)]

    remaining = money(match.get("amount") or 0)
    out: list[tuple[str, Decimal | None]] = []
    for invoice_id in invoice_ids:
        if remaining <= 0:
            break
        invoice = refresh_invoice(qbo, invoice_id, settings.payment_terms_days)
        if invoice is None:
            out.append((invoice_id, None))
            continue
        share = min(remaining, invoice.balance)
        out.append((invoice_id, share))
        remaining -= share
    return out
