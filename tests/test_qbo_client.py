"""The read-only guarantees, tested as behaviour rather than trusted as policy."""

import pytest

from ar_followup.qbo.auth import QboAuth, Token, TokenStore
from ar_followup.qbo.client import QboClient, QboError, QboWriteAttempted


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.headers = {}
        self.content = b"{}"

    def json(self):
        return self._payload


class RecordingSession:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [FakeResponse()])

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(("GET", url, params))
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]

    def post(self, *args, **kwargs):
        self.calls.append(("POST",) + args)
        return FakeResponse(
            payload={"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
        )


def auth(tmp_path, session):
    store = TokenStore(tmp_path / "token.json")
    store.save(Token("at", "rt", 9e12, "realm-1"))
    return QboAuth("id", "secret", store, session=session)


def test_write_methods_do_not_exist(tmp_path):
    client = QboClient(auth(tmp_path, RecordingSession()), session=RecordingSession())
    for method in ("post", "put", "patch", "delete", "create", "update", "send"):
        with pytest.raises(QboWriteAttempted):
            getattr(client, method)


def test_report_endpoints_are_refused(tmp_path):
    session = RecordingSession()
    client = QboClient(auth(tmp_path, session), session=session)
    with pytest.raises(QboError, match="cached data"):
        client._get("reports/AgedReceivables")
    assert session.calls == []


def test_a_query_carries_the_bearer_token_and_minor_version(tmp_path):
    session = RecordingSession([FakeResponse(payload={"QueryResponse": {"Invoice": [{"Id": "1"}]}})])
    client = QboClient(auth(tmp_path, session), base_url="https://example.test", session=session)
    rows = client.query("SELECT * FROM Invoice")
    assert rows == [{"Id": "1"}]
    method, url, params = session.calls[0]
    assert method == "GET"
    assert url == "https://example.test/v3/company/realm-1/query"
    assert params["minorversion"] == "75"


def test_paging_stops_when_a_short_page_comes_back(tmp_path):
    pages = [
        FakeResponse(payload={"QueryResponse": {"Invoice": [{"Id": str(i)} for i in range(500)]}}),
        FakeResponse(payload={"QueryResponse": {"Invoice": [{"Id": "500"}]}}),
    ]
    session = RecordingSession(pages)
    client = QboClient(auth(tmp_path, session), session=session)
    rows = list(client.query_all("Invoice"))
    assert len(rows) == 501
    assert len(session.calls) == 2


def test_a_429_is_retried(tmp_path):
    session = RecordingSession([
        FakeResponse(status_code=429, text="slow down"),
        FakeResponse(payload={"QueryResponse": {"Invoice": []}}),
    ])
    client = QboClient(auth(tmp_path, session), session=session, sleep=lambda _: None)
    assert client.query("SELECT * FROM Invoice") == []
    assert len(session.calls) == 2


def test_a_rotated_refresh_token_is_persisted(tmp_path):
    session = RecordingSession()
    store = TokenStore(tmp_path / "token.json")
    store.save(Token("old", "old-refresh", 0, "realm-1"))
    qbo_auth = QboAuth("id", "secret", store, session=session)

    assert qbo_auth.access_token() == "at"
    assert store.load().refresh_token == "rt"


def test_the_token_file_is_not_world_readable(tmp_path):
    store = TokenStore(tmp_path / "token.json")
    store.save(Token("at", "rt", 0, "realm-1"))
    assert oct(store.path.stat().st_mode)[-3:] == "600"
