"""Reconciliation. This runs before anything is said about AR.

The premise: an invoice with a balance in QuickBooks is not evidence that a
client owes money. It is evidence that no payment has been *applied* to it. Those
are different facts, and treating the first as the second is how a client who
paid gets a dunning email.

So every run starts by putting money next to invoices. Three places money hides:

  * a customer payment with an unapplied amount sitting on account
  * a bank-feed deposit line nobody matched to an invoice, which is worse when
    it was categorized straight to an income account — then the same dollars are
    counted twice, once as revenue on the deposit and once as AR on the invoice
  * an open credit memo

Matching runs best-evidence-first and never spends the same dollar twice:

  1. referenced  a payment citing the invoice number, for about the right money
  2. exact       one source equal to one invoice balance
  3. split       several sources adding up to one invoice balance
  4. lump        one source covering several invoice balances
  5. near        the same shapes, within tolerance

Strategy 3 is the one that had been missing, and it is the common case: a client
who pays a $9,765.14 invoice in two transfers matches nothing on amount, so
QuickBooks leaves both deposits for a human and the invoice stays open forever.

Nothing here writes to QuickBooks. Every match is a proposal for the digest.
"""

from __future__ import annotations

import itertools
import logging
from datetime import date
from decimal import Decimal

from .config import Settings
from .models import (
    CreditMemo,
    Deposit,
    Invoice,
    MoneySource,
    Payment,
    ProposedMatch,
    UnexplainedMoney,
    ZERO,
)

LOG = logging.getLogger("ar_followup.matching")

# Confidence by strategy. A match a human should be able to approve at a glance
# is "high"; anything relying on tolerance is not.
CONFIDENCE = {
    "referenced": "high",
    "exact": "high",
    "split": "high",
    "lump": "medium",
    "near": "medium",
    "near_split": "low",
}


class RefCounter:
    def __init__(self, prefix: str):
        self.prefix = prefix
        self.n = 0

    def next(self) -> str:
        self.n += 1
        return f"{self.prefix}{self.n}"


# --- building the money side ------------------------------------------------


def _is_income_account(account_name: str, hints: list[str]) -> bool:
    haystack = (account_name or "").lower()
    return any(hint in haystack for hint in hints)


def payment_sources(payments: list[Payment]) -> list[MoneySource]:
    sources: list[MoneySource] = []
    for payment in payments:
        available = payment.unapplied
        note = ""
        if available <= 0 and not payment.applied_invoice_ids and payment.total > 0:
            # QBO says nothing is unapplied, yet the payment is on no invoice.
            # Contradictory, so surface it rather than let the money vanish.
            available = payment.total
            note = "payment is applied to no invoice despite showing nothing unapplied"
        if available <= 0:
            continue
        sources.append(
            MoneySource(
                kind="payment",
                id=payment.id,
                txn_date=payment.txn_date,
                amount=available,
                account=payment.deposit_account or "unapplied on account",
                customer_id=payment.customer_id,
                customer_name=payment.customer_name,
                reference=payment.reference,
                note=note or payment.private_note,
            )
        )
    return sources


def deposit_sources(deposits: list[Deposit], settings: Settings) -> list[MoneySource]:
    sources: list[MoneySource] = []
    for deposit in deposits:
        for index, line in enumerate(deposit.loose_lines, start=1):
            if line.amount <= 0:
                continue
            sources.append(
                MoneySource(
                    kind="deposit",
                    id=f"{deposit.id}:{index}" if len(deposit.lines) > 1 else deposit.id,
                    txn_date=deposit.txn_date,
                    amount=line.amount,
                    account=deposit.account_name,
                    customer_name=line.entity_name,
                    reference=deposit.id,
                    note=(deposit.private_note or line.account_name),
                    posted_to_income=_is_income_account(
                        line.account_name, settings.income_account_hints
                    ),
                )
            )
    return sources


def credit_sources(memos: list[CreditMemo]) -> list[MoneySource]:
    return [
        MoneySource(
            kind="credit_memo",
            id=memo.id,
            txn_date=memo.txn_date,
            amount=memo.remaining,
            account="credit on account",
            customer_id=memo.customer_id,
            customer_name=memo.customer_name,
            reference=memo.doc_number,
        )
        for memo in memos
        if memo.remaining > 0
    ]


def collect_sources(
    payments: list[Payment],
    deposits: list[Deposit],
    memos: list[CreditMemo],
    settings: Settings,
) -> list[MoneySource]:
    """Every dollar that has arrived and is not yet sitting on an invoice."""
    return (
        payment_sources(payments)
        + deposit_sources(deposits, settings)
        + credit_sources(memos)
    )


