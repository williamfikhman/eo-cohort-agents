"""Load and validate config/settings.yaml and config/clients.yaml.

Every run starts here. A config that does not parse, or that tries to turn off
one of the two standing rules (no QBO writes, no client email without approval),
stops the run before a single API call is made.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from .models import money

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_DIR = REPO_ROOT / "config"


class ConfigError(Exception):
    """The config is unusable. Never send anything on a config error."""


def _as_date(value: Any, where: str) -> date | None:
    if value in (None, "", "null"):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ConfigError(f"{where}: '{value}' is not a YYYY-MM-DD date") from exc


def _as_decimal(value: Any, where: str) -> Decimal | None:
    if value in (None, "", "null"):
        return None
    try:
        return money(value)
    except (InvalidOperation, ValueError) as exc:
        raise ConfigError(f"{where}: '{value}' is not a number") from exc


@dataclass(frozen=True)
class Stage:
    id: str
    days_past_due: int
    template: str
    cc: list[str]
    sends_email: bool
    label: str


@dataclass(frozen=True)
class TermPeriod:
    effective_from: date
    effective_to: date | None
    flat_fee_monthly: Decimal | None
    commission_pct: Decimal | None
    commission_basis: str
    notes: str

    def covers(self, day: date) -> bool:
        if day < self.effective_from:
            return False
        return self.effective_to is None or day <= self.effective_to

    @property
    def validatable(self) -> bool:
        """Only validate an invoice when the period actually states its terms."""
        return self.flat_fee_monthly is not None or self.commission_pct is not None


@dataclass(frozen=True)
class CreditBalance:
    amount: Decimal
    as_of: date | None
    note: str


@dataclass
class ClientTerms:
    name: str
    qbo_customer_id: str | None
    aliases: list[str]
    billing_contact: str | None
    payment_terms_days: int | None
    currency: str | None
    fx_rate: Decimal | None
    terms: list[TermPeriod]
    sunset_date: date | None
    step_down_dates: list[date]
    expected_total_range: tuple[Decimal, Decimal] | None
    reminders_suppressed: bool
    suppression_reason: str
    credit_balance: CreditBalance | None
    flags: list[dict[str, Any]]

    def term_for(self, day: date) -> TermPeriod | None:
        for period in self.terms:
            if period.covers(day):
                return period
        return None

    @property
    def match_names(self) -> list[str]:
        return [n.lower().strip() for n in ([self.name] + self.aliases) if n]


@dataclass
class Settings:
    digest_to: str
    billing_from: str
    billing_reply_to: str
    escalation_cc: str
    approval_ttl_hours: int
    send_weekdays: list[int]
    payment_terms_days: int
    currency: str
    stages: list[Stage]
    stacked_ar_threshold: int
    stacked_ar_distinct_months: bool
    contract_change_lookahead_days: int
    unapplied_lookback_days: int
    unapplied_tolerance_pct: Decimal
    unapplied_tolerance_abs: Decimal
    short_pay_review_floor: Decimal
    minimum_reminder_balance: Decimal
    bank_feed_accounts: list[str]
    flat_fee_patterns: list[str]
    commission_patterns: list[str]
    digest_subject_prefix: str
    max_rows_per_section: int
    timezone: str
    raw: dict[str, Any] = field(default_factory=dict)

    def stage_by_id(self, stage_id: str) -> Stage | None:
        return next((s for s in self.stages if s.id == stage_id), None)


@dataclass
class Config:
    settings: Settings
    clients: list[ClientTerms]
    config_dir: Path

    def find_client(self, customer_id: str, display_name: str) -> ClientTerms | None:
        """Id first, then name/alias. Id wins because names get renamed in QBO."""
        for client in self.clients:
            if client.qbo_customer_id and client.qbo_customer_id == str(customer_id):
                return client
        needle = (display_name or "").lower().strip()
        if not needle:
            return None
        for client in self.clients:
            if needle in client.match_names:
                return client
        return None


def _require(mapping: dict[str, Any], key: str, where: str) -> Any:
    if key not in mapping or mapping[key] in (None, ""):
        raise ConfigError(f"{where}: required key '{key}' is missing or empty")
    return mapping[key]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} did not parse to a mapping")
    return data


def _parse_settings(data: dict[str, Any]) -> Settings:
    identities = _require(data, "identities", "settings.yaml")
    policy = _require(data, "policy", "settings.yaml")
    defaults = data.get("defaults") or {}
    cadence = _require(data, "cadence", "settings.yaml")
    rules = _require(data, "rules", "settings.yaml")
    lines = data.get("invoice_lines") or {}
    digest = data.get("digest") or {}

    # The two standing rules are not configurable downward.
    if not policy.get("require_approval_before_client_email", False):
        raise ConfigError(
            "policy.require_approval_before_client_email is false. This agent does "
            "not run without human approval on client email. Change the policy with "
            "William, not with this file."
        )
    if policy.get("allow_qbo_writes", False):
        raise ConfigError(
            "policy.allow_qbo_writes is true. This agent is read-only against QBO."
        )
    if policy.get("allow_payment_application", False):
        raise ConfigError(
            "policy.allow_payment_application is true. Auto-applying payments has "
            "known double-payment risk and is not implemented."
        )

    raw_stages = _require(cadence, "stages", "settings.yaml cadence")
    stages: list[Stage] = []
    for i, s in enumerate(raw_stages):
        where = f"settings.yaml cadence.stages[{i}]"
        stages.append(
            Stage(
                id=str(_require(s, "id", where)),
                days_past_due=int(_require(s, "days_past_due", where)),
                template=str(s.get("template") or s.get("id")),
                cc=[str(c) for c in (s.get("cc") or [])],
                sends_email=bool(s.get("sends_email", True)),
                label=str(s.get("label") or s.get("id")),
            )
        )
    stages.sort(key=lambda s: s.days_past_due)
    if not stages:
        raise ConfigError("settings.yaml: cadence.stages is empty")
    if len({s.id for s in stages}) != len(stages):
        raise ConfigError("settings.yaml: duplicate stage ids in cadence.stages")

    return Settings(
        digest_to=str(_require(identities, "digest_to", "settings.yaml identities")),
        billing_from=str(_require(identities, "billing_from", "settings.yaml identities")),
        billing_reply_to=str(
            identities.get("billing_reply_to") or identities["billing_from"]
        ),
        escalation_cc=str(identities.get("escalation_cc") or ""),
        approval_ttl_hours=int(policy.get("approval_ttl_hours", 36)),
        send_weekdays=[int(d) for d in (policy.get("send_weekdays") or [0, 1, 2, 3, 4])],
        payment_terms_days=int(defaults.get("payment_terms_days", 15)),
        currency=str(defaults.get("currency", "USD")),
        stages=stages,
        stacked_ar_threshold=int(rules.get("stacked_ar_open_invoice_threshold", 2)),
        stacked_ar_distinct_months=bool(rules.get("stacked_ar_requires_distinct_months", True)),
        contract_change_lookahead_days=int(rules.get("contract_change_lookahead_days", 30)),
        unapplied_lookback_days=int(rules.get("unapplied_payment_lookback_days", 21)),
        unapplied_tolerance_pct=_as_decimal(
            rules.get("unapplied_match_tolerance_pct", 2.0), "rules"
        ) or Decimal("0"),
        unapplied_tolerance_abs=_as_decimal(
            rules.get("unapplied_match_tolerance_abs", 25), "rules"
        ) or Decimal("0"),
        short_pay_review_floor=_as_decimal(
            rules.get("short_pay_review_floor", 50), "rules"
        ) or Decimal("0"),
        minimum_reminder_balance=_as_decimal(
            rules.get("minimum_reminder_balance", 5), "rules"
        ) or Decimal("0"),
        bank_feed_accounts=[str(a) for a in (rules.get("bank_feed_accounts") or [])],
        flat_fee_patterns=[str(p).lower() for p in (lines.get("flat_fee_patterns") or [])],
        commission_patterns=[str(p).lower() for p in (lines.get("commission_patterns") or [])],
        digest_subject_prefix=str(digest.get("subject_prefix", "AR digest")),
        max_rows_per_section=int(digest.get("max_rows_per_section", 25)),
        timezone=str(digest.get("timezone", "America/Los_Angeles")),
        raw=data,
    )


def _parse_client(entry: dict[str, Any], index: int) -> ClientTerms:
    where = f"clients.yaml clients[{index}]"
    if not isinstance(entry, dict):
        raise ConfigError(f"{where}: entry is not a mapping")
    name = str(_require(entry, "name", where)).strip()

    periods: list[TermPeriod] = []
    for j, t in enumerate(entry.get("terms") or []):
        tw = f"{where} ({name}) terms[{j}]"
        start = _as_date(_require(t, "effective_from", tw), tw)
        end = _as_date(t.get("effective_to"), tw)
        if end and start and end < start:
            raise ConfigError(f"{tw}: effective_to is before effective_from")
        periods.append(
            TermPeriod(
                effective_from=start,
                effective_to=end,
                flat_fee_monthly=_as_decimal(t.get("flat_fee_monthly"), tw),
                commission_pct=_as_decimal(t.get("commission_pct"), tw),
                commission_basis=str(t.get("commission_basis") or ""),
                notes=str(t.get("notes") or "").strip(),
            )
        )
    periods.sort(key=lambda p: p.effective_from)

    # Overlapping periods make "which terms applied" ambiguous — refuse to guess.
    for a, b in zip(periods, periods[1:]):
        if a.effective_to is None or b.effective_from <= a.effective_to:
            raise ConfigError(
                f"{where} ({name}): terms periods overlap or the earlier one never "
                f"ends ({a.effective_from} → {a.effective_to}, then {b.effective_from}). "
                "Close the earlier period with effective_to."
            )

    # A step-down is the start of any period after the first.
    step_downs = [p.effective_from for p in periods[1:]]

    raw_range = entry.get("expected_total_range")
    total_range: tuple[Decimal, Decimal] | None = None
    if raw_range:
        if not isinstance(raw_range, list) or len(raw_range) != 2:
            raise ConfigError(f"{where} ({name}): expected_total_range must be [low, high]")
        low = _as_decimal(raw_range[0], where)
        high = _as_decimal(raw_range[1], where)
        if low is None or high is None or low > high:
            raise ConfigError(f"{where} ({name}): expected_total_range is not a valid band")
        total_range = (low, high)

    status = entry.get("status") or {}
    reminders = str(status.get("reminders", "active")).lower().strip()
    if reminders not in ("active", "suppressed"):
        raise ConfigError(
            f"{where} ({name}): status.reminders must be 'active' or 'suppressed', "
            f"got '{reminders}'"
        )

    credit_raw = entry.get("credit_balance")
    credit = None
    if credit_raw:
        amount = _as_decimal(_require(credit_raw, "amount", f"{where} credit_balance"), where)
        credit = CreditBalance(
            amount=amount or Decimal("0.00"),
            as_of=_as_date(credit_raw.get("as_of"), where),
            note=str(credit_raw.get("note") or "").strip(),
        )

    return ClientTerms(
        name=name,
        qbo_customer_id=(str(entry["qbo_customer_id"]).strip() if entry.get("qbo_customer_id") else None),
        aliases=[str(a) for a in (entry.get("aliases") or [])],
        billing_contact=(str(entry["billing_contact"]).strip() if entry.get("billing_contact") else None),
        payment_terms_days=(int(entry["payment_terms_days"]) if entry.get("payment_terms_days") else None),
        currency=(str(entry["currency"]) if entry.get("currency") else None),
        fx_rate=_as_decimal(entry.get("fx_rate"), where),
        terms=periods,
        sunset_date=_as_date(entry.get("sunset_date"), where),
        step_down_dates=step_downs,
        expected_total_range=total_range,
        reminders_suppressed=(reminders == "suppressed"),
        suppression_reason=str(status.get("reason") or "").strip(),
        credit_balance=credit,
        flags=[f for f in (entry.get("flags") or []) if isinstance(f, dict)],
    )


def load_config(config_dir: str | Path | None = None) -> Config:
    """Read both config files. Raises ConfigError on anything unusable."""
    directory = Path(config_dir or os.environ.get("AR_CONFIG_DIR") or DEFAULT_CONFIG_DIR)
    settings = _parse_settings(_load_yaml(directory / "settings.yaml"))

    client_data = _load_yaml(directory / "clients.yaml")
    raw_clients = client_data.get("clients") or []
    if not isinstance(raw_clients, list):
        raise ConfigError("clients.yaml: 'clients' must be a list")

    clients = [_parse_client(entry, i) for i, entry in enumerate(raw_clients)]

    seen: set[str] = set()
    for client in clients:
        for key in client.match_names:
            if key in seen:
                raise ConfigError(
                    f"clients.yaml: '{key}' is used as a name or alias by two clients"
                )
            seen.add(key)

    return Config(settings=settings, clients=clients, config_dir=directory)
