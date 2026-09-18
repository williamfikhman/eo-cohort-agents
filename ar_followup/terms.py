"""Validate every invoice against the client's contract terms.

This is the check that would have caught a client billed $3,000/mo + 8% for four
months after their contract stepped down to 10%-commission-only. That one cost
$7,328 in credits. The rule the agent follows now: an invoice whose shape does
not match the contract period it was issued under is William's problem, not the
client's — it never turns into a reminder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .config import ClientTerms, Settings
from .models import Invoice, InvoiceLine, money

PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
CENT = Decimal("0.01")


def pct(value: Decimal) -> str:
    """8.00 -> '8', 8.50 -> '8.5'. Rates read like rates in the digest."""
    trimmed = value.normalize()
    return f"{trimmed:f}"


@dataclass
class TermFinding:
    kind: str
    reason: str
    action: str
    severity: str = "review"
    amount: Decimal | None = None


def _matches(line: InvoiceLine, patterns: list[str]) -> bool:
    return any(p in line.haystack for p in patterns)


def classify_lines(
    invoice: Invoice, settings: Settings
) -> tuple[list[InvoiceLine], list[InvoiceLine], list[InvoiceLine]]:
    """Split invoice lines into (flat fee, commission, everything else)."""
    flat, commission, other = [], [], []
    for line in invoice.lines:
        if _matches(line, settings.flat_fee_patterns):
            flat.append(line)
        elif _matches(line, settings.commission_patterns):
            commission.append(line)
        else:
            other.append(line)
    return flat, commission, other


def implied_commission_pct(line: InvoiceLine) -> Decimal | None:
    """Recover the commission rate a line was billed at, if it can be recovered.

    Three shapes turn up in our invoices: the rate written into the description
    ("8% of net sales"), a unit price stored as a percentage (8.0), and a unit
    price stored as a fraction (0.08) against a sales-volume quantity.
    """
    match = PERCENT_RE.search(f"{line.description} {line.item_name}")
    if match:
        return money(match.group(1))

    if line.rate is not None and line.qty and line.qty > 0:
        if 0 < line.rate < 1:
            return (line.rate * 100).quantize(CENT)
        if 1 <= line.rate <= 100:
            return money(line.rate)

    if line.qty and line.qty > 0 and line.amount > 0:
        # Fall back to amount / sales base, which is what a commission line is.
        return (line.amount / line.qty * 100).quantize(CENT)

    return None


def validate_invoice(
    invoice: Invoice, client: ClientTerms | None, settings: Settings
) -> list[TermFinding]:
    """Every way this invoice disagrees with the contract on file."""
    findings: list[TermFinding] = []

    if client is None:
        return [
            TermFinding(
                kind="no_contract_terms",
                reason=(
                    f"{invoice.customer_name} has no entry in clients.yaml, so "
                    f"invoice {invoice.label} for ${invoice.total:,.2f} was not "
                    "validated against any contract."
                ),
                action="Add the client's rate structure to config/clients.yaml.",
            )
        ]

    period = client.term_for(invoice.txn_date)
    if period is None:
        findings.append(
            TermFinding(
                kind="terms_gap",
                reason=(
                    f"Invoice {invoice.label} is dated {invoice.txn_date} which falls "
                    "outside every contract period on file."
                ),
                action="Close the gap in clients.yaml or confirm the invoice date is right.",
                severity="urgent",
            )
        )
        return findings

    if not period.validatable:
        findings.append(
            TermFinding(
                kind="terms_unverified",
                reason=(
                    f"Contract period from {period.effective_from} has no rate on file, "
                    f"so invoice {invoice.label} could not be checked."
                ),
                action="Fill in flat_fee_monthly and commission_pct in clients.yaml.",
            )
        )
        return findings

    flat_lines, commission_lines, _ = classify_lines(invoice, settings)
    billed_flat = sum((line.amount for line in flat_lines), Decimal("0.00"))

    # --- flat fee -----------------------------------------------------------
    expected_flat = period.flat_fee_monthly
    if expected_flat is not None:
        if expected_flat == 0 and billed_flat > 0:
            findings.append(
                TermFinding(
                    kind="flat_fee_after_step_down",
                    reason=(
                        f"Invoice {invoice.label} bills a ${billed_flat:,.2f} flat fee, but "
                        f"the contract period starting {period.effective_from} has no flat fee."
                    ),
                    action=(
                        "Hold the invoice, correct it in QBO, and check earlier months for "
                        "the same error before crediting."
                    ),
                    severity="urgent",
                    amount=billed_flat,
                )
            )
        elif expected_flat > 0 and not flat_lines:
            findings.append(
                TermFinding(
                    kind="flat_fee_missing",
                    reason=(
                        f"Contract calls for a ${expected_flat:,.2f} monthly flat fee and "
                        f"invoice {invoice.label} has none."
                    ),
                    action="Confirm whether the fee was waived or the invoice is short.",
                    amount=expected_flat,
                )
            )
        elif expected_flat > 0 and billed_flat != expected_flat:
            findings.append(
                TermFinding(
                    kind="flat_fee_mismatch",
                    reason=(
                        f"Invoice {invoice.label} bills ${billed_flat:,.2f} in flat fee against "
                        f"a contracted ${expected_flat:,.2f}."
                    ),
                    action="Correct the invoice before it goes out, or update the terms.",
                    severity="urgent",
                    amount=(billed_flat - expected_flat),
                )
            )

    # --- commission ---------------------------------------------------------
    expected_pct = period.commission_pct
    if expected_pct is not None:
        if expected_pct > 0 and not commission_lines:
            findings.append(
                TermFinding(
                    kind="commission_missing",
                    reason=(
                        f"Contract is {pct(expected_pct)}% of {period.commission_basis or 'sales'} "
                        f"and invoice {invoice.label} has no commission line."
                    ),
                    action="Check whether the sales report landed before billing ran.",
                )
            )
        for line in commission_lines:
            billed_pct = implied_commission_pct(line)
            if billed_pct is None:
                findings.append(
                    TermFinding(
                        kind="commission_unreadable",
                        reason=(
                            f"Commission line on invoice {invoice.label} does not state a rate, "
                            f"so it could not be checked against the contracted {pct(expected_pct)}%."
                        ),
                        action="Put the rate in the line description so it can be verified.",
                    )
                )
            elif billed_pct != expected_pct:
                findings.append(
                    TermFinding(
                        kind="commission_rate_mismatch",
                        reason=(
                            f"Invoice {invoice.label} bills commission at {pct(billed_pct)}% against a "
                            f"contracted {pct(expected_pct)}% (period from {period.effective_from})."
                        ),
                        action="Hold the invoice and reconcile the rate before any reminder.",
                        severity="urgent",
                        amount=line.amount,
                    )
                )

    # --- currency and FX ----------------------------------------------------
    expected_currency = client.currency or settings.currency
    if invoice.currency and expected_currency and invoice.currency != expected_currency:
        findings.append(
            TermFinding(
                kind="currency_mismatch",
                reason=(
                    f"Invoice {invoice.label} is in {invoice.currency}; the contract is in "
                    f"{expected_currency}."
                ),
                action="Confirm the billing currency before sending anything.",
                severity="urgent",
            )
        )
    if client.fx_rate is not None and invoice.exchange_rate is not None:
        if money(invoice.exchange_rate) != money(client.fx_rate):
            findings.append(
                TermFinding(
                    kind="fx_rate_mismatch",
                    reason=(
                        f"Invoice {invoice.label} used an FX rate of {invoice.exchange_rate}; "
                        f"the contract fixes {client.fx_rate}."
                    ),
                    action="Re-rate the invoice at the contracted rate.",
                    severity="urgent",
                )
            )

    # --- sanity band --------------------------------------------------------
    if client.expected_total_range:
        low, high = client.expected_total_range
        if not (low <= invoice.total <= high):
            findings.append(
                TermFinding(
                    kind="total_out_of_band",
                    reason=(
                        f"Invoice {invoice.label} totals ${invoice.total:,.2f}, outside this "
                        f"client's expected ${low:,.2f}-${high:,.2f}."
                    ),
                    action="Eyeball the invoice before it is chased.",
                    amount=invoice.total,
                )
            )

    return findings


def overbilling_estimate(
    invoices: list[Invoice], client: ClientTerms, settings: Settings
) -> Decimal:
    """Total flat fee billed under periods whose contract has no flat fee.

    Run across every invoice the agent can see for a client, this is the size of
    the credit owed — the number that came out as $7,328 the hard way.
    """
    total = Decimal("0.00")
    for invoice in invoices:
        period = client.term_for(invoice.txn_date)
        if period is None or period.flat_fee_monthly is None or period.flat_fee_monthly != 0:
            continue
        flat_lines, _, _ = classify_lines(invoice, settings)
        total += sum((line.amount for line in flat_lines), Decimal("0.00"))
    return total


def credit_drawdown_findings(
    invoices: list[Invoice], client: ClientTerms
) -> list[TermFinding]:
    """A client with a credit on file should see it netted against new invoices."""
    if not client.credit_balance or client.credit_balance.amount <= 0:
        return []

    as_of = client.credit_balance.as_of
    newer = [
        inv for inv in invoices
        if as_of is None or inv.txn_date >= as_of
    ]
    if not newer:
        return []

    unapplied = [inv for inv in newer if not any(t == "CreditMemo" for t, _ in inv.linked_txns)]
    if not unapplied:
        return []

    labels = ", ".join(inv.label for inv in unapplied[:5])
    noun = "invoice" if len(unapplied) == 1 else "invoices"
    return [
        TermFinding(
            kind="credit_not_drawing_down",
            reason=(
                f"{client.name} carries a ${client.credit_balance.amount:,.2f} credit "
                f"(as of {as_of or 'unknown'}) and {noun} {labels} "
                f"{'has' if len(unapplied) == 1 else 'have'} no credit memo applied."
            ),
            action="Apply the credit in QBO before any of these invoices is chased.",
            severity="urgent",
            amount=client.credit_balance.amount,
        )
    ]


def upcoming_contract_changes(
    clients: list[ClientTerms], today: date, lookahead_days: int
) -> list[tuple[ClientTerms, str, date]]:
    """Step-downs and sunsets landing inside the lookahead window.

    Billing changes before the invoice goes out, not after.
    """
    horizon = today + timedelta(days=lookahead_days)
    upcoming: list[tuple[ClientTerms, str, date]] = []
    for client in clients:
        for when in client.step_down_dates:
            if today <= when <= horizon:
                upcoming.append((client, "rate step-down", when))
        if client.sunset_date and today <= client.sunset_date <= horizon:
            upcoming.append((client, "contract sunset", client.sunset_date))
    upcoming.sort(key=lambda row: row[2])
    return upcoming
