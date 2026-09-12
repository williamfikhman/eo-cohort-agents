"""Client and CMO configuration, validated before anything is rendered.

A missing or empty variable is a hard failure here, by design: an agreement that
reaches a client with an unfilled placeholder is worse than one that never got
built. Every problem in the file is reported at once rather than one per run.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENTS_DIR = REPO_ROOT / "clients"
CMO_CONFIG = REPO_ROOT / "config" / "cmo.yaml"

#: Per-client variables. Every one is required -- see module docstring.
REQUIRED_STRING_VARS = (
    "company_legal_name",
    "entity_type",
    "state_of_incorporation",
    "company_address",
    "signatory_name",
    "signatory_title",
    "signatory_email",
    "effective_date",
    "monthly_fee",
    "commission_pct",
    "commission_terms",
    "initial_term_months",
    "trademark_exhibit",
)

REQUIRED_LIST_VARS = ("schedule_b_deliverables",)

ENTITY_TYPES = ("LLC", "Corporation")

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class ConfigError(Exception):
    """Raised when a client or CMO config is missing, malformed or incomplete."""


@dataclass(frozen=True)
class ClientConfig:
    slug: str
    path: Path
    values: dict[str, Any] = field(repr=False)

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    @property
    def signatory_email(self) -> str:
        return self.values["signatory_email"]

    @property
    def company_legal_name(self) -> str:
        return self.values["company_legal_name"]

    def context(self, cmo: dict[str, Any]) -> dict[str, Any]:
        """Template context: client values plus the constant CMO block."""
        ctx = dict(self.values)
        ctx.update({f"cmo_{k}": v for k, v in cmo.items()})
        return ctx


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    if loaded is None:
        raise ConfigError(f"{path} is empty")
    if not isinstance(loaded, dict):
        raise ConfigError(f"{path} must contain a mapping, got {type(loaded).__name__}")
    return loaded


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def validate_client_values(values: dict[str, Any]) -> list[str]:
    """Return every problem found. An empty list means the config is usable."""
    problems: list[str] = []

    for key in REQUIRED_STRING_VARS:
        if key not in values:
            problems.append(f"missing required variable: {key}")
        elif _blank(values[key]):
            problems.append(f"required variable is empty: {key}")

    for key in REQUIRED_LIST_VARS:
        if key not in values:
            problems.append(f"missing required variable: {key}")
            continue
        value = values[key]
        if not isinstance(value, list) or not value:
            problems.append(f"{key} must be a non-empty list")
        elif any(_blank(item) for item in value):
            problems.append(f"{key} contains an empty entry")

    entity = values.get("entity_type")
    if isinstance(entity, str) and entity.strip() and entity not in ENTITY_TYPES:
        problems.append(
            f"entity_type must be one of {' or '.join(ENTITY_TYPES)}, got {entity!r}"
        )

    email = values.get("signatory_email")
    if isinstance(email, str) and email.strip() and not _EMAIL.match(email.strip()):
        problems.append(f"signatory_email is not a valid address: {email!r}")

    effective = values.get("effective_date")
    if isinstance(effective, _dt.date):
        problems.append(
            "effective_date was parsed as a YAML date; quote it so it renders "
            "exactly as written, e.g. effective_date: \"January 5, 2026\""
        )

    term = values.get("initial_term_months")
    if not _blank(term):
        try:
            months = int(str(term).strip())
        except (TypeError, ValueError):
            problems.append(f"initial_term_months must be a whole number, got {term!r}")
        else:
            if months <= 0:
                problems.append(f"initial_term_months must be positive, got {months}")

    unknown = set(values) - set(REQUIRED_STRING_VARS) - set(REQUIRED_LIST_VARS)
    for key in sorted(unknown):
        problems.append(
            f"unknown variable: {key} (it would be silently dropped from the agreement)"
        )

    return problems


def load_client(slug: str, clients_dir: Path | None = None) -> ClientConfig:
    if not _SLUG.match(slug):
        raise ConfigError(
            f"invalid client slug {slug!r}: use lowercase letters, digits and hyphens"
        )

    directory = clients_dir or CLIENTS_DIR
    path = directory / f"{slug}.yaml"
    if not path.is_file():
        available = sorted(p.stem for p in directory.glob("*.yaml"))
        hint = f" Available: {', '.join(available)}" if available else ""
        raise ConfigError(f"no client config at {path}.{hint}")

    values = _load_yaml(path)
    problems = validate_client_values(values)
    if problems:
        listed = "\n".join(f"  - {p}" for p in problems)
        raise ConfigError(
            f"{path} cannot be used to build an agreement:\n{listed}\n\n"
            "Fix every line above and re-run. Nothing was rendered or uploaded."
        )

    values["initial_term_months"] = str(values["initial_term_months"]).strip()
    return ClientConfig(slug=slug, path=path, values=values)


def load_cmo(path: Path | None = None) -> dict[str, Any]:
    """The constant CMO side of the agreement: legal entity and signature block."""
    cmo_path = path or CMO_CONFIG
    values = _load_yaml(cmo_path)

    required = (
        "legal_name",
        "entity_type",
        "state_of_incorporation",
        "address",
        "signatory_name",
        "signatory_title",
        "signature_date",
        "signature_mark",
        "sender_email",
    )
    missing = [k for k in required if _blank(values.get(k))]
    if missing:
        raise ConfigError(
            f"{cmo_path} is missing: {', '.join(missing)}"
        )
    return values


def list_clients(clients_dir: Path | None = None) -> list[str]:
    directory = clients_dir or CLIENTS_DIR
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.yaml"))
