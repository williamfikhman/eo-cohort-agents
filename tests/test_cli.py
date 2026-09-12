"""The approval gate. Nothing may reach a client without an explicit, typed send."""

import json

import pytest
from click.testing import CliRunner

import cli as cli_module
from cli import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    """A prepared agreement on disk, as `prepare` would leave it."""
    build = tmp_path / "build"
    monkeypatch.setattr(cli_module, "BUILD_DIR", build)

    state = {
        "slug": "acme",
        "company_legal_name": "Acme Sandbox Brands, LLC",
        "document_id": "doc-1",
        "base_url": "https://api-eval.signnow.com",
        "sandbox": True,
        "dry_run": False,
        "pdf_path": str(tmp_path / "acme.pdf"),
        "page_count": 8,
        "fields": ["signature  role=Client    page 4, x=320, y=610"],
        "invite": {
            "document_id": "doc-1",
            "to": [{"email": "jordan@example.com", "role_id": "role-abc", "role": "Client",
                    "order": 1, "subject": "Sign", "message": "Please sign."}],
            "from": "william@marketplaceofficer.com",
            "subject": "Sign",
            "message": "Please sign.",
            "cc": [],
        },
        "review_url": "https://app-eval.signnow.com/document/doc-1",
    }
    path = build / "acme" / "prepared.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(state), encoding="utf-8")
    return state


@pytest.fixture
def sent(monkeypatch):
    """Capture send_invite calls instead of making them."""
    calls = []

    class Recorder:
        def __init__(self, *args, **kwargs):
            self.dry_run = kwargs.get("dry_run", False)

        def send_invite(self, document_id, invite):
            calls.append((document_id, invite))
            return {"status": "success"}

    monkeypatch.setattr(cli_module, "SignNowClient", Recorder)
    monkeypatch.setattr(cli_module, "_credentials_for", lambda state: object())
    return calls


def test_send_refuses_a_wrong_confirmation(runner, prepared, sent):
    result = runner.invoke(cli, ["send", "acme", "--yes", "Acme Sandbox Brands"])
    assert result.exit_code != 0
    assert "did not match" in result.output
    assert sent == [], "nothing may be sent after a failed confirmation"


def test_send_refuses_an_empty_confirmation(runner, prepared, sent):
    result = runner.invoke(cli, ["send", "acme"], input="\n")
    assert result.exit_code != 0
    assert sent == []


def test_send_refuses_the_slug_instead_of_the_legal_name(runner, prepared, sent):
    """Typing the slug is the easy mistake; it must not be accepted."""
    result = runner.invoke(cli, ["send", "acme", "--yes", "acme"])
    assert result.exit_code != 0
    assert sent == []


def test_send_proceeds_on_an_exact_match(runner, prepared, sent):
    result = runner.invoke(cli, ["send", "acme", "--yes", "Acme Sandbox Brands, LLC"])
    assert result.exit_code == 0, result.output
    assert len(sent) == 1
    document_id, invite = sent[0]
    assert document_id == "doc-1"
    assert invite["to"][0]["email"] == "jordan@example.com"


def test_send_refuses_when_prepare_was_a_dry_run(runner, prepared, sent, tmp_path):
    path = tmp_path / "build" / "acme" / "prepared.json"
    state = json.loads(path.read_text())
    state["dry_run"] = True
    path.write_text(json.dumps(state), encoding="utf-8")

    result = runner.invoke(cli, ["send", "acme", "--yes", "Acme Sandbox Brands, LLC"])
    assert result.exit_code != 0
    assert "no document exists" in result.output
    assert sent == []


def test_send_dry_run_confirms_but_does_not_send(runner, prepared, sent):
    result = runner.invoke(
        cli, ["send", "acme", "--dry-run", "--yes", "Acme Sandbox Brands, LLC"]
    )
    assert result.exit_code == 0, result.output
    assert "Not sent" in result.output


def test_send_needs_a_prepare_first(runner, tmp_path, monkeypatch, sent):
    monkeypatch.setattr(cli_module, "BUILD_DIR", tmp_path / "empty")
    result = runner.invoke(cli, ["send", "ghost", "--yes", "Anything"])
    assert result.exit_code != 0
    assert "has not been prepared" in result.output
    assert sent == []


def test_preview_shows_the_recipient_without_sending(runner, prepared, sent):
    result = runner.invoke(cli, ["preview", "acme"])
    assert result.exit_code == 0, result.output
    assert "jordan@example.com" in result.output
    assert "not sent" in result.output.lower()
    assert sent == []


def test_prepare_reports_a_bad_config_and_stops(runner, tmp_path, monkeypatch):
    import config as config_module

    empty = tmp_path / "clients"
    empty.mkdir()
    monkeypatch.setattr(config_module, "CLIENTS_DIR", empty)
    result = runner.invoke(cli, ["prepare", "nobody"])
    assert result.exit_code != 0
    assert "no client config" in result.output
