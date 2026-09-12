#!/usr/bin/env python3
"""Step 1: turn a raw Google Calendar events dump into the day's meeting list.

Reads the JSON the calendar MCP server returns (either the whole response
object with an "events" key, or a bare list of events), applies the selection
rules from the spec, and prints a console table plus a JSON document.

Rules (spec section 3):
  include  - at least one external attendee (domain != INTERNAL_DOMAIN), or a
             Zoom/Meet/Teams link whose organizer is external
  exclude  - all-day events, anything the owner declined, cancelled events,
             internal standups, personal blocks (no attendees, no external link)

Usage:
  python3 bin/filter_events.py --date 2026-09-14 < events.json
  python3 bin/filter_events.py --date 2026-09-14 --in logs/2026-09-14.events.json --json

Deterministic on purpose: the model reads the calendar, saves the raw JSON,
and this script decides. No guessing about who is "external".
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

INTERNAL_DOMAIN = "marketplaceofficer.com"
SELF_EMAIL = "william@marketplaceofficer.com"
TZ = "America/Los_Angeles"

FREE_MAIL = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com",
    "aol.com", "protonmail.com", "proton.me", "me.com", "live.com", "msn.com",
}

MEETING_LINK_RE = re.compile(
    r"https?://(?:[\w.-]*zoom\.us/j/[^\s\"'<>]+|meet\.google\.com/[\w-]+"
    r"|teams\.microsoft\.com/l/meetup-join/[^\s\"'<>]+)",
    re.I,
)
BOOKING_RE = re.compile(r"https?://[\w.-]*hubspot\.com/meetings/([\w-]+)", re.I)
COMPANY_RE = re.compile(r"Company name:\s*([^\n<]+)", re.I)
PHONE_RE = re.compile(r"(?:Mobile phone number|Phone):\s*([+\d][\d\s().-]{6,})", re.I)
# "KNJ & William from Chief Marketplace Officer", "Laleva & Chief Marketplace Officer-Amazon (...)"
TITLE_COMPANY_RE = re.compile(
    r"^\s*(?:Canceled:|Cancelled:)?\s*(.+?)\s*&\s*(?:William|Will|Chief Marketplace Officer)", re.I
)


def strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return html_lib.unescape(text)


def domain_of(email: str) -> str:
    return (email or "").rsplit("@", 1)[-1].lower().strip()


def is_internal(email: str) -> bool:
    d = domain_of(email)
    return d == INTERNAL_DOMAIN or d.endswith("." + INTERNAL_DOMAIN)


def is_group_calendar(email: str) -> bool:
    return domain_of(email).endswith("group.calendar.google.com") or domain_of(email).endswith(
        "resource.calendar.google.com"
    )


def local_dt(value: dict, tz: ZoneInfo) -> datetime | None:
    raw = (value or {}).get("dateTime")
    if not raw:
        return None
    raw = raw.replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def guess_company(event: dict, external: list[dict]) -> str:
    desc = strip_html(event.get("description", ""))
    m = COMPANY_RE.search(desc)
    if m:
        name = m.group(1).strip()
        # HubSpot booking forms sometimes carry an EIN or junk instead of a name.
        if not re.fullmatch(r"[\d\s-]+", name):
            return name
    m = TITLE_COMPANY_RE.match(event.get("summary", "") or "")
    if m:
        return m.group(1).strip()
    for a in external:
        d = domain_of(a["email"])
        if d and d not in FREE_MAIL:
            return d.split(".")[0].capitalize()
    if external:
        return external[0].get("name") or external[0]["email"]
    return event.get("summary", "").strip() or "Unknown"


def classify(event: dict, target: str, tz: ZoneInfo) -> dict:
    """Return a record with `bucket` in {external, internal, excluded}."""
    summary = (event.get("summary") or "").strip()
    start = local_dt(event.get("start", {}), tz)
    end = local_dt(event.get("end", {}), tz)
    rec = {
        "id": event.get("id"),
        "summary": summary,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "html_link": event.get("htmlLink"),
    }

    if event.get("status") == "cancelled" or re.match(r"^(canceled|cancelled):", summary, re.I):
        return {**rec, "bucket": "excluded", "reason": "cancelled"}
    if not start:
        return {**rec, "bucket": "excluded", "reason": "all-day event"}
    if start.strftime("%Y-%m-%d") != target:
        return {**rec, "bucket": "excluded", "reason": "not on target date"}

    attendees = [a for a in event.get("attendees", []) if a.get("email")]
    me = next((a for a in attendees if a.get("self") or a["email"].lower() == SELF_EMAIL), None)
    my_status = (me or {}).get("responseStatus", "accepted" if not attendees else "needsAction")
    if my_status == "declined":
        return {**rec, "bucket": "excluded", "reason": "declined by owner"}

    external = [
        {"email": a["email"], "name": a.get("displayName", ""), "response": a.get("responseStatus", "")}
        for a in attendees
        if not is_internal(a["email"]) and not is_group_calendar(a["email"])
    ]
    internal = [a["email"] for a in attendees if is_internal(a["email"])]

    text_blob = " ".join([event.get("location") or "", event.get("description") or ""])
    links = list(dict.fromkeys(m.group(0).rstrip(".,") for m in MEETING_LINK_RE.finditer(text_blob)))
    organizer = (event.get("organizer") or {}).get("email", "")
    organizer_external = bool(organizer) and not is_internal(organizer) and not is_group_calendar(organizer)

    desc_text = strip_html(event.get("description", ""))
    booking = BOOKING_RE.search(text_blob)
    phone = PHONE_RE.search(desc_text)

    duration = int((end - start).total_seconds() // 60) if end else None
    base = {
        **rec,
        "duration_min": duration,
        "meeting_link": links[0] if links else None,
        "my_response": my_status,
        "internal_attendees": internal,
    }

    if external or (links and organizer_external):
        return {
            **base,
            "bucket": "external",
            "company": guess_company(event, external),
            "external_attendees": external,
            "organizer": organizer,
            "booking_link": f"HubSpot meeting link ({booking.group(1)})" if booking else None,
            "phone": phone.group(1).strip() if phone else None,
            "description_text": desc_text.strip()[:1500],
        }

    if not attendees:
        return {**base, "bucket": "internal", "kind": "personal block"}
    return {**base, "bucket": "internal", "kind": "internal meeting"}


def dedupe(external: list[dict]) -> None:
    """Same company + same external attendees booked twice -> research once."""
    seen: dict[tuple, str] = {}
    for m in external:
        key = (m["company"].lower(), tuple(sorted(a["email"].lower() for a in m["external_attendees"])))
        if key in seen:
            m["duplicate_of"] = seen[key]
        else:
            seen[key] = m["id"]


def run(events: list[dict], target: str, tz_name: str) -> dict:
    tz = ZoneInfo(tz_name)
    buckets: dict[str, list] = {"external": [], "internal": [], "excluded": []}
    for ev in events:
        rec = classify(ev, target, tz)
        buckets[rec["bucket"]].append(rec)
    for b in buckets.values():
        b.sort(key=lambda r: r.get("start") or "")
    dedupe(buckets["external"])
    return {
        "date": target,
        "timezone": tz_name,
        "external": buckets["external"],
        "internal": buckets["internal"],
        "excluded": buckets["excluded"],
    }


def fmt_time(iso: str | None) -> str:
    if not iso:
        return "--:--"
    return datetime.fromisoformat(iso).strftime("%-I:%M %p")


def print_console(result: dict) -> None:
    print(f"\n=== {result['date']} ({result['timezone']}) ===")
    ext = result["external"]
    print(f"\nEXTERNAL MEETINGS: {len(ext)}")
    for m in ext:
        dup = f"  (duplicate booking of {m['duplicate_of']})" if m.get("duplicate_of") else ""
        print(f"  {fmt_time(m['start'])}-{fmt_time(m['end'])}  {m['company']}  |  {m['summary']}{dup}")
        for a in m["external_attendees"]:
            print(f"      - {a['name'] or '?'} <{a['email']}>  [{a['response'] or 'no response'}]")
        if m.get("meeting_link"):
            print(f"      link: {m['meeting_link']}")
        if m.get("booking_link"):
            print(f"      via:  {m['booking_link']}")
    print(f"\nINTERNAL / PERSONAL (one-line list): {len(result['internal'])}")
    print("  " + "; ".join(f"{fmt_time(m['start'])} {m['summary']}" + (" (tentative)" if m['my_response'] == 'tentative' else "") for m in result["internal"]))
    print(f"\nEXCLUDED: {len(result['excluded'])}")
    for m in result["excluded"]:
        print(f"  - {m['summary'] or '(untitled)'}: {m['reason']}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", required=True, help="target day, YYYY-MM-DD (in --tz)")
    ap.add_argument("--tz", default=TZ)
    ap.add_argument("--in", dest="infile", help="events JSON file (default: stdin)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of the console table")
    ap.add_argument("--out", help="also write the JSON result here")
    args = ap.parse_args()

    raw = open(args.infile, encoding="utf-8").read() if args.infile else sys.stdin.read()
    data = json.loads(raw)
    events = data.get("events", data.get("items", [])) if isinstance(data, dict) else data

    result = run(events, args.date, args.tz)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_console(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
