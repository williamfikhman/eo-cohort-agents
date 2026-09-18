"""Turn QBO JSON into the agent's data model.

Every function here is a read. The live `Balance` on an Invoice object is the
only thing this agent treats as the truth about what a client owes.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from ..models import (
    CreditMemo,
    Customer,
    Deposit,
    DepositLine,
    Invoice,
    InvoiceLine,
    Payment,
    money,
)
from .client import QboClient


def _date(value: Any, fallback: date | None = None) -> date | None:
    if not value:
        return fallback
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return fallback


def _ref(node: dict[str, Any] | None, key: str = "name") -> str:
    if not isinstance(node, dict):
        return ""
    return str(node.get(key) or "")


def _decimal_or_none(value: Any) -> Decimal | None:
    return money(value) if value not in (None, "") else None


def parse_invoice(row: dict[str, Any], default_terms_days: int) -> Invoice:
    txn_date = _date(row.get("TxnDate")) or date.today()
    # QBO can omit DueDate on an invoice with no terms set. Fall back to the
    # configured default (Net 15) rather than treating it as due today.
    due_date = _date(row.get("DueDate")) or (txn_date + timedelta(days=default_terms_days))

    lines: list[InvoiceLine] = []
    for line in row.get("Line") or []:
        if line.get("DetailType") in (None, "SubTotalLineDetail"):
            continue
        detail = line.get("SalesItemLineDetail") or {}
        lines.append(
            InvoiceLine(
                description=str(line.get("Description") or ""),
                item_name=_ref(detail.get("ItemRef")),
                amount=money(line.get("Amount")),
                qty=_decimal_or_none(detail.get("Qty")),
                rate=_decimal_or_none(detail.get("UnitPrice")),
            )
        )

    linked = [
        (str(t.get("TxnType") or ""), str(t.get("TxnId") or ""))
        for t in (row.get("LinkedTxn") or [])
    ]

    bill_email = None
    email_node = row.get("BillEmail")
    if isinstance(email_node, dict):
        bill_email = email_node.get("Address")

    return Invoice(
        id=str(row.get("Id")),
        doc_number=str(row.get("DocNumber") or ""),
        customer_id=_ref(row.get("CustomerRef"), "value"),
        customer_name=_ref(row.get("CustomerRef")),
        txn_date=txn_date,
        due_date=due_date,
        total=money(row.get("TotalAmt")),
        balance=money(row.get("Balance")),
        currency=_ref(row.get("CurrencyRef"), "value") or "USD",
        exchange_rate=_decimal_or_none(row.get("ExchangeRate")),
        bill_email=bill_email,
        lines=lines,
        linked_txns=linked,
        private_note=str(row.get("PrivateNote") or ""),
    )


def open_invoices(client: QboClient, default_terms_days: int) -> list[Invoice]:
    """Every invoice carrying a balance right now.

    Deliberately not filtered by customer in the query. The customer filter is
    the exact thing that failed us before, and at 50-80 clients the whole open
    set is a couple of pages anyway — so we pull it and filter in Python, where
    the filter is testable.
    """
    invoices = [
        parse_invoice(row, default_terms_days)
        for row in client.query_all("Invoice", where="Balance > '0'", order_by="TxnDate")
    ]
    # Re-check client-side: the WHERE clause is a hint, the parsed balance decides.
    return [inv for inv in invoices if inv.balance > 0]


def refresh_invoice(client: QboClient, invoice_id: str, default_terms_days: int) -> Invoice | None:
    """Single-invoice re-read, used immediately before any send is proposed."""
    row = client.read_entity("Invoice", invoice_id)
    return parse_invoice(row, default_terms_days) if row else None


def parse_payment(row: dict[str, Any]) -> Payment:
    applied: list[str] = []
    for line in row.get("Line") or []:
        for txn in line.get("LinkedTxn") or []:
            if str(txn.get("TxnType")) == "Invoice":
                applied.append(str(txn.get("TxnId")))
    return Payment(
        id=str(row.get("Id")),
        customer_id=_ref(row.get("CustomerRef"), "value"),
        customer_name=_ref(row.get("CustomerRef")),
        txn_date=_date(row.get("TxnDate")) or date.today(),
        total=money(row.get("TotalAmt")),
        unapplied=money(row.get("UnappliedAmt")),
        applied_invoice_ids=applied,
        reference=str(row.get("PaymentRefNum") or ""),
        private_note=str(row.get("PrivateNote") or ""),
        deposit_account=_ref(row.get("DepositToAccountRef")),
    )


def recent_payments(client: QboClient, since: date) -> list[Payment]:
    where = f"TxnDate >= '{since.isoformat()}'"
    return [parse_payment(row) for row in client.query_all("Payment", where=where, order_by="TxnDate")]


def parse_deposit(row: dict[str, Any]) -> Deposit:
    """Parse a deposit line by line.

    Two details on each line decide whether the money is loose: the account it
    was categorized to, and whether it links back to a Payment. A line linked to
    a Payment is money QBO has already put through AR. A line categorized
    straight to an income account is money that skipped AR entirely.
    """
    lines: list[DepositLine] = []
    for line in row.get("Line") or []:
        detail = line.get("DepositLineDetail") or {}
        entity = detail.get("Entity")
        lines.append(
            DepositLine(
                amount=money(line.get("Amount")),
                entity_name=(str(entity["name"]) if isinstance(entity, dict) and entity.get("name") else ""),
                account_name=_ref(detail.get("AccountRef")),
                linked_txn_types=[
                    str(t.get("TxnType") or "")
                    for t in (line.get("LinkedTxn") or [])
                    if t.get("TxnType")
                ],
            )
        )
    return Deposit(
        id=str(row.get("Id")),
        txn_date=_date(row.get("TxnDate")) or date.today(),
        total=money(row.get("TotalAmt")),
        account_name=_ref(row.get("DepositToAccountRef")),
        lines=lines,
        private_note=str(row.get("PrivateNote") or ""),
    )


def recent_deposits(client: QboClient, since: date, accounts: list[str]) -> list[Deposit]:
    """Bank-feed deposits — the Chase and QuickBooks Payments side of the check.

    Money lands here first. An invoice can be fully paid in the bank and still
    show a balance in QBO because nobody applied the payment yet. Troomy and
    OcuSoft were both one click from a dunning email on the day they paid.
    """
    where = f"TxnDate >= '{since.isoformat()}'"
    deposits = [parse_deposit(row) for row in client.query_all("Deposit", where=where, order_by="TxnDate")]
    if not accounts:
        return deposits
    needles = [a.lower() for a in accounts]
    return [d for d in deposits if any(n in d.account_name.lower() for n in needles)]


def parse_credit_memo(row: dict[str, Any]) -> CreditMemo:
    remaining = row.get("RemainingCredit")
    if remaining in (None, ""):
        remaining = row.get("Balance")
    return CreditMemo(
        id=str(row.get("Id")),
        doc_number=str(row.get("DocNumber") or ""),
        customer_id=_ref(row.get("CustomerRef"), "value"),
        customer_name=_ref(row.get("CustomerRef")),
        txn_date=_date(row.get("TxnDate")) or date.today(),
        total=money(row.get("TotalAmt")),
        remaining=money(remaining),
    )


def credit_memos(client: QboClient) -> list[CreditMemo]:
    """Open credit memos. The MCP connector had no endpoint for these at all."""
    memos = [parse_credit_memo(row) for row in client.query_all("CreditMemo", order_by="TxnDate")]
    return [m for m in memos if m.remaining > 0]


def parse_customer(row: dict[str, Any]) -> Customer:
    email_node = row.get("PrimaryEmailAddr")
    return Customer(
        id=str(row.get("Id")),
        display_name=str(row.get("DisplayName") or ""),
        balance=money(row.get("Balance")),
        email=(email_node.get("Address") if isinstance(email_node, dict) else None),
        active=bool(row.get("Active", True)),
    )


def customers(client: QboClient) -> list[Customer]:
    return [parse_customer(row) for row in client.query_all("Customer", order_by="Id")]


def invoice_share_link(client: QboClient, invoice_id: str) -> str | None:
    """QBO's customer-facing invoice URL.

    Asked for per invoice because the link is minted on request and expires;
    a reminder with a dead link is worse than a reminder with none.
    """
    row = client.read_entity("Invoice", invoice_id, {"include": "invoiceLink"})
    if not row:
        return None
    return row.get("InvoiceLink") or None
