"""A bad client config must fail before anything is rendered."""

import pytest

from config import ConfigError, load_client, validate_client_values


def test_valid_config_loads(clients_dir):
    client = load_client("acme", clients_dir=clients_dir)
    assert client.company_legal_name == "Acme Sandbox Brands, LLC"
    assert client.signatory_email == "jordan@example.com"


def test_initial_term_is_normalised_to_string(clients_dir):
    client = load_client("acme", clients_dir=clients_dir)
    assert client["initial_term_months"] == "12"


@pytest.mark.parametrize("missing", [
    "company_legal_name", "entity_type", "state_of_incorporation", "company_address",
    "signatory_name", "signatory_title", "signatory_email", "effective_date",
    "monthly_fee", "commission_pct", "commission_terms", "initial_term_months",
    "trademark_exhibit", "schedule_b_deliverables",
])
def test_every_required_variable_is_enforced(client_values, missing):
    del client_values[missing]
    problems = validate_client_values(client_values)
    assert any(missing in p for p in problems), problems


def test_empty_string_is_rejected(client_values):
    client_values["monthly_fee"] = "   "
    assert any("empty" in p for p in validate_client_values(client_values))


def test_empty_deliverables_list_is_rejected(client_values):
    client_values["schedule_b_deliverables"] = []
    assert any("non-empty list" in p for p in validate_client_values(client_values))


def test_blank_deliverable_entry_is_rejected(client_values):
    client_values["schedule_b_deliverables"] = ["Weekly reporting", ""]
    assert any("empty entry" in p for p in validate_client_values(client_values))


def test_bad_entity_type_is_rejected(client_values):
    client_values["entity_type"] = "S-Corp"
    assert any("entity_type" in p for p in validate_client_values(client_values))


def test_bad_email_is_rejected(client_values):
    client_values["signatory_email"] = "not-an-email"
    assert any("signatory_email" in p for p in validate_client_values(client_values))


def test_unquoted_yaml_date_is_rejected(write_client, client_values, clients_dir):
    """An unquoted date becomes a date object and would render as 2026-01-05."""
    import datetime

    client_values["effective_date"] = datetime.date(2026, 1, 5)
    write_client("dated", client_values)
    with pytest.raises(ConfigError, match="quote it"):
        load_client("dated", clients_dir=clients_dir)


def test_unknown_variable_is_rejected(client_values):
    """A typo'd key would otherwise be dropped without a word."""
    client_values["montly_fee"] = "$8,500"
    assert any("unknown variable: montly_fee" in p for p in validate_client_values(client_values))


def test_zero_term_is_rejected(client_values):
    client_values["initial_term_months"] = 0
    assert any("positive" in p for p in validate_client_values(client_values))


def test_all_problems_reported_at_once(client_values):
    del client_values["monthly_fee"]
    del client_values["commission_pct"]
    client_values["entity_type"] = "S-Corp"
    problems = validate_client_values(client_values)
    assert len(problems) >= 3


def test_missing_file_names_the_alternatives(clients_dir):
    with pytest.raises(ConfigError, match="acme"):
        load_client("nope", clients_dir=clients_dir)


def test_slug_must_be_safe(clients_dir):
    with pytest.raises(ConfigError, match="invalid client slug"):
        load_client("../../etc/passwd", clients_dir=clients_dir)
