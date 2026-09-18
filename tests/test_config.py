import pytest

from ar_followup.config import ConfigError, load_config

BASE_SETTINGS = """
identities:
  digest_to: william@marketplaceofficer.com
  billing_from: Billing <billing@marketplaceofficer.com>
policy:
  require_approval_before_client_email: true
  allow_qbo_writes: false
defaults:
  payment_terms_days: 15
cadence:
  stages:
    - id: friendly
      days_past_due: 3
rules: {}
"""


def write(tmp_path, settings=BASE_SETTINGS, clients="clients: []"):
    directory = tmp_path / "config"
    directory.mkdir(exist_ok=True)
    (directory / "settings.yaml").write_text(settings)
    (directory / "clients.yaml").write_text(clients)
    return directory


def test_the_repo_config_is_valid(config):
    assert config.settings.digest_to == "william@marketplaceofficer.com"
    assert {c.name for c in config.clients} == {"Kaaral", "Rust Check Corp", "Zendex"}


def test_every_named_client_is_suppressed_today(config):
    """Kaaral, Rust Check and Zendex are all hands-off right now."""
    assert all(c.reminders_suppressed for c in config.clients)


def test_rust_check_credit_is_on_file(config):
    rust_check = config.find_client("x", "rust check")
    assert rust_check.credit_balance.amount == __import__("decimal").Decimal("6026.67")


def test_clients_match_by_alias_and_id(config):
    assert config.find_client("999", "Kaaral USA").name == "Kaaral"
    assert config.find_client("999", "unknown name") is None


def test_turning_off_the_approval_rule_is_refused(tmp_path):
    settings = BASE_SETTINGS.replace(
        "require_approval_before_client_email: true",
        "require_approval_before_client_email: false",
    )
    with pytest.raises(ConfigError, match="does not run without human approval"):
        load_config(write(tmp_path, settings=settings))


def test_enabling_qbo_writes_is_refused(tmp_path):
    settings = BASE_SETTINGS.replace("allow_qbo_writes: false", "allow_qbo_writes: true")
    with pytest.raises(ConfigError, match="read-only"):
        load_config(write(tmp_path, settings=settings))


def test_posting_without_a_confirmation_is_refused(tmp_path):
    """The agent never decides on its own that a match is right."""
    settings = BASE_SETTINGS + """
payment_application:
  enabled: true
  require_confirmed_match: false
"""
    with pytest.raises(ConfigError, match="confirmed by a person"):
        load_config(write(tmp_path, settings=settings))


def test_posting_deposit_sourced_matches_is_refused(tmp_path):
    """Posting a payment for cash already in the register books it twice."""
    settings = BASE_SETTINGS + """
payment_application:
  enabled: true
  allow_deposit_sources: true
"""
    with pytest.raises(ConfigError, match="booking the cash twice|cash twice"):
        load_config(write(tmp_path, settings=settings))


def test_posting_is_off_by_default(tmp_path):
    config = load_config(write(tmp_path))
    assert config.settings.apply_enabled is False


def test_the_repo_has_posting_switched_off(config):
    """Until William turns it on deliberately, nothing is written to QBO."""
    assert config.settings.apply_enabled is False


def test_overlapping_contract_periods_are_refused(tmp_path):
    clients = """
clients:
  - name: Acme
    terms:
      - effective_from: 2026-01-01
        effective_to: 2026-06-30
        flat_fee_monthly: 3000
      - effective_from: 2026-06-01
        flat_fee_monthly: 0
"""
    with pytest.raises(ConfigError, match="overlap"):
        load_config(write(tmp_path, clients=clients))


def test_an_unclosed_earlier_period_is_refused(tmp_path):
    clients = """
clients:
  - name: Acme
    terms:
      - effective_from: 2026-01-01
        flat_fee_monthly: 3000
      - effective_from: 2026-07-01
        flat_fee_monthly: 0
"""
    with pytest.raises(ConfigError, match="Close the earlier period"):
        load_config(write(tmp_path, clients=clients))


def test_a_duplicate_alias_is_refused(tmp_path):
    clients = """
clients:
  - name: Acme
    aliases: ["Acme Inc"]
  - name: Acme Inc
"""
    with pytest.raises(ConfigError, match="two clients"):
        load_config(write(tmp_path, clients=clients))


def test_a_bad_reminder_status_is_refused(tmp_path):
    clients = """
clients:
  - name: Acme
    status:
      reminders: maybe
"""
    with pytest.raises(ConfigError, match="'active' or 'suppressed'"):
        load_config(write(tmp_path, clients=clients))


def test_a_bad_date_is_refused(tmp_path):
    clients = """
clients:
  - name: Acme
    terms:
      - effective_from: "last April"
"""
    with pytest.raises(ConfigError, match="YYYY-MM-DD"):
        load_config(write(tmp_path, clients=clients))


def test_broken_yaml_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(write(tmp_path, clients="clients: [oops"))


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nowhere")


def test_step_down_dates_are_derived_from_the_periods(tmp_path):
    clients = """
clients:
  - name: Acme
    terms:
      - effective_from: 2026-01-01
        effective_to: 2026-04-30
        flat_fee_monthly: 3000
      - effective_from: 2026-05-01
        flat_fee_monthly: 0
"""
    config = load_config(write(tmp_path, clients=clients))
    assert [d.isoformat() for d in config.clients[0].step_down_dates] == ["2026-05-01"]
