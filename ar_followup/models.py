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
class Deposit:
    id: str
    txn_date: date
    total: Decimal
    account_name: str
    # A deposit line that already points at a customer/payment is not loose money.
    customer_names: list[str] = field(default_factory=list)
    line_amounts: list[Decimal] = field(default_factory=list)
    private_note: str = ""


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
class UnappliedCandidate:
    """Money that looks like it already arrived for this invoice."""

    ref: str  # M1, M2 ...
    invoice: Invoice
    source: str  # "payment" | "deposit"
    source_id: str
    source_date: date
    amount: Decimal
    account: str
    confidence: str  # exact | near
    note: str


@dataclass
class Snapshot:
    """One morning's AR totals. Kept so week-over-week is a fact, not a guess."""

    as_of: date
    total_ar: Decimal
    past_due_ar: Decimal
    open_invoice_count: int
    past_due_count: int


@dataclass
class RunResult:
    digest_id: str
    as_of: date
    reminders: list[ReminderCandidate] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    unapplied: list[UnappliedCandidate] = field(default_factory=list)
    snapshot: Snapshot | None = None
    prior_snapshot: Snapshot | None = None
    suppressed_count: int = 0
    errors: list[str] = field(default_factory=list)
