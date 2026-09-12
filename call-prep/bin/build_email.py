#!/usr/bin/env python3
"""Step 4: render the brief JSON into the HTML email (+ plain text) and subject.

    python3 bin/build_email.py logs/2026-09-14.brief.json            # writes out/<date>.html, .txt, prints subject
    python3 bin/build_email.py logs/2026-09-14.brief.json --print    # also dump the text version to console

The model writes the *content* (hooks, questions, objections); this script
owns the *layout*, so every brief looks the same on a phone, links are always
clickable, and meetings are always in chronological order (ties broken by
`priority`, lower first).

Brief JSON schema (all strings are plain text; links go in `links`):
{
  "date": "2026-09-14", "timezone": "America/Los_Angeles", "mode": "full|supplement",
  "summary": ["line 1", "line 2", "line 3"],
  "meetings": [{
    "start": "08:45", "end": "09:00", "duration_min": 15, "priority": 1,
    "company": "KNJ", "title": "KNJ & William ...", "meeting_link": "https://zoom.us/...",
    "also_at": ["10:15"],                       # duplicate bookings, optional
    "booking_link": "HubSpot meeting link (wfikhman)",
    "attendees": [{"name": "...", "title": "...", "email": "...", "linkedin": "https://..."}],
    "hook": ["...", "..."],
    "status": "one sentence",
    "links": {"amazon_search": "...", "storefront": null, "site": "...", "hubspot_contact": "...", "hubspot_deal": "...", "other": [{"label": "...", "url": "..."}]},
    "questions": ["...", "...", "..."],
    "objections": [{"objection": "...", "response": "..."}],
    "history": "one or two sentences, or null",
    "sources": [{"label": "...", "url": "..."}],
    "unavailable": ["hubspot", "amazon"]        # sections that failed, optional
  }],
  "internal": ["8:00 PM Huddle", "11:00 Sales Meeting: Will & Angie"],
  "failures": ["calendar: ..."]
}
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def a(url: str | None, label: str | None = None) -> str:
    if not url:
        return ""
    return f'<a href="{esc(url)}" style="color:#0b57d0;text-decoration:none">{esc(label or url)}</a>'


def fmt_time(hhmm: str) -> str:
    try:
        return datetime.strptime(hhmm, "%H:%M").strftime("%-I:%M %p")
    except Exception:
        return hhmm or ""


def subject_for(brief: dict) -> str:
    d = datetime.strptime(brief["date"], "%Y-%m-%d")
    n = len(brief.get("meetings", []))
    prefix = "Call Prep Supplement" if brief.get("mode") == "supplement" else "Call Prep"
    noun = "meeting" if n == 1 else "meetings"
    return f"{prefix} — {d.strftime('%a, %b %-d')} — {n} {noun}"


def sorted_meetings(brief: dict) -> list[dict]:
    return sorted(brief.get("meetings", []), key=lambda m: (m.get("start") or "99:99", m.get("priority", 99)))


def render_meeting(m: dict) -> str:
    when = f"{fmt_time(m.get('start'))}–{fmt_time(m.get('end'))}"
    if m.get("duration_min"):
        when += f" ({m['duration_min']} min)"
    if m.get("also_at"):
        when += " · also booked " + ", ".join(fmt_time(t) for t in m["also_at"])
    attendees = "; ".join(
        " ".join(x for x in [esc(p.get("name") or p.get("email")), f"({esc(p['title'])})" if p.get("title") else "", f"— {a(p['linkedin'], 'LinkedIn')}" if p.get("linkedin") else ""] if x)
        for p in m.get("attendees", [])
    )
    unavailable = m.get("unavailable") or []
    hooks = "".join(f"<li>{esc(h)}</li>" for h in m.get("hook", [])) or "<li><i>[unavailable]</i></li>"
    links = m.get("links", {}) or {}
    link_items = [
        a(links.get("amazon_search"), "Amazon search"),
        a(links.get("storefront"), "Amazon Storefront") if links.get("storefront") else '<span style="color:#888">no Storefront found</span>' if "amazon" not in unavailable else "",
        a(links.get("site"), "Their site"),
        a(links.get("hubspot_contact"), "HubSpot contact"),
        a(links.get("hubspot_deal"), "HubSpot deal"),
        *[a(o.get("url"), o.get("label")) for o in links.get("other", []) or []],
    ]
    link_row = " · ".join(x for x in link_items if x)
    questions = "".join(f"<li>{esc(q)}</li>" for q in m.get("questions", []))
    objections = "".join(f"<li><b>{esc(o.get('objection'))}</b> — {esc(o.get('response'))}</li>" for o in m.get("objections", []))
    sources = " · ".join(a(s.get("url"), s.get("label")) for s in m.get("sources", []) if s.get("url"))
    unavailable_note = f'<p style="margin:6px 0;color:#b00020;font-size:13px">[unavailable]: {esc(", ".join(unavailable))}</p>' if unavailable else ""

    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e3e3e3;border-radius:8px;margin:0 0 18px 0">
<tr><td style="padding:14px 16px">
  <div style="font-size:13px;color:#555">{esc(when)}</div>
  <div style="font-size:19px;font-weight:700;margin:2px 0 4px 0">{esc(m.get('company'))}</div>
  <div style="font-size:13px;color:#333">{attendees or '<i>no external attendee listed</i>'}</div>
  <div style="font-size:13px;margin:4px 0 0 0">{a(m.get('meeting_link'), 'Join call')}{(' · ' + esc(m['booking_link'])) if m.get('booking_link') else ''}</div>
  {unavailable_note}
  <div style="margin:12px 0 0 0;font-size:12px;letter-spacing:.06em;color:#777;text-transform:uppercase">The hook</div>
  <ul style="margin:4px 0 0 18px;padding:0;font-size:15px;line-height:1.4">{hooks}</ul>
  <p style="margin:12px 0 0 0;font-size:14px"><b>Status:</b> {esc(m.get('status') or '[unavailable]')}</p>
  <p style="margin:8px 0 0 0;font-size:14px">{link_row}</p>
  <div style="margin:12px 0 0 0;font-size:12px;letter-spacing:.06em;color:#777;text-transform:uppercase">Discovery questions</div>
  <ol style="margin:4px 0 0 18px;padding:0;font-size:14px;line-height:1.4">{questions}</ol>
  <div style="margin:12px 0 0 0;font-size:12px;letter-spacing:.06em;color:#777;text-transform:uppercase">Likely objections</div>
  <ul style="margin:4px 0 0 18px;padding:0;font-size:14px;line-height:1.4">{objections}</ul>
  {f'<p style="margin:12px 0 0 0;font-size:14px"><b>History with us:</b> {esc(m["history"])}</p>' if m.get('history') else ''}
  {f'<p style="margin:10px 0 0 0;font-size:12px;color:#666">Sources: {sources}</p>' if sources else ''}
</td></tr></table>"""


