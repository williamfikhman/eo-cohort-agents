"""Plain data structures shared across the agent.

Money is `Decimal` everywhere. Floats are not allowed near an invoice balance —
a cent of drift turns a paid invoice into a dunning email.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

ZERO = Decimal("0.00")


def money(value: Any) -> Decimal:
    """Coerce anything QBO hands us into a 2-place Decimal."""
    if value is None or value == "":
        return ZERO
    return Decimal(str(value)).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class InvoiceLine:
    description: str
    item_name: str
    amount: Decimal
    qty: Decimal | None = None
    rate: Decimal | None = None

    @property
    def haystack(self) -> str:
        return f"{self.item_name} {self.description}".lower()


@dataclass
class Invoice:
    id: str
    doc_number: str
    customer_id: str
    customer_name: str
    txn_date: date
    due_date: date
    total: Decimal
    balance: Decimal
    currency: str = "USD"
    exchange_rate: Decimal | None = None
    bill_email: str | None = None
    lines: list[InvoiceLine] = field(default_factory=list)
    # Ids of credit memos / payments QBO already links to this invoice.
    linked_txns: list[tuple[str, str]] = field(default_factory=list)
    private_note: str = ""
    # QBO's shareable customer-facing invoice URL, fetched on demand.
    share_link: str | None = None

    @property
    def paid(self) -> bool:
        return self.balance <= ZERO

    @property
    def partially_paid(self) -> bool:
        return ZERO < self.balance < self.total

    @property
    def amount_paid(self) -> Decimal:
        return self.total - self.balance

    def days_past_due(self, today: date) -> int:
        return (today - self.due_date).days

    @property
    def period(self) -> tuple[int, int]:
        """(year, month) of the invoice — how 'stacked across months' is counted."""
        return (self.txn_date.year, self.txn_date.month)

    @property
    def label(self) -> str:
        return f"#{self.doc_number or self.id}"


@dataclass
class Payment:
    id: str
    customer_id: str
    customer_name: str
    txn_date: date
    total: Decimal
    unapplied: Decimal
    applied_invoice_ids: list[str] = field(default_factory=list)
    reference: str = ""
    private_note: str = ""
    deposit_account: str = ""

    @property
    def haystack(self) -> str:
        return f"{self.reference} {self.private_note}".lower()


@dataclass
class DepositLine:
    """One line of a bank deposit.

    `account_name` is where the line was categorized. When that is an income
    account and an invoice for the same money is still open, the money has been
    counted twice: once on the invoice and once on the deposit. That is the
    shape a bank-feed "Add" leaves behind when it should have been a "Match".
    """

    amount: Decimal
    entity_name: str = ""
    account_name: str = ""
    linked_txn_types: list[str] = field(default_factory=list)

    @property
    def already_linked(self) -> bool:
        """A line pointing at a Payment is money QBO has already accounted for."""
        return bool(self.linked_txn_types)


@dataclass
class Deposit:
    id: str
    txn_date: date
    total: Decimal
    account_name: str
    lines: list[DepositLine] = field(default_factory=list)
    private_note: str = ""

    @property
    def customer_names(self) -> list[str]:
        return [line.entity_name for line in self.lines if line.entity_name]

    @property
    def line_amounts(self) -> list[Decimal]:
        return [line.amount for line in self.lines]

    @property
    def loose_lines(self) -> list[DepositLine]:
        """Lines not already tied to a Payment — the money that can go missing."""
        return [line for line in self.lines if not line.already_linked]


@dataclass
class CreditMemo:
    id: str
    doc_number: str
    customer_id: str
    customer_name: str
    txn_date: date
    total: Decimal
    remaining: Decimal


@dataclass
class Customer:
    id: str
    display_name: str
    balance: Decimal
    email: str | None = None
    active: bool = True


# --- what the run produces -------------------------------------------------


@dataclass
class ReminderCandidate:
    """A reminder the agent proposes. Nothing here has been sent."""

    ref: str  # R1, R2 ... the handle William approves by
    invoice: Invoice
    stage_id: str
    stage_label: str
    days_past_due: int
    to: str
    cc: list[str]
    subject: str
    body: str

    @property
    def key(self) -> str:
        """Idempotency key. One send per invoice per stage, ever."""
        return f"{self.invoice.id}:{self.stage_id}"


@dataclass
class Flag:
    """Something that does not look right. Goes to William, never to the client."""

    ref: str  # F1, F2 ...
    kind: str
    customer_name: str
    reason: str
    recommended_action: str
    invoice_label: str | None = None
    amount: Decimal | None = None
    severity: str = "review"  # review | urgent

    @property
    def sort_key(self) -> tuple[int, str]:
        return (0 if self.severity == "urgent" else 1, self.customer_name.lower())


@dataclass
class MoneySource:
    """Money sitting somewhere other than on the invoice it belongs to.

    One shape for all three origins — an unapplied customer payment, a loose
    bank-feed deposit line, an open credit memo — so the matcher does not care
    which door the money came through.
    """

    kind: str  # payment | deposit | credit_memo
    id: str
    txn_date: date
    amount: Decimal
    account: str = ""
    customer_id: str = ""
    customer_name: str = ""
    reference: str = ""
    note: str = ""
    # True when the line was booked straight to an income account. If an invoice
    # for the same money is open, revenue and AR are both overstated by it.
    posted_to_income: bool = False

    @property
    def label(self) -> str:
        pretty = {"payment": "Payment", "deposit": "Deposit", "credit_memo": "Credit memo"}
        name = pretty.get(self.kind, self.kind)
        return f"{name} {self.reference or self.id}"

    @property
    def haystack(self) -> str:
        return f"{self.reference} {self.note}".lower()


@dataclass
class ProposedMatch:
    """Money the agent believes belongs to one or more open invoices.

    A proposal, never an action. Nothing is applied in QuickBooks by this agent;
    the match goes in the digest and a human applies it. Deposit-to-invoice
    matching carries real double-payment risk, which is exactly why the decision
    stays with a person.
    """

    ref: str  # M1, M2 ... the handle William confirms by
    invoices: list["Invoice"]
    sources: list[MoneySource]
    amount: Decimal
    strategy: str  # referenced | exact | split | lump | near | near_split
    confidence: str  # high | medium | low
    rationale: str

    @property
    def customer_name(self) -> str:
        return self.invoices[0].customer_name if self.invoices else ""

    @property
    def invoice_labels(self) -> str:
        return ", ".join(inv.label for inv in self.invoices)

    @property
    def source_labels(self) -> str:
        return ", ".join(s.label for s in self.sources)

    @property
    def invoice_total(self) -> Decimal:
        return sum((inv.balance for inv in self.invoices), ZERO)

    @property
    def fully_covers(self) -> bool:
        return self.amount >= self.invoice_total

    @property
    def double_counted(self) -> Decimal:
        """How much of this match is already sitting in an income account."""
        return sum((s.amount for s in self.sources if s.posted_to_income), ZERO)

    def covers(self, invoice_id: str) -> bool:
        return any(inv.id == invoice_id for inv in self.invoices)


@dataclass
class UnexplainedMoney:
    """A source the matcher could not tie to any open invoice."""

    ref: str  # U1, U2 ...
    source: MoneySource
    reason: str


@dataclass
class Snapshot:
    """One morning's AR totals. Kept so week-over-week is a fact, not a guess."""

    as_of: date
    total_ar: Decimal
    past_due_ar: Decimal
    open_invoice_count: int
    past_due_count: int
    # Money already received and waiting only to be applied in QBO. Carried
    # separately so "AR" never quietly includes cash that is already in the bank.
    pending_application: Decimal = ZERO
    pending_invoice_count: int = 0

    @property
    def net_ar(self) -> Decimal:
        """What clients still actually owe, once pending applications land."""
        return self.total_ar - self.pending_application


@dataclass
class RunResult:
    digest_id: str
    as_of: date
    # The reconciliation pass runs first; everything below is computed from
    # what it leaves behind.
    matches: list[ProposedMatch] = field(default_factory=list)
    unexplained: list[UnexplainedMoney] = field(default_factory=list)
    reminders: list[ReminderCandidate] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    snapshot: Snapshot | None = None
    prior_snapshot: Snapshot | None = None
    suppressed_count: int = 0
    errors: list[str] = field(default_factory=list)

    def match_for(self, invoice_id: str) -> "ProposedMatch | None":
        return next((m for m in self.matches if m.covers(invoice_id)), None)
