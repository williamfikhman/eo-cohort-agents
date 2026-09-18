"""Gmail send and read for the billing mailbox.

Reminders go out through Gmail rather than QBO's native invoice reminder on
purpose: we want the send log and the thread history in the mailbox where Angie
and William already work, and we want a client's reply to land in the same
thread as the reminder that prompted it.

Scopes needed on the OAuth client:
  https://www.googleapis.com/auth/gmail.send      (digest + approved reminders)
  https://www.googleapis.com/auth/gmail.readonly  (reading approval replies)
"""

from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any

import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


class GmailError(Exception):
    pass


@dataclass
class GmailMessage:
    id: str
    thread_id: str
    sender: str
    subject: str
    body: str
    received: str

    @property
    def sender_email(self) -> str:
        return parseaddr(self.sender)[1].lower()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode(data: str | None) -> str:
    if not data:
        return ""
    padding = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(data + padding).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


def _extract_text(payload: dict[str, Any]) -> str:
    """Depth-first hunt for the text/plain part; fall back to any body there is."""
    if not payload:
        return ""
    mime = payload.get("mimeType", "")
    body = payload.get("body") or {}
    if mime == "text/plain" and body.get("data"):
        return _decode(body["data"])
    for part in payload.get("parts") or []:
        text = _extract_text(part)
        if text:
            return text
    if body.get("data"):
        return _decode(body["data"])
    return ""


class GmailClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        session: requests.Session | None = None,
    ):
        missing = [
            name
            for name, value in (
                ("GMAIL_CLIENT_ID", client_id),
                ("GMAIL_CLIENT_SECRET", client_secret),
                ("GMAIL_REFRESH_TOKEN", refresh_token),
            )
            if not value
        ]
        if missing:
            raise GmailError(f"Gmail credentials missing: {', '.join(missing)}")
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.session = session or requests.Session()
        self._access_token = ""
        self._expires_at = 0.0

    @classmethod
    def from_env(cls, session: requests.Session | None = None) -> "GmailClient":
        return cls(
            client_id=os.environ.get("GMAIL_CLIENT_ID", ""),
            client_secret=os.environ.get("GMAIL_CLIENT_SECRET", ""),
            refresh_token=os.environ.get("GMAIL_REFRESH_TOKEN", ""),
            session=session,
        )

    # --- auth --------------------------------------------------------------

    def _token(self) -> str:
        if self._access_token and time.time() < self._expires_at - 120:
            return self._access_token
        response = self.session.post(
            TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        if response.status_code != 200:
            raise GmailError(f"Gmail token refresh failed ({response.status_code}): {response.text[:300]}")
        payload = response.json()
        self._access_token = payload["access_token"]
        self._expires_at = time.time() + float(payload.get("expires_in", 3600))
        return self._access_token

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        url = f"{API_BASE}{path}"
        for attempt in range(1, 4):
            response = self.session.request(
                method,
                url,
                headers={"Authorization": f"Bearer {self._token()}"},
                timeout=60,
                **kwargs,
            )
            if response.status_code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            if response.status_code >= 400:
                raise GmailError(f"Gmail {method} {path} failed ({response.status_code}): {response.text[:300]}")
            return response.json() if response.content else {}
        raise GmailError(f"Gmail {method} {path} gave up after retries")

    # --- sending -----------------------------------------------------------

    def send(
        self,
        *,
        sender: str,
        to: str,
        subject: str,
        text: str,
        html_body: str | None = None,
        cc: list[str] | None = None,
        reply_to: str | None = None,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
    ) -> dict[str, Any]:
        message = EmailMessage()
        message["From"] = sender
        message["To"] = to
        message["Subject"] = subject
        if cc:
            message["Cc"] = ", ".join(cc)
        if reply_to:
            message["Reply-To"] = reply_to
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
            message["References"] = in_reply_to
        message.set_content(text)
        if html_body:
            message.add_alternative(html_body, subtype="html")

        payload: dict[str, Any] = {"raw": _b64url(message.as_bytes())}
        if thread_id:
            payload["threadId"] = thread_id
        return self._request("POST", "/messages/send", json=payload)

    # --- reading -----------------------------------------------------------

    def search(self, query: str, limit: int = 20) -> list[str]:
        payload = self._request(
            "GET", "/messages", params={"q": query, "maxResults": limit}
        )
        return [m["id"] for m in payload.get("messages", [])]

    def message(self, message_id: str) -> GmailMessage:
        payload = self._request("GET", f"/messages/{message_id}", params={"format": "full"})
        headers = {
            h["name"].lower(): h["value"] for h in (payload.get("payload") or {}).get("headers", [])
        }
        return GmailMessage(
            id=payload["id"],
            thread_id=payload.get("threadId", ""),
            sender=headers.get("from", ""),
            subject=headers.get("subject", ""),
            body=_extract_text(payload.get("payload") or {}),
            received=headers.get("date", ""),
        )

    def thread(self, thread_id: str) -> list[GmailMessage]:
        """Every message in one thread, oldest first — how approvals are read."""
        payload = self._request("GET", f"/threads/{thread_id}", params={"format": "full"})
        messages: list[GmailMessage] = []
        for raw in payload.get("messages", []):
            headers = {
                h["name"].lower(): h["value"]
                for h in (raw.get("payload") or {}).get("headers", [])
            }
            messages.append(
                GmailMessage(
                    id=raw["id"],
                    thread_id=raw.get("threadId", thread_id),
                    sender=headers.get("from", ""),
                    subject=headers.get("subject", ""),
                    body=_extract_text(raw.get("payload") or {}),
                    received=headers.get("date", ""),
                )
            )
        return messages