def render_html(brief: dict) -> str:
    d = datetime.strptime(brief["date"], "%Y-%m-%d")
    meetings = sorted_meetings(brief)
    summary = "".join(f"<div style=\"font-size:15px;line-height:1.45\">{esc(s)}</div>" for s in brief.get("summary", []))
    internal = brief.get("internal") or []
    failures = brief.get("failures") or []
    body = "".join(render_meeting(m) for m in meetings) if meetings else (
        '<p style="font-size:16px"><b>No external meetings today.</b></p>'
    )
    internal_line = f'<p style="font-size:13px;color:#555;margin:0"><b>Internal:</b> {esc("; ".join(internal))}</p>' if internal else ""
    failure_block = (
        '<p style="font-size:12px;color:#b00020;margin:12px 0 0 0">Data problems this run: ' + esc("; ".join(failures)) + "</p>"
    ) if failures else ""
    title = "Call Prep Supplement" if brief.get("mode") == "supplement" else "Call Prep"
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(subject_for(brief))}</title></head>
<body style="margin:0;padding:0;background:#f6f6f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#111">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:16px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:620px;background:#fff;border-radius:10px">
<tr><td style="padding:18px 16px 8px 16px">
  <div style="font-size:12px;letter-spacing:.08em;color:#777;text-transform:uppercase">{esc(title)} · {esc(d.strftime('%A, %B %-d, %Y'))}</div>
  <div style="margin:8px 0 14px 0">{summary}</div>
  {body}
  {internal_line}
  {failure_block}
</td></tr></table>
</td></tr></table></body></html>"""


def render_text(brief: dict) -> str:
    lines = [subject_for(brief), ""]
    lines += brief.get("summary", [])
    lines.append("")
    for m in sorted_meetings(brief):
        when = f"{fmt_time(m.get('start'))}-{fmt_time(m.get('end'))}"
        lines.append(f"## {when}  {m.get('company')}")
        for p in m.get("attendees", []):
            lines.append(f"   {p.get('name') or p.get('email')}{' (' + p['title'] + ')' if p.get('title') else ''}{'  ' + p['linkedin'] if p.get('linkedin') else ''}")
        if m.get("meeting_link"):
            lines.append(f"   join: {m['meeting_link']}")
        if m.get("unavailable"):
            lines.append(f"   [unavailable]: {', '.join(m['unavailable'])}")
        lines.append("   HOOK:")
        lines += [f"   - {h}" for h in m.get("hook", [])]
        lines.append(f"   STATUS: {m.get('status')}")
        links = m.get("links", {}) or {}
        for k in ("amazon_search", "storefront", "site", "hubspot_contact", "hubspot_deal"):
            if links.get(k):
                lines.append(f"   {k}: {links[k]}")
        for o in links.get("other", []) or []:
            lines.append(f"   {o.get('label')}: {o.get('url')}")
        lines.append("   QUESTIONS:")
        lines += [f"   {i}. {q}" for i, q in enumerate(m.get("questions", []), 1)]
        lines.append("   OBJECTIONS:")
        lines += [f"   - {o.get('objection')} -> {o.get('response')}" for o in m.get("objections", [])]
        if m.get("history"):
            lines.append(f"   HISTORY: {m['history']}")
        if m.get("sources"):
            lines.append("   SOURCES: " + " | ".join(s.get("url", "") for s in m["sources"]))
        lines.append("")
    if not brief.get("meetings"):
        lines.append("No external meetings today.")
    if brief.get("internal"):
        lines.append("Internal: " + "; ".join(brief["internal"]))
    if brief.get("failures"):
        lines.append("Data problems: " + "; ".join(brief["failures"]))
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("brief", help="path to logs/<date>.brief.json")
    ap.add_argument("--print", action="store_true", help="print the text version to console")
    ap.add_argument("--outdir", default=str(OUT))
    args = ap.parse_args()

    brief = json.loads(Path(args.brief).read_text(encoding="utf-8"))
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stem = brief["date"] + ("-supplement" if brief.get("mode") == "supplement" else "")
    html_path = outdir / f"{stem}.html"
    txt_path = outdir / f"{stem}.txt"
    html_path.write_text(render_html(brief), encoding="utf-8")
    txt_path.write_text(render_text(brief), encoding="utf-8")
    print(json.dumps({"subject": subject_for(brief), "html": str(html_path), "text": str(txt_path), "meetings": len(brief.get("meetings", []))}))
    if args.print:
        print()
        print(render_text(brief))
    return 0


if __name__ == "__main__":
    sys.exit(main())