def sources_for_customer(
    sources: list[MoneySource], customer_id: str, customer_name: str
) -> list[MoneySource]:
    """Id match where QBO gave us one, name match for loose deposit lines.

    A deposit line with no customer on it belongs to nobody in particular, so it
    stays in the pool for every customer and is resolved by amount instead.
    """
    needle = (customer_name or "").lower().strip()
    mine: list[MoneySource] = []
    for source in sources:
        if source.customer_id and customer_id and source.customer_id == str(customer_id):
            mine.append(source)
            continue
        if source.customer_id and customer_id and source.customer_id != str(customer_id):
            continue
        name = (source.customer_name or "").lower().strip()
        if not name:
            mine.append(source)  # unattributed money, decided on amount alone
        elif needle and (needle in name or name in needle):
            mine.append(source)
    return mine


# --- comparing amounts ------------------------------------------------------


def within_tolerance(left: Decimal, right: Decimal, settings: Settings) -> bool:
    gap = abs(left - right)
    allowance = (max(left, right) * settings.near_tolerance_pct) / Decimal("100")
    return gap <= max(allowance, settings.near_tolerance_abs)


def _cites(source: MoneySource, invoice: Invoice) -> bool:
    doc = (invoice.doc_number or "").strip()
    return bool(doc) and doc.lower() in source.haystack


def _total(items) -> Decimal:
    return sum((item.amount if hasattr(item, "amount") else item.balance for item in items), ZERO)


# --- the matcher ------------------------------------------------------------


def match_customer(
    invoices: list[Invoice],
    sources: list[MoneySource],
    settings: Settings,
    refs: RefCounter,
) -> tuple[list[ProposedMatch], list[MoneySource]]:
    """Propose matches for one customer. Returns (matches, money left over).

    Greedy and best-first: a dollar consumed by a high-confidence match is never
    offered to a weaker one, and an invoice covered once is not covered again.
    """
    open_invoices = sorted(
        [inv for inv in invoices if inv.balance > 0], key=lambda i: i.txn_date
    )
    pool = sorted([s for s in sources if s.amount > 0], key=lambda s: s.txn_date)
    # Bound the combinatorics. A customer with a dozen loose items is a
    # reconciliation problem for a person, not a subset-sum problem for a cron.
    pool = pool[: settings.max_source_pool]

    matches: list[ProposedMatch] = []
    used_sources: set[int] = set()
    used_invoices: set[str] = set()

    def available() -> list[MoneySource]:
        return [s for i, s in enumerate(pool) if i not in used_sources]

    def consume(chosen: list[MoneySource], invs: list[Invoice], strategy: str, why: str) -> None:
        for source in chosen:
            used_sources.add(pool.index(source))
        for invoice in invs:
            used_invoices.add(invoice.id)
        matches.append(
            ProposedMatch(
                ref=refs.next(),
                invoices=list(invs),
                sources=list(chosen),
                amount=_total(chosen),
                strategy=strategy,
                confidence=CONFIDENCE.get(strategy, "low"),
                rationale=why,
            )
        )

    def pending_invoices() -> list[Invoice]:
        return [inv for inv in open_invoices if inv.id not in used_invoices]

    # 1. A payment that names the invoice. Strongest evidence there is.
    for invoice in pending_invoices():
        hit = next(
            (s for s in available() if _cites(s, invoice) and within_tolerance(s.amount, invoice.balance, settings)),
            None,
        )
        if hit:
            consume(
                [hit],
                [invoice],
                "referenced",
                f"{hit.label} names invoice {invoice.label} and is within tolerance of its "
                f"${invoice.balance:,.2f} balance.",
            )

    # 2. One source, one invoice, to the cent.
    for invoice in pending_invoices():
        hit = next((s for s in available() if s.amount == invoice.balance), None)
        if hit:
            consume(
                [hit],
                [invoice],
                "exact",
                f"{hit.label} of ${hit.amount:,.2f} on {hit.txn_date} is the exact balance of "
                f"invoice {invoice.label}.",
            )

    # 3. Several sources adding up to one invoice. The split-payment case.
    for invoice in pending_invoices():
        combo = _subset_summing_to(
            available(), invoice.balance, settings.max_sources_per_match, exact=True
        )
        if combo:
            consume(
                combo,
                [invoice],
                "split",
                f"{len(combo)} payments totalling ${_total(combo):,.2f} "
                f"({', '.join(f'${s.amount:,.2f} on {s.txn_date}' for s in combo)}) "
                f"add up to invoice {invoice.label} exactly.",
            )

    # 4. One source covering several invoices. A client clearing two months at once.
    for source in available():
        combo = _invoice_subset_summing_to(
            pending_invoices(), source.amount, settings.max_invoices_per_match
        )
        if combo:
            consume(
                [source],
                combo,
                "lump",
                f"{source.label} of ${source.amount:,.2f} is the exact total of invoices "
                f"{', '.join(i.label for i in combo)}.",
            )

    # 5. Same shapes again, this time allowing the configured tolerance.
    for invoice in pending_invoices():
        hit = next(
            (s for s in available() if within_tolerance(s.amount, invoice.balance, settings)),
            None,
        )
        if hit:
            gap = invoice.balance - hit.amount
            consume(
                [hit],
                [invoice],
                "near",
                f"{hit.label} of ${hit.amount:,.2f} is ${abs(gap):,.2f} "
                f"{'under' if gap > 0 else 'over'} invoice {invoice.label}. "
                "Could be a fee, a short pay, or the wrong invoice.",
            )

    for invoice in pending_invoices():
        combo = _subset_summing_to(
            available(), invoice.balance, settings.max_sources_per_match, exact=False,
            settings_obj=settings,
        )
        if combo:
            consume(
                combo,
                [invoice],
                "near_split",
                f"{len(combo)} payments totalling ${_total(combo):,.2f} come within tolerance "
                f"of invoice {invoice.label} at ${invoice.balance:,.2f}.",
            )

    return matches, available()


