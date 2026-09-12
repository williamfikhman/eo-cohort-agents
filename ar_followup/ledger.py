"""Append-only record of everything the agent proposed, was told, and sent.

Three jobs:

* idempotency — one reminder per invoice per stage, ever, even if the run is
  triggered twice in a morning
* audit — every approval decision and every sent reminder, with who and when
* week-over-week — a daily AR snapshot, so the digest's trend line is a
  measurement rather than a recollection

The ledger is plain JSONL. It is meant to be greppable at 6 AM without tooling.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from .models import Snapshot, money

DEFAULT_STATE_DIR = Path(os.environ.get("AR_STATE_DIR", "state"))


def _encode(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _encode(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    return value


def new_digest_id(as_of: date) -> str:
    return f"{as_of.isoformat()}-{uuid.uuid4().hex[:6]}"


class Ledger:
    def __init__(self, state_dir: str | Path | None = None):
        self.dir = Path(state_dir or DEFAULT_STATE_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "digests").mkdir(exist_ok=True)
        self.path = self.dir / "ledger.jsonl"
        self.snapshots_path = self.dir / "snapshots.jsonl"

    # --- writing -----------------------------------------------------------

    def append(self, record_type: str, **fields: Any) -> dict[str, Any]:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "type": record_type,
            **{k: _encode(v) for k, v in fields.items()},
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        return record

    def record_digest(self, digest_id: str, as_of: date, payload: dict[str, Any]) -> Path:
        path = self.dir / "digests" / f"{digest_id}.json"
        path.write_text(json.dumps(_encode(payload), indent=2), encoding="utf-8")
        self.append(
            "digest",
            digest_id=digest_id,
            as_of=as_of,
            reminder_count=len(payload.get("reminders", [])),
            flag_count=len(payload.get("flags", [])),
            unapplied_count=len(payload.get("unapplied", [])),
        )
        return path

    def load_digest(self, digest_id: str) -> dict[str, Any] | None:
        path = self.dir / "digests" / f"{digest_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def latest_digest_id(self) -> str | None:
        digests = sorted((self.dir / "digests").glob("*.json"))
        return digests[-1].stem if digests else None

    def record_approval(
        self, digest_id: str, ref: str, decision: str, approver: str, source: str, detail: str = ""
    ) -> None:
        self.append(
            "approval",
            digest_id=digest_id,
            ref=ref,
            decision=decision,
            approver=approver,
            source=source,
            detail=detail,
        )

    def record_send(
        self,
        digest_id: str,
        invoice_id: str,
        stage_id: str,
        customer: str,
        to: str,
        cc: list[str],
        subject: str,
        message_id: str,
        approver: str,
    ) -> None:
        self.append(
            "send",
            digest_id=digest_id,
            invoice_id=invoice_id,
            stage_id=stage_id,
            customer=customer,
            to=to,
            cc=cc,
            subject=subject,
            message_id=message_id,
            approver=approver,
        )

    def record_snapshot(self, snapshot: Snapshot) -> None:
        with self.snapshots_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_encode(snapshot)) + "\n")

    # --- reading -----------------------------------------------------------

    def records(self, record_type: str | None = None) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a torn write never blocks a morning run
                if record_type is None or record.get("type") == record_type:
                    yield record

    def sent_stages(self) -> dict[str, set[str]]:
        """invoice_id -> {stage_id, ...} already sent. The idempotency guard."""
        sent: dict[str, set[str]] = {}
        for record in self.records("send"):
            sent.setdefault(str(record.get("invoice_id")), set()).add(str(record.get("stage_id")))
        return sent

    def approvals_for(self, digest_id: str) -> dict[str, dict[str, Any]]:
        """Latest decision per ref for one digest. A later reply overrides an earlier one."""
        decisions: dict[str, dict[str, Any]] = {}
        for record in self.records("approval"):
            if record.get("digest_id") == digest_id:
                decisions[str(record.get("ref"))] = record
        return decisions

    def snapshots(self) -> list[Snapshot]:
        if not self.snapshots_path.exists():
            return []
        out: list[Snapshot] = []
        with self.snapshots_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                out.append(
                    Snapshot(
                        as_of=date.fromisoformat(row["as_of"]),
                        total_ar=money(row["total_ar"]),
                        past_due_ar=money(row["past_due_ar"]),
                        open_invoice_count=int(row["open_invoice_count"]),
                        past_due_count=int(row["past_due_count"]),
                    )
                )
        return out

    def snapshot_near(self, target: date, tolerance_days: int = 3) -> Snapshot | None:
        """The snapshot closest to `target`, for the week-over-week line."""
        best: Snapshot | None = None
        best_gap = tolerance_days + 1
        for snapshot in self.snapshots():
            gap = abs((snapshot.as_of - target).days)
            if gap <= tolerance_days and gap < best_gap:
                best, best_gap = snapshot, gap
        return best
