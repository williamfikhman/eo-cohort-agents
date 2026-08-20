#!/usr/bin/env python3
"""Fallback path: turn exported idea-sheet rows into agent files on main.

For anyone whose GitHub push failed on workshop night. Export the
"EO Agent Ideas — Aug 20" sheet to CSV (File -> Download -> CSV), then:

    python runner/ingest_sheet.py ideas.csv            # write + commit + push to main
    python runner/ingest_sheet.py ideas.csv --dry-run  # show what it would do
    python runner/ingest_sheet.py ideas.csv --no-commit

A row is ingested only if it has (a) a usable email address and (b) a body that
looks like a RAFT (contains a TASK section). Everything else is reported and
skipped. Existing agent files are never overwritten unless --force is passed —
a founder who successfully opened a PR keeps their own version.
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"

EMAIL_RE = re.compile(r"[^@\s,;]+@[^@\s,;]+\.[A-Za-z]{2,}")

# Header substrings we'll accept for each field, most specific first.
HEADER_HINTS = {
    "email": ("email", "e-mail", "mail"),
    "name": ("your name", "name", "founder", "member"),
    "agent": ("agent name", "agent", "slug", "title"),
    "raft": ("raft", "agent idea", "idea", "task", "prompt", "description"),
    "schedule": ("schedule", "cadence", "when", "frequency"),
}


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:60] or "unnamed-agent"


def pick_columns(headers: list[str]) -> dict[str, str | None]:
    """Map our field names onto whatever the sheet's headers happen to be."""
    lowered = {h: (h or "").strip().lower() for h in headers}
    chosen: dict[str, str | None] = {}
    for field, hints in HEADER_HINTS.items():
        match = None
        for hint in hints:
            for header, low in lowered.items():
                if hint in low and header not in chosen.values():
                    match = header
                    break
            if match:
                break
        chosen[field] = match
    return chosen


def find_email(row: dict[str, str], col: str | None) -> str | None:
    """Prefer the mapped column; otherwise scan every cell for an address."""
    if col and row.get(col):
        found = EMAIL_RE.search(row[col])
        if found:
            return found.group(0)
    for value in row.values():
        if not value:
            continue
        found = EMAIL_RE.search(value)
        if found:
            return found.group(0)
    return None


def find_raft(row: dict[str, str], col: str | None) -> str | None:
    """Prefer the mapped column; otherwise take the longest RAFT-looking cell."""
    candidates: list[str] = []
    if col and row.get(col):
        candidates.append(row[col])
    candidates.extend(v for v in row.values() if v and v not in candidates)
    for value in candidates:
        if "task" in value.lower() and len(value.strip()) > 40:
            return value.strip()
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path", help="CSV exported from the EO Agent Ideas sheet")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--no-commit", action="store_true", help="write files but don't commit")
    ap.add_argument("--force", action="store_true", help="overwrite existing agent files")
    args = ap.parse_args()

    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        print(f"FATAL: {csv_path} not found", file=sys.stderr)
        return 1

    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        cols = pick_columns(headers)
        rows = list(reader)

    print(f"Read {len(rows)} row(s) from {csv_path.name}")
    print(f"Column mapping: {cols}\n")

    written: list[Path] = []
    skipped: list[tuple[int, str]] = []

    for i, row in enumerate(rows, start=2):  # row 1 is the header
        row = {k: (v or "").strip() for k, v in row.items() if k}

        email = find_email(row, cols["email"])
        if not email:
            skipped.append((i, "no email address in the row"))
            continue

        raft = find_raft(row, cols["raft"])
        if not raft:
            skipped.append((i, "no RAFT-shaped body (needs a TASK section)"))
            continue

        name = (row.get(cols["name"] or "", "") or email.split("@")[0]).strip()
        agent_raw = (row.get(cols["agent"] or "", "") or f"{name} agent").strip()
        agent = slugify(agent_raw)
        schedule = (row.get(cols["schedule"] or "", "") or "Weekly").strip()

        target = AGENTS_DIR / f"{agent}.md"
        if target.exists() and not args.force:
            skipped.append((i, f"{target.name} already exists (use --force to overwrite)"))
            continue

        content = (
            "---\n"
            f"name: {name}\n"
            f"email: {email}\n"
            f"agent: {agent}\n"
            f"schedule: {schedule}\n"
            "---\n\n"
            f"{raft}\n"
        )

        if args.dry_run:
            print(f"WOULD WRITE  {target.name}  ({name} <{email}>)")
        else:
            target.write_text(content, encoding="utf-8")
            print(f"WROTE        {target.name}  ({name} <{email}>)")
        written.append(target)

    print(f"\n{len(written)} written / {len(skipped)} skipped")
    for line_no, reason in skipped:
        print(f"  row {line_no}: {reason}")

    if args.dry_run or args.no_commit or not written:
        if written and (args.dry_run or args.no_commit):
            print("\n(not committing)")
        return 0

    subprocess.run(["git", "add", *[str(p) for p in written]], check=True)
    subprocess.run(
        ["git", "commit", "-m",
         f"agents: ingest {len(written)} agent(s) from the EO idea sheet"],
        check=True,
    )
    subprocess.run(["git", "push", "origin", "HEAD:main"], check=True)
    print(f"\nCommitted and pushed {len(written)} agent file(s) to main.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
