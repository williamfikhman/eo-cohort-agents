"""Read-only HTTP client for the QuickBooks Online v3 API.

Two things this client will not do:

1. Any HTTP method other than GET. Every QBO write — creating a payment,
   applying a credit, sending an invoice reminder — is a POST, so refusing POST
   makes "no writes to QBO" a property of the transport rather than a promise in
   a docstring.
2. Any report endpoint. Aging reports are served from a cache that has shown us
   invoices as open days after they were paid. Live invoice balances are the
   only source of truth in this build.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Iterator

import requests

from .auth import QboAuth

LOG = logging.getLogger("ar_followup.qbo")

PRODUCTION_BASE = "https://quickbooks.api.intuit.com"
SANDBOX_BASE = "https://sandbox-quickbooks.api.intuit.com"
MINOR_VERSION = "75"
PAGE_SIZE = 500
MAX_ATTEMPTS = 5
BANNED_PATH_FRAGMENTS = ("/reports/",)


class QboError(Exception):
    pass


class QboWriteAttempted(QboError):
    """Raised if any code path tries to write. This is a bug, not a condition."""


class QboClient:
    def __init__(
        self,
        auth: QboAuth,
        base_url: str | None = None,
        session: requests.Session | None = None,
        sleep=time.sleep,
    ):
        self.auth = auth
        self.base_url = (base_url or os.environ.get("QBO_BASE_URL") or PRODUCTION_BASE).rstrip("/")
        self.session = session or requests.Session()
        self._sleep = sleep

    # --- transport ---------------------------------------------------------

    def _url(self, path: str) -> str:
        realm = self.auth.realm_id
        return f"{self.base_url}/v3/company/{realm}/{path.lstrip('/')}"

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        for fragment in BANNED_PATH_FRAGMENTS:
            if fragment in f"/{path.lstrip('/')}":
                raise QboError(
                    f"refusing to call '{path}'. Report endpoints (aging included) "
                    "serve cached data; query live invoice balances instead."
                )

        url = self._url(path)
        query = dict(params or {})
        query["minorversion"] = MINOR_VERSION

        last_error = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            headers = {
                "Authorization": f"Bearer {self.auth.access_token()}",
                "Accept": "application/json",
            }
            try:
                response = self.session.get(url, headers=headers, params=query, timeout=60)
            except requests.RequestException as exc:
                last_error = f"network error: {exc}"
                self._backoff(attempt)
                continue

            if response.status_code == 200:
                return response.json()

            if response.status_code == 401 and attempt < MAX_ATTEMPTS:
                # Token died mid-run (revoked, or rotated elsewhere). Force one refresh.
                LOG.info("QBO returned 401; refreshing access token")
                self.auth.refresh()
                continue

            if response.status_code in (429, 500, 502, 503, 504) and attempt < MAX_ATTEMPTS:
                last_error = f"{response.status_code}: {response.text[:200]}"
                LOG.warning("QBO %s on %s, retrying (attempt %s)", response.status_code, path, attempt)
                self._backoff(attempt, response)
                continue

            raise QboError(f"QBO GET {path} failed ({response.status_code}): {response.text[:400]}")

        raise QboError(f"QBO GET {path} failed after {MAX_ATTEMPTS} attempts. {last_error}")

    def _backoff(self, attempt: int, response: requests.Response | None = None) -> None:
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                self._sleep(min(int(retry_after), 60))
                return
        self._sleep(min(2 ** attempt, 30))

    # --- reads -------------------------------------------------------------

    def query(self, statement: str) -> list[dict[str, Any]]:
        """Run one query and return its rows. Use `query_all` for large sets."""
        payload = self._get("query", {"query": statement})
        response = payload.get("QueryResponse") or {}
        for key, value in response.items():
            if isinstance(value, list):
                return value
        return []

    def query_all(self, select: str, where: str = "", order_by: str = "Id") -> Iterator[dict[str, Any]]:
        """Page through a query with STARTPOSITION/MAXRESULTS.

        QBO caps a query at 1000 rows and gives no cursor, so paging is the only
        way to see all of a 50-80 client company's open invoices.
        """
        start = 1
        while True:
            clauses = [f"SELECT * FROM {select}"]
            if where:
                clauses.append(f"WHERE {where}")
            clauses.append(f"ORDER BY {order_by}")
            clauses.append(f"STARTPOSITION {start} MAXRESULTS {PAGE_SIZE}")
            rows = self.query(" ".join(clauses))
            for row in rows:
                yield row
            if len(rows) < PAGE_SIZE:
                return
            start += PAGE_SIZE

    def read_entity(
        self, entity: str, entity_id: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Fetch one object by id — the freshest read QBO offers.

        This is what pre-send verification uses. Never trust a balance carried
        over from the top of the run; re-read it here, seconds before sending.
        """
        payload = self._get(f"{entity.lower()}/{entity_id}", params)
        return payload.get(entity.capitalize()) or payload.get(entity)

    # --- guards ------------------------------------------------------------

    def __getattr__(self, name: str):
        if name in ("post", "put", "patch", "delete", "create", "update", "send"):
            raise QboWriteAttempted(
                f"QboClient has no '{name}'. This agent is read-only against QBO; "
                "writes go through a human."
            )
        raise AttributeError(name)
