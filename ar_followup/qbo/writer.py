"""The only code in this build that writes to QuickBooks.

It does exactly one thing: allocate a Payment that already exists in QuickBooks
to an invoice it already belongs to. No money is created, nothing is deposited,
the cash total in the register does not move. Only the allocation changes, which
is the difference between an invoice that says "open" and one that says "paid".

Everything else stays manual, and not out of timidity:

  * A bank-feed deposit with no Payment behind it cannot be posted here. Creating
    a Payment for cash that is already sitting in the register books the same
    money twice. The fix lives in the bank feed — undo the Add, then Find match —
    and a person has to do it.
  * A deposit already categorized to income has the same problem plus revenue to
    back out.

Three things stop a double application, in order:

  1. the ledger, which knows what was applied before
  2. the audit stamp written into the Payment's PrivateNote, which survives even
     if the ledger is lost
  3. the live re-read, which refuses if the invoice balance or the payment's
     unapplied amount has moved since the match was proposed
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import requests

from .auth import QboAuth
from .client import MINOR_VERSION, PRODUCTION_BASE, QboError

LOG = logging.getLogger("ar_followup.qbo.writer")


class QboWriteRefused(QboError):
    """The write was not attempted, and the reason is not a transient one."""


class QboWriter:
    """POST access, off by default, with one operation on it."""

    def __init__(
        self,
        auth: QboAuth,
        enabled: bool,
        audit_stamp: str = "AR-agent",
        base_url: str | None = None,
        session: requests.Session | None = None,
    ):
        self.auth = auth
        self.enabled = enabled
        self.audit_stamp = audit_stamp
        self.base_url = (base_url or PRODUCTION_BASE).rstrip("/")
        self.session = session or requests.Session()

    def _url(self, path: str) -> str:
        return f"{self.base_url}/v3/company/{self.auth.realm_id}/{path.lstrip('/')}"

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise QboWriteRefused(
                "posting to QuickBooks is off. Set payment_application.enabled to true "
                "in config/settings.yaml once you are ready for it."
            )
        response = self.session.post(
            self._url(path),
            headers={
                "Authorization": f"Bearer {self.auth.access_token()}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            params={"minorversion": MINOR_VERSION},
            json=payload,
            timeout=60,
        )
        if response.status_code == 401:
            self.auth.refresh()
            response = self.session.post(
                self._url(path),
                headers={
                    "Authorization": f"Bearer {self.auth.access_token()}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                params={"minorversion": MINOR_VERSION},
                json=payload,
                timeout=60,
            )
        if response.status_code >= 400:
            # Never retry a write on an unclear failure. A second POST after an
            # ambiguous first one is how a payment gets applied twice.
            raise QboError(
                f"QBO POST {path} failed ({response.status_code}): {response.text[:400]}"
            )
        return response.json()

    # --- the one operation -------------------------------------------------

    def stamp_for(self, invoice_id: str) -> str:
        return f"[{self.audit_stamp} applied to invoice {invoice_id}]"

    def already_stamped(self, payment: dict[str, Any], invoice_id: str) -> bool:
        return self.stamp_for(invoice_id) in str(payment.get("PrivateNote") or "")

    def links_invoice(self, payment: dict[str, Any], invoice_id: str) -> bool:
        for line in payment.get("Line") or []:
            for txn in line.get("LinkedTxn") or []:
                if str(txn.get("TxnType")) == "Invoice" and str(txn.get("TxnId")) == str(invoice_id):
                    return True
        return False

    def apply_payment_to_invoice(
        self, payment: dict[str, Any], invoice_id: str, amount: Decimal
    ) -> dict[str, Any]:
        """Allocate an existing unapplied Payment to an invoice.

        `payment` must be a freshly read Payment object — its SyncToken is what
        makes this safe against a concurrent edit. If someone changed the payment
        in QuickBooks since we read it, QBO rejects the write rather than
        silently overwriting their change.
        """
        payment_id = str(payment.get("Id"))
        if self.links_invoice(payment, invoice_id):
            raise QboWriteRefused(
                f"payment {payment_id} is already linked to invoice {invoice_id}"
            )
        if self.already_stamped(payment, invoice_id):
            raise QboWriteRefused(
                f"payment {payment_id} already carries this agent's stamp for invoice "
                f"{invoice_id}; refusing to apply it a second time"
            )

        unapplied = Decimal(str(payment.get("UnappliedAmt") or "0"))
        if amount > unapplied:
            raise QboWriteRefused(
                f"payment {payment_id} has ${unapplied:,.2f} unapplied, which is less "
                f"than the ${amount:,.2f} this would allocate"
            )

        body = dict(payment)
        note = str(body.get("PrivateNote") or "").strip()
        body["PrivateNote"] = (note + " " + self.stamp_for(invoice_id)).strip()[:4000]
        body["Line"] = list(body.get("Line") or []) + [
            {
                "Amount": float(amount),
                "LinkedTxn": [{"TxnId": str(invoice_id), "TxnType": "Invoice"}],
            }
        ]
        LOG.info("applying $%s of payment %s to invoice %s", amount, payment_id, invoice_id)
        return self._post("payment", body)
