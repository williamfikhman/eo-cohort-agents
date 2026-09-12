import copy
import json
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

VALID_CLIENT = {
    "company_legal_name": "Acme Sandbox Brands, LLC",
    "entity_type": "LLC",
    "state_of_incorporation": "Delaware",
    "company_address": "100 Test Street, Wilmington, DE 19801",
    "signatory_name": "Jordan Rivera",
    "signatory_title": "Chief Executive Officer",
    "signatory_email": "jordan@example.com",
    "effective_date": "January 5, 2026",
    "monthly_fee": "$8,500 per month",
    "commission_pct": "4%",
    "commission_terms": "Commission on net sales above a $250,000 monthly baseline.",
    "initial_term_months": 12,
    "schedule_b_deliverables": ["Weekly reporting", "Listing optimisation"],
    "trademark_exhibit": "TBD",
}

VALID_CMO = {
    "legal_name": "Chief Marketplace Officer, Inc.",
    "entity_type": "corporation",
    "state_of_incorporation": "Delaware",
    "address": "123 Example Ave, Los Angeles, CA 90001",
    "signatory_name": "William Fikhman",
    "signatory_title": "CEO",
    "signature_date": "January 5, 2026",
    "signature_mark": "/s/ William Fikhman",
    "sender_email": "william@marketplaceofficer.com",
}


@pytest.fixture
def client_values():
    return copy.deepcopy(VALID_CLIENT)


@pytest.fixture
def cmo_values():
    return copy.deepcopy(VALID_CMO)


@pytest.fixture
def clients_dir(tmp_path, client_values):
    """A clients/ directory holding one valid config."""
    directory = tmp_path / "clients"
    directory.mkdir()
    (directory / "acme.yaml").write_text(
        yaml.safe_dump(client_values, sort_keys=False), encoding="utf-8"
    )
    return directory


@pytest.fixture
def write_client(clients_dir):
    def _write(slug, values):
        path = clients_dir / f"{slug}.yaml"
        path.write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
        return path

    return _write


@pytest.fixture
def template_path():
    path = REPO_ROOT / "templates" / "amazon_services_agreement.docx"
    if not path.is_file():
        pytest.skip("template not built; run python tools/build_template.py")
    return path


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=None):
        self.status_code = status_code
        self._body = body
        self.text = text if text is not None else json.dumps(body or {})
        self.content = self.text.encode()

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    """Records requests and replays queued responses.

    Queue by (METHOD, path-suffix); anything unqueued raises, so a test can never
    pass by accidentally hitting an endpoint it did not intend to.
    """

    def __init__(self):
        self.queued: dict[tuple[str, str], list[FakeResponse]] = {}
        self.calls: list[dict] = []

    def queue(self, method, path, response):
        self.queued.setdefault((method.upper(), path), []).append(response)

    def _match(self, method, url):
        for (m, path), responses in self.queued.items():
            if m == method.upper() and url.endswith(path) and responses:
                return responses.pop(0)
        raise AssertionError(f"unexpected request: {method.upper()} {url}")

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method.upper(), "url": url, **kwargs})
        return self._match(method, url)


@pytest.fixture
def session():
    return FakeSession()


@pytest.fixture
def audit(tmp_path):
    from signnow import AuditLog

    return AuditLog(tmp_path / "audit.log")


@pytest.fixture
def credentials():
    from signnow import Credentials

    return Credentials(
        client_id="cid",
        client_secret="csecret",
        username="user@example.com",
        password="hunter2",
        base_url="https://api-eval.signnow.com",
    )
