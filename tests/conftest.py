"""Fixtures: a fake QuickBooks that can lie to us the way the real one did.

The important trick here is `live_overrides`. The fake serves one set of rows
from the list query and a different set from the single-invoice read, which is
exactly the situation that burned us — a list that says open, an invoice that is
already paid.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from ar_followup.config import load_config
from ar_followup.ledger import Ledger

REPO_ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 9, 12)


def invoice_row(
    id: str = "1",
    doc_number: str = "1001",
    customer_id: str = "10",
    customer_name: str = "Acme Brands",
    txn_date: date = date(2026, 9, 1),
    due_date: date | None = None,
    total: float = 3000.00,
    balance: float | None = None,
    lines: list[dict[str, Any]] | None = None,
    linked: list[dict[str, str]] | None = None,
    email: str | None = "ap@acmebrands.com",
) -> dict[str, Any]:
    due = due_date or (txn_date + timedelta(days=15))
    default_lines = [
        {
            "Amount": total,
            "Description": "Marketplace management",
            "DetailType": "SalesItemLineDetail",
            "SalesItemLineDetail": {"ItemRef": {"name": "Services"}},
        }
    ]
    return {
        "Id": id,
        "DocNumber": doc_number,
        "CustomerRef": {"value": customer_id, "name": customer_name},
        "TxnDate": txn_date.isoformat(),
        "DueDate": due.isoformat(),
        "TotalAmt": total,
        "Balance": total if balance is None else balance,
        "CurrencyRef": {"value": "USD"},
        "BillEmail": ({"Address": email} if email else None),
        "Line": lines if lines is not None else default_lines,
        "LinkedTxn": linked or [],
    }


def line(amount: float, description: str, item: str = "Services", qty=None, rate=None) -> dict[str, Any]:
    detail: dict[str, Any] = {"ItemRef": {"name": item}}
    if qty is not None:
        detail["Qty"] = qty
    if rate is not None:
        detail["UnitPrice"] = rate
    return {
        "Amount": amount,
        "Description": description,
        "DetailType": "SalesItemLineDetail",
        "SalesItemLineDetail": detail,
    }


def payment_row(
    id: str = "p1",
    customer_id: str = "10",
    customer_name: str = "Acme Brands",
    txn_date: date = date(2026, 9, 10),
    total: float = 3000.00,
    unapplied: float = 0.0,
    applied_invoice_ids: list[str] | None = None,
    reference: str = "",
    account: str = "Chase Business Checking",
) -> dict[str, Any]:
    return {
        "Id": id,
        "CustomerRef": {"value": customer_id, "name": customer_name},
        "TxnDate": txn_date.isoformat(),
        "TotalAmt": total,
        "UnappliedAmt": unapplied,
        "PaymentRefNum": reference,
        "DepositToAccountRef": {"name": account},
        "Line": [
            {"Amount": total, "LinkedTxn": [{"TxnId": inv, "TxnType": "Invoice"}]}
            for inv in (applied_invoice_ids or [])
        ],
    }


def deposit_row(
    id: str = "d1",
    txn_date: date = date(2026, 9, 10),
    total: float = 3000.00,
    account: str = "Chase Business Checking",
    customer_names: list[str] | None = None,
    line_amounts: list[float] | None = None,
) -> dict[str, Any]:
    amounts = line_amounts if line_amounts is not None else [total]
    names = customer_names or []
    lines = []
    for i, amount in enumerate(amounts):
        detail: dict[str, Any] = {}
        if i < len(names):
            detail["Entity"] = {"name": names[i]}
        lines.append({"Amount": amount, "DepositLineDetail": detail})
    return {
        "Id": id,
        "TxnDate": txn_date.isoformat(),
        "TotalAmt": total,
        "DepositToAccountRef": {"name": account},
        "Line": lines,
    }


def credit_memo_row(
    id: str = "cm1",
    customer_id: str = "10",
    customer_name: str = "Acme Brands",
    total: float = 500.0,
    remaining: float = 500.0,
) -> dict[str, Any]:
    return {
        "Id": id,
        "DocNumber": "CM-1",
        "CustomerRef": {"value": customer_id, "name": customer_name},
        "TxnDate": date(2026, 9, 1).isoformat(),
        "TotalAmt": total,
        "RemainingCredit": remaining,
    }


class FakeQbo:
    """Serves canned rows and records what was asked for."""

    def __init__(
        self,
        invoices: list[dict[str, Any]] | None = None,
        payments: list[dict[str, Any]] | None = None,
        deposits: list[dict[str, Any]] | None = None,
        credit_memos: list[dict[str, Any]] | None = None,
        live_overrides: dict[str, dict[str, Any]] | None = None,
        share_link: str | None = "https://qbo.example/invoice/abc",
    ):
        self.rows = {
            "Invoice": invoices or [],
            "Payment": payments or [],
            "Deposit": deposits or [],
            "CreditMemo": credit_memos or [],
            "Customer": [],
        }
        self.live_overrides = live_overrides or {}
        self.share_link = share_link
        self.entity_reads: list[str] = []
        self.queries: list[str] = []

    def query_all(self, select: str, where: str = "", order_by: str = "Id"):
        self.queries.append(f"{select} {where}".strip())
        yield from self.rows.get(select, [])

    def read_entity(self, entity: str, entity_id: str, params=None):
        self.entity_reads.append(f"{entity}:{entity_id}")
        row = self.live_overrides.get(entity_id)
        if row is None:
            row = next(
                (r for r in self.rows.get(entity.capitalize(), []) if str(r["Id"]) == str(entity_id)),
                None,
            )
        if row is None:
            return None
        row = dict(row)
        if params and params.get("include") == "invoiceLink" and self.share_link:
            row["InvoiceLink"] = self.share_link
        return row


class FakeGmail:
    def __init__(self, thread_messages: list | None = None):
        self.sent: list[dict[str, Any]] = []
        self.thread_messages = thread_messages or []

    def send(self, **kwargs):
        self.sent.append(kwargs)
        return {"id": f"msg{len(self.sent)}", "threadId": "thread-1"}

    def thread(self, thread_id: str):
        return self.thread_messages


@pytest.fixture
def config():
    return load_config(REPO_ROOT / "config")


@pytest.fixture
def settings(config):
    return config.settings


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "state")


@pytest.fixture
def today():
    return TODAY
