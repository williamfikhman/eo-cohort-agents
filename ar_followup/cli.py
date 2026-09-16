"""Command line for the AR follow-up agent.

    python -m ar_followup validate-config
    python -m ar_followup auth --refresh-token ... --realm-id ...
    python -m ar_followup scan --no-email --print
    python -m ar_followup send-approved --dry-run
    python -m ar_followup status
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from .config import ConfigError, load_config
from .ledger import Ledger
from .qbo.auth import QboAuth, QboAuthError
from .run import scan, send_approved


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def cmd_validate(args) -> int:
    config = load_config(args.config_dir)
    settings = config.settings
    print(f"config ok — {len(config.clients)} clients on file")
    print(f"  digest to        {settings.digest_to}")
    print(f"  sending as       {settings.billing_from}")
    print(f"  default terms    Net {settings.payment_terms_days}")
    stages = ", ".join(
        f"+{s.days_past_due} {s.id}{'' if s.sends_email else ' (no email)'}"
        for s in settings.stages
    )
    print(f"  cadence          {stages}")
    suppressed = [c for c in config.clients if c.reminders_suppressed]
    if suppressed:
        print(f"  suppressed       {', '.join(c.name for c in suppressed)}")
    credits = [c for c in config.clients if c.credit_balance and c.credit_balance.amount > 0]
    for client in credits:
        print(f"  credit on file   {client.name}: ${client.credit_balance.amount:,.2f}")
    return 0


def cmd_auth(args) -> int:
    auth = QboAuth.from_env()
    token = auth.bootstrap(args.refresh_token, args.realm_id)
    print(f"stored a working token for realm {token.realm_id} at {auth.store.path}")
    print("back this file up: the refresh token rotates and the old one stops working")
    return 0


def cmd_scan(args) -> int:
    config = load_config(args.config_dir)
    ledger = Ledger(args.state_dir)
    payload = scan(
        config,
        ledger,
        today=_parse_date(args.date),
        email=not args.no_email,
    )
    if args.print_digest:
        print(payload["_text"])
    print(
        f"\ndigest {payload['digest_id']}: "
        f"{len(payload['matches'])} matches proposed, "
        f"{len(payload['reminders'])} reminders proposed, "
        f"{len(payload['flags'])} flags, "
        f"{len(payload['unexplained'])} unexplained payments"
        + ("" if args.no_email else f" — emailed to {config.settings.digest_to}")
    )
    if payload["errors"]:
        print(f"with {len(payload['errors'])} error(s); see the digest")
    return 0


def cmd_send_approved(args) -> int:
    config = load_config(args.config_dir)
    ledger = Ledger(args.state_dir)
    outcomes = send_approved(
        config,
        ledger,
        digest_id=args.digest,
        approve_refs=args.approve,
        confirm_refs=args.confirm,
        dry_run=args.dry_run,
        today=_parse_date(args.date),
    )
    for outcome in outcomes:
        print(
            f"  {outcome.status.upper():<8} {outcome.ref:<4} "
            f"{outcome.customer[:28]:<28} {outcome.invoice_label:<10} {outcome.detail}"
        )
    sent = sum(1 for o in outcomes if o.status == "sent")
    held = sum(1 for o in outcomes if o.status == "held")
    failed = sum(1 for o in outcomes if o.status == "failed")
    confirmed = sum(1 for o in outcomes if o.status == "confirmed")
    print(
        f"\n{confirmed} matches confirmed / {sent} reminders sent / "
        f"{held} held / {failed} failed"
    )
    return 1 if failed else 0


def cmd_status(args) -> int:
    ledger = Ledger(args.state_dir)
    digest_id = ledger.latest_digest_id()
    print(f"state dir      {ledger.dir}")
    print(f"latest digest  {digest_id or 'none yet'}")
    sends = list(ledger.records("send"))
    print(f"reminders sent {len(sends)} all time")
    for record in sends[-5:]:
        print(f"  {record['ts']}  {record['customer']}  {record['stage_id']}  → {record['to']}")
    snapshots = ledger.snapshots()
    if snapshots:
        latest = snapshots[-1]
        print(
            f"last snapshot  {latest.as_of}: ${latest.total_ar:,.2f} AR, "
            f"${latest.past_due_ar:,.2f} past due"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ar_followup", description=__doc__)
    parser.add_argument("--config-dir", default=None, help="defaults to ./config")
    parser.add_argument("--state-dir", default=None, help="defaults to ./state")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate-config", help="parse both YAML files and print a summary").set_defaults(
        func=cmd_validate
    )

    auth = sub.add_parser("auth", help="store a QBO refresh token once")
    auth.add_argument("--refresh-token", required=True)
    auth.add_argument("--realm-id", required=True)
    auth.set_defaults(func=cmd_auth)

    scan_cmd = sub.add_parser("scan", help="the morning run: read QBO, email the digest")
    scan_cmd.add_argument("--date", help="pretend it is this date (YYYY-MM-DD)")
    scan_cmd.add_argument("--no-email", action="store_true", help="build the digest, do not send it")
    scan_cmd.add_argument("--print", dest="print_digest", action="store_true", help="print the digest")
    scan_cmd.set_defaults(func=cmd_scan)

    send = sub.add_parser("send-approved", help="send the reminders William approved")
    send.add_argument("--digest", help="digest id; defaults to the most recent")
    send.add_argument("--approve", nargs="*", help="approve these reminders from the terminal, e.g. R1 R3")
    send.add_argument("--confirm", nargs="*", help="confirm these matches from the terminal, e.g. M1 M2")
    send.add_argument("--date", help="pretend it is this date (YYYY-MM-DD)")
    send.add_argument("--dry-run", action="store_true", help="verify everything, send nothing")
    send.set_defaults(func=cmd_send_approved)

    sub.add_parser("status", help="what the ledger knows").set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2
    except QboAuthError as exc:
        print(f"QBO AUTH ERROR: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