def _subset_summing_to(
    sources: list[MoneySource],
    target: Decimal,
    max_size: int,
    exact: bool,
    settings_obj: Settings | None = None,
) -> list[MoneySource] | None:
    """Smallest group of sources that adds up to the target. Pairs before triples."""
    if target <= 0 or len(sources) < 2:
        return None
    for size in range(2, min(max_size, len(sources)) + 1):
        for combo in itertools.combinations(sources, size):
            total = _total(combo)
            if exact:
                if total == target:
                    return list(combo)
            elif settings_obj is not None and within_tolerance(total, target, settings_obj):
                return list(combo)
    return None


def _invoice_subset_summing_to(
    invoices: list[Invoice], target: Decimal, max_size: int
) -> list[Invoice] | None:
    if target <= 0 or len(invoices) < 2:
        return None
    for size in range(2, min(max_size, len(invoices)) + 1):
        for combo in itertools.combinations(invoices, size):
            if sum((inv.balance for inv in combo), ZERO) == target:
                return list(combo)
    return None


# --- what the pipeline calls ------------------------------------------------


def reconcile(
    invoices_by_customer: dict[str, list[Invoice]],
    sources: list[MoneySource],
    settings: Settings,
    today: date,
) -> tuple[list[ProposedMatch], list[UnexplainedMoney]]:
    """Run the matching pass across every customer with an open invoice."""
    refs = RefCounter("M")
    all_matches: list[ProposedMatch] = []
    claimed: set[tuple[str, str]] = set()  # (kind, id) already spent

    for customer_id, invoices in invoices_by_customer.items():
        if not invoices:
            continue
        name = invoices[0].customer_name
        mine = [
            s for s in sources_for_customer(sources, customer_id, name)
            if (s.kind, s.id) not in claimed
        ]
        if not mine:
            continue
        matches, _ = match_customer(invoices, mine, settings, refs)
        for match in matches:
            for source in match.sources:
                claimed.add((source.kind, source.id))
        all_matches.extend(matches)

    unexplained = _unexplained(sources, claimed, today, settings)
    return all_matches, unexplained


def _unexplained(
    sources: list[MoneySource],
    claimed: set[tuple[str, str]],
    today: date,
    settings: Settings,
) -> list[UnexplainedMoney]:
    """Money that arrived and matched nothing. Usually a prepayment or a typo."""
    refs = RefCounter("U")
    out: list[UnexplainedMoney] = []
    for source in sources:
        if (source.kind, source.id) in claimed:
            continue
        if source.amount < settings.unexplained_floor:
            continue
        age = (today - source.txn_date).days
        if source.posted_to_income:
            reason = (
                f"${source.amount:,.2f} booked straight to '{source.note or 'an income account'}' "
                f"{age} days ago and matched to no open invoice."
            )
        elif source.kind == "credit_memo":
            reason = f"${source.amount:,.2f} of credit is open and drawing down against nothing."
        else:
            reason = (
                f"${source.amount:,.2f} received {age} days ago "
                f"({source.account or 'unknown account'}) matches no open invoice."
            )
        out.append(UnexplainedMoney(ref=refs.next(), source=source, reason=reason))
    return out


def pending_by_invoice(matches: list[ProposedMatch]) -> dict[str, Decimal]:
    """How much matched money is waiting to be applied to each invoice.

    A lump payment covering several invoices is allocated oldest first, capped at
    each invoice's balance, so the total never exceeds the money that arrived.
    """
    pending: dict[str, Decimal] = {}
    for match in matches:
        remaining = match.amount
        for invoice in sorted(match.invoices, key=lambda i: i.txn_date):
            if remaining <= 0:
                break
            share = min(remaining, invoice.balance)
            pending[invoice.id] = pending.get(invoice.id, ZERO) + share
            remaining -= share
    return pending
