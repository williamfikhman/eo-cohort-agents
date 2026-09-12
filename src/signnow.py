"""SignNow REST client.

Every endpoint and payload here is traceable to a source recorded in
docs/signnow-api-notes.md. None of it was written from memory.

Two properties matter more than the API surface:

* **Sending is one method.** SignNow has no "prepare but hold" endpoint -- creating
  a role-based invite *is* sending it. So ``send_invite`` is the only code path in
  this module that can put an agreement in front of a client, and nothing calls it
  except the CLI's ``send`` command.
* **Dry run blocks writes, not reads.** With ``dry_run=True`` every mutating
  request is recorded to the audit log and skipped. Reads still go out, so a dry
  run tells you something true about the account.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_LOG = REPO_ROOT / "logs" / "signnow-audit.log"

PRODUCTION_BASE_URL = "https://api.signnow.com"
SANDBOX_BASE_URL = "https://api-eval.signnow.com"

#: Multipart part name for the tag definitions on /document/fieldextract.
#: The .NET SDK sends "Tags"; the Node SDK's payload dict keys it "tags". The
#: .NET code is the one that shows the literal multipart assembly, so that wins.
#: If the sandbox rejects the tags, this is the first thing to flip.
TAGS_PART_NAME = "Tags"

#: Token is refreshed this many seconds before it actually expires.
TOKEN_LEEWAY_SECONDS = 60

_REDACTED = "<redacted>"
_SECRET_KEYS = frozenset(
    {"password", "client_secret", "access_token", "refresh_token", "authorization"}
)


class SignNowError(Exception):
    """Base class for every failure raised by this module."""


class SignNowConfigError(SignNowError):
    """Credentials are missing or malformed."""


class SignNowAuthError(SignNowError):
    """The account could not be authenticated."""


class SignNowAPIError(SignNowError):
    """SignNow returned an error response."""

    def __init__(self, method: str, path: str, status: int, body: str):
        self.method = method
        self.path = path
        self.status = status
        self.body = body
        super().__init__(f"{method.upper()} {path} -> HTTP {status}: {body[:500]}")


# --------------------------------------------------------------------------- creds


@dataclass(frozen=True)
class Credentials:
    client_id: str
    client_secret: str = field(repr=False)
    username: str
    password: str = field(repr=False)
    base_url: str = PRODUCTION_BASE_URL

    @property
    def is_sandbox(self) -> bool:
        return "api-eval" in self.base_url

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "Credentials":
        env = environ if environ is not None else os.environ
        required = (
            "SIGNNOW_CLIENT_ID",
            "SIGNNOW_CLIENT_SECRET",
            "SIGNNOW_USERNAME",
            "SIGNNOW_PASSWORD",
        )
        missing = [k for k in required if not (env.get(k) or "").strip()]
        if missing:
            raise SignNowConfigError(
                "missing SignNow credentials: " + ", ".join(missing) + "\n"
                "Set them in .env (see .env.example). .env is gitignored and its "
                "values are never written to the audit log."
            )
        return cls(
            client_id=env["SIGNNOW_CLIENT_ID"].strip(),
            client_secret=env["SIGNNOW_CLIENT_SECRET"].strip(),
            username=env["SIGNNOW_USERNAME"].strip(),
            password=env["SIGNNOW_PASSWORD"],
            base_url=(env.get("SIGNNOW_BASE_URL") or PRODUCTION_BASE_URL).rstrip("/"),
        )


@dataclass
class Token:
    access_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    expires_at: float = 0.0

    @property
    def expired(self) -> bool:
        return time.time() >= (self.expires_at - TOKEN_LEEWAY_SECONDS)


# ---------------------------------------------------------------------- audit log


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: (_REDACTED if k.lower() in _SECRET_KEYS else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


class AuditLog:
    """Append-only record of every API call, one JSON object per line.

    Credentials are redacted on the way in, so the log can be read, copied and
    attached to a support ticket without leaking the account.
    """

    def __init__(self, path: Path | None = None):
        self.path = path or AUDIT_LOG

    def record(
        self,
        action: str,
        *,
        method: str | None = None,
        path: str | None = None,
        document_id: str | None = None,
        status: int | str | None = None,
        dry_run: bool = False,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "action": action,
            "method": method,
            "path": path,
            "document_id": document_id,
            "status": status,
            "dry_run": dry_run,
            "detail": _redact(detail or {}),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry


# ------------------------------------------------------------------------- models


@dataclass(frozen=True)
class Role:
    unique_id: str
    name: str
    signing_order: str


@dataclass(frozen=True)
class DocumentField:
    id: str
    type: str
    role: str
    role_id: str
    page_number: int | None
    x: float | None
    y: float | None

    def describe(self) -> str:
        where = (
            f"page {self.page_number}, x={self.x:.0f}, y={self.y:.0f}"
            if self.page_number is not None and self.x is not None and self.y is not None
            else "position not reported"
        )
        return f"{self.type:<10} role={self.role:<10} {where}"


@dataclass(frozen=True)
class InviteState:
    email: str
    role: str
    status: str
    updated: str | None


# ------------------------------------------------------------------------- client


class SignNowClient:
    """Thin, explicit wrapper over the endpoints this project uses."""

    def __init__(
        self,
        credentials: Credentials,
        audit: AuditLog | None = None,
        dry_run: bool = False,
        session: requests.Session | None = None,
        timeout: int = 60,
    ):
        self.credentials = credentials
        self.audit = audit or AuditLog()
        self.dry_run = dry_run
        self.timeout = timeout
        self._session = session or requests.Session()
        self._token: Token | None = None

    @classmethod
    def from_env(
        cls,
        dry_run: bool = False,
        audit: AuditLog | None = None,
        environ: dict[str, str] | None = None,
    ) -> "SignNowClient":
        return cls(Credentials.from_env(environ), audit=audit, dry_run=dry_run)

    # ------------------------------------------------------------------ auth

    def _fetch_token(self, refresh: bool = False) -> Token:
        creds = self.credentials
        if refresh and self._token and self._token.refresh_token:
            data = {
                "grant_type": "refresh_token",
                "refresh_token": self._token.refresh_token,
                "scope": "*",
            }
        else:
            data = {
                "grant_type": "password",
                "username": creds.username,
                "password": creds.password,
                "scope": "*",
            }

        response = self._session.post(
            f"{creds.base_url}/oauth2/token",
            data=data,
            auth=(creds.client_id, creds.client_secret),
            timeout=self.timeout,
        )
        self.audit.record(
            "oauth2.token",
            method="POST",
            path="/oauth2/token",
            status=response.status_code,
            detail={"grant_type": data["grant_type"]},
        )
        if response.status_code != 200:
            raise SignNowAuthError(
                f"could not get an access token (HTTP {response.status_code}). "
                "Check SIGNNOW_CLIENT_ID, SIGNNOW_CLIENT_SECRET, SIGNNOW_USERNAME "
                "and SIGNNOW_PASSWORD."
            )

        body = response.json()
        return Token(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token"),
            expires_at=time.time() + float(body.get("expires_in", 3600)),
        )

    def token(self) -> str:
        if self._token is None or self._token.expired:
            self._token = self._fetch_token(refresh=bool(self._token))
        return self._token.access_token

    # --------------------------------------------------------------- transport

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        document_id: str | None = None,
        action: str | None = None,
        audit_detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        mutating = method.upper() != "GET"
        label = action or f"{method.lower()}{path}"

        if mutating and self.dry_run:
            self.audit.record(
                label,
                method=method.upper(),
                path=path,
                document_id=document_id,
                status="skipped-dry-run",
                dry_run=True,
                detail=audit_detail or json_body or {},
            )
            return {"dry_run": True, "skipped": f"{method.upper()} {path}"}

        url = f"{self.credentials.base_url}{path}"

        def _send() -> requests.Response:
            return self._session.request(
                method,
                url,
                headers={"Authorization": f"Bearer {self.token()}"},
                json=json_body,
                data=data,
                files=files,
                timeout=self.timeout,
            )

        response = _send()
        if response.status_code == 401:
            # Token rejected -- refresh once and retry before giving up.
            self._token = None
            response = _send()

        self.audit.record(
            label,
            method=method.upper(),
            path=path,
            document_id=document_id,
            status=response.status_code,
            dry_run=False,
            detail=audit_detail or {},
        )

        if not 200 <= response.status_code < 300:
            raise SignNowAPIError(method, path, response.status_code, response.text)

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

    # ------------------------------------------------------------------ flow

    def upload_with_tags(
        self,
        pdf_path: Path,
        tags: list[dict[str, Any]],
        document_name: str | None = None,
    ) -> str:
        """POST /document/fieldextract -- upload and let SignNow derive fields.

        Returns the document_id.
        """
        if not pdf_path.is_file():
            raise SignNowError(f"PDF not found: {pdf_path}")

        name = document_name or pdf_path.name
        detail = {"document_name": name, "tag_count": len(tags),
                  "tags": [t.get("tag_name") for t in tags]}

        if self.dry_run:
            self.audit.record(
                "document.fieldextract",
                method="POST",
                path="/document/fieldextract",
                status="skipped-dry-run",
                dry_run=True,
                detail=detail,
            )
            return "dry-run-document-id"

        with pdf_path.open("rb") as handle:
            body = self._request(
                "POST",
                "/document/fieldextract",
                data={
                    TAGS_PART_NAME: json.dumps(tags),
                    "parse_type": "default",
                    "client_timestamp": str(int(time.time())),
                },
                files={"file": (name, handle, "application/pdf")},
                action="document.fieldextract",
                audit_detail=detail,
            )

        document_id = body.get("id")
        if not document_id:
            raise SignNowError(
                f"upload succeeded but no document id came back: {body!r}"
            )
        return document_id

    def get_document(self, document_id: str) -> dict[str, Any]:
        """GET /document/{id} -- the full document, fields and invites included."""
        return self._request(
            "GET",
            f"/document/{document_id}",
            document_id=document_id,
            action="document.get",
        )

    def roles(self, document_id: str) -> list[Role]:
        document = self.get_document(document_id)
        return [
            Role(
                unique_id=r.get("unique_id", ""),
                name=r.get("name", ""),
                signing_order=str(r.get("signing_order", "")),
            )
            for r in document.get("roles", [])
        ]

    def fields(self, document_id: str) -> list[DocumentField]:
        """The fields SignNow actually created, for confirming tag extraction."""
        document = self.get_document(document_id)
        out: list[DocumentField] = []
        for f in document.get("fields", []):
            attrs = f.get("json_attributes") or {}
            out.append(
                DocumentField(
                    id=f.get("id", ""),
                    type=f.get("type", ""),
                    role=f.get("role", ""),
                    role_id=f.get("role_id", ""),
                    page_number=attrs.get("page_number"),
                    x=attrs.get("x"),
                    y=attrs.get("y"),
                )
            )
        return out

    def send_invite(self, document_id: str, invite: dict[str, Any]) -> dict[str, Any]:
        """POST /document/{id}/invite -- **this sends the email.**

        The only method in this module that puts an agreement in front of a client.
        """
        recipients = [t.get("email") for t in invite.get("to", [])]
        return self._request(
            "POST",
            f"/document/{document_id}/invite",
            json_body=invite,
            document_id=document_id,
            action="document.invite.send",
            audit_detail={"to": recipients, "subject": invite.get("subject")},
        )

    def invite_status(self, document_id: str) -> list[InviteState]:
        document = self.get_document(document_id)
        return [
            InviteState(
                email=i.get("email", ""),
                role=i.get("role", ""),
                status=i.get("status", "unknown"),
                updated=i.get("updated"),
            )
            for i in document.get("field_invites", [])
        ]

    def document_url(self, document_id: str) -> str:
        """Where to open the document for review.

        Derived from the API host rather than returned by the API, so treat it as
        a convenience; the document_id beside it is the authoritative handle.
        """
        host = (
            "https://app-eval.signnow.com"
            if self.credentials.is_sandbox
            else "https://app.signnow.com"
        )
        return f"{host}/document/{document_id}"


# --------------------------------------------------------------- invite building


def build_invite(
    *,
    document_id: str,
    role_id: str,
    role_name: str,
    signer_email: str,
    sender_email: str,
    subject: str,
    message: str,
    expiration_days: int | None = None,
    reminder_days: int | None = None,
) -> dict[str, Any]:
    """Build the invite payload. Pure -- touches no network.

    Kept separate from ``send_invite`` on purpose: ``prepare`` builds this, writes
    it to disk and stops, so the exact bytes that will be sent can be reviewed
    before anything leaves the account.
    """
    recipient: dict[str, Any] = {
        "email": signer_email,
        "role_id": role_id,
        "role": role_name,
        "order": 1,
        "subject": subject,
        "message": message,
    }
    if expiration_days is not None:
        recipient["expiration_days"] = expiration_days
    if reminder_days is not None:
        recipient["reminder"] = reminder_days

    return {
        "document_id": document_id,
        "to": [recipient],
        "from": sender_email,
        "subject": subject,
        "message": message,
        "cc": [],
    }


def find_role(roles: Iterable[Role], name: str) -> Role:
    """Locate the signer role by name, case-insensitively."""
    roles = list(roles)
    for role in roles:
        if role.name.strip().lower() == name.strip().lower():
            return role
    available = ", ".join(r.name for r in roles) or "none"
    raise SignNowError(
        f"the document has no role named {name!r}. Roles present: {available}.\n"
        "A missing role usually means the text tags were not extracted -- check "
        "that the tags survived the PDF export."
    )
