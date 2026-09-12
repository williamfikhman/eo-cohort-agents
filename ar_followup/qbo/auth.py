"""OAuth2 for the Intuit QuickBooks API.

This is a standalone OAuth app, not the MCP connector. The connector was dropped
on purpose: its aging reports serve cached data (it reported invoices open that
had already been paid), its customer filter on invoice queries does not work, and
it has no credit-memo endpoint. All three of those are load-bearing here.

Token lifetimes, from Intuit: the access token lasts 1 hour, the refresh token
lasts 100 days and is rotated roughly every 24 hours. The rotated refresh token
MUST be persisted or the agent locks itself out within a day — that is what
`TokenStore` exists for.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests

TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
REVOKE_URL = "https://developer.api.intuit.com/v2/oauth2/tokens/revoke"
# Refresh this many seconds before the token actually expires.
EXPIRY_SKEW_SECONDS = 120


class QboAuthError(Exception):
    pass


@dataclass
class Token:
    access_token: str
    refresh_token: str
    expires_at: float
    realm_id: str

    @property
    def expired(self) -> bool:
        return time.time() >= (self.expires_at - EXPIRY_SKEW_SECONDS)

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "realm_id": self.realm_id,
        }


class TokenStore:
    """Persists the token triple to disk with 0600 permissions.

    The file is the only stateful thing the agent owns that cannot be rebuilt
    from QBO. Back it up, do not commit it.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> Token | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise QboAuthError(f"token file at {self.path} is unreadable: {exc}") from exc
        missing = [k for k in ("access_token", "refresh_token", "realm_id") if not data.get(k)]
        if missing:
            raise QboAuthError(f"token file is missing: {', '.join(missing)}")
        return Token(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=float(data.get("expires_at", 0)),
            realm_id=str(data["realm_id"]),
        )

    def save(self, token: Token) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(token.to_dict(), indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)


class QboAuth:
    """Holds a live access token, refreshing it when it is about to expire."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        store: TokenStore,
        session: requests.Session | None = None,
    ):
        if not client_id or not client_secret:
            raise QboAuthError(
                "QBO_CLIENT_ID and QBO_CLIENT_SECRET must be set (the Intuit app "
                "credentials for the standalone OAuth app)."
            )
        self.client_id = client_id
        self.client_secret = client_secret
        self.store = store
        self.session = session or requests.Session()
        self._token: Token | None = None

    @classmethod
    def from_env(cls, session: requests.Session | None = None) -> "QboAuth":
        state_dir = Path(os.environ.get("AR_STATE_DIR", "state"))
        token_path = os.environ.get("QBO_TOKEN_FILE") or (state_dir / "qbo_token.json")
        return cls(
            client_id=os.environ.get("QBO_CLIENT_ID", ""),
            client_secret=os.environ.get("QBO_CLIENT_SECRET", ""),
            store=TokenStore(token_path),
            session=session,
        )

    def bootstrap(self, refresh_token: str, realm_id: str) -> Token:
        """Seed the store from a refresh token obtained in the OAuth playground."""
        token = Token(
            access_token="",
            refresh_token=refresh_token.strip(),
            expires_at=0.0,
            realm_id=str(realm_id).strip(),
        )
        self._token = token
        return self.refresh()

    @property
    def realm_id(self) -> str:
        return self._current().realm_id

    def _current(self) -> Token:
        if self._token is None:
            token = self.store.load()
            if token is None:
                raise QboAuthError(
                    f"no QBO token at {self.store.path}. Run "
                    "`python -m ar_followup auth --refresh-token ... --realm-id ...` once."
                )
            self._token = token
        return self._token

    def access_token(self) -> str:
        token = self._current()
        if token.expired or not token.access_token:
            token = self.refresh()
        return token.access_token

    def refresh(self) -> Token:
        token = self._current()
        response = self.session.post(
            TOKEN_URL,
            auth=(self.client_id, self.client_secret),
            headers={"Accept": "application/json"},
            data={"grant_type": "refresh_token", "refresh_token": token.refresh_token},
            timeout=30,
        )
        if response.status_code != 200:
            raise QboAuthError(
                f"token refresh failed ({response.status_code}): {response.text[:300]}. "
                "A 400 'invalid_grant' means the refresh token expired or was rotated "
                "out from under us — re-run the auth bootstrap."
            )
        payload = response.json()
        # Intuit rotates the refresh token; keeping the old one locks us out.
        refreshed = Token(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token", token.refresh_token),
            expires_at=time.time() + float(payload.get("expires_in", 3600)),
            realm_id=token.realm_id,
        )
        self._token = refreshed
        self.store.save(refreshed)
        return refreshed
