"""The daily digest: what the agent wants to send, and what it refuses to.

Written to be read on a phone, standing up, before coffee. Section order is
fixed so the shape is familiar every morning:

  1. reminders queued for approval
  2. flagged anomalies
  3. possible unapplied payments
  4. summary stats

Approval is a reply. `APPROVE R1 R3` sends those two; `APPROVE ALL` sends every
reminder in section 1. Anything not named is not sent — silence approves nothing.
"""

from __future__ import annotations

import html
import os
from decimal import Decimal

from .config import Settings
from .models import Flag, ReminderCandidate, RunResult, Snapshot, UnappliedCandidate

ZERO = Decimal("0.00")


def _money(value: Decimal | None) -> str:
    return f"${value:,.2f}" if value is not None else "—"


def approval_link(digest_id: str) -> str | None:
    base = os.environ.get("AR_APPROVAL_LINK_BASE")
    return f"{base.rstrip('/')}?digest={digest_id}" if base else None


def subject(result: RunResult, settings: Settings) -> str:
    bits = [f"{len(result.reminders)} to approve"]
    urgent = [f for f in result.flags if f.severity == "urgent"]
    if urgent:
        bits.append(f"{len(urgent)} urgent")
    if result.unapplied:
        bits.append(f"{len(result.unapplied)} possible payments")
    past_due = result.snapshot.past_due_ar if result.snapshot else ZERO
    return (
        f"{settings.digest_subject_prefix} {result.as_of:%a %b %-d} — "
        f"{', '.join(bits)} · {_money(past_due)} past due"
    )


def _wow_line(snapshot: Snapshot | None, prior: Snapshot | None) -> str:
    if snapshot is None:
        return "No snapshot taken."
    if prior is None:
        return "Week-over-week: no snapshot from a week ago yet."
    delta = snapshot.past_due_ar - prior.past_due_ar
    direction = "up" if delta > 0 else ("down" if delta < 0 else "flat")
    return (
        f"Week-over-week past due: {direction} {_money(abs(delta))} "
        f"(from {_money(prior.past_due_ar)} on {prior.as_of:%b %-d})"
    )


# --- plain text -------------------------------------------------------------


def _text_reminders(reminders: list[ReminderCandidate], limit: int) -> list[str]:
    lines = ["1. REMINDERS QUEUED FOR APPROVAL", ""]
    if not reminders:
        lines += ["   Nothing queued. No invoice reached a reminder stage cleanly.", ""]
        return lines
    for r in reminders[:limit]:
        lines.append(
            f"   {r.ref}  {r.invoice.customer_name} · {r.invoice.label} · "
            f"{_money(r.invoice.balance)} · {r.days_past_due} days overdue"
        )
        lines.append(f"       {r.stage_label} → {r.to}" + (f" (cc {', '.join(r.cc)})" if r.cc else ""))
    if len(reminders) > limit:
        lines.append(f"   ... and {len(reminders) - limit} more, not shown.")
    lines += ["", "   Reply: APPROVE R1 R2   |   APPROVE ALL   |   HOLD R3", ""]
    return lines


def _text_flags(flags: list[Flag], limit: int) -> list[str]:
    lines = ["2. FLAGGED — needs you, not the client", ""]
    if not flags:
        lines += ["   Nothing unusual.", ""]
        return lines
    for f in flags[:limit]:
        mark = "!!" if f.severity == "urgent" else "  "
        lines.append(f"   {mark} {f.ref}  {f.customer_name}" + (f" · {f.invoice_label}" if f.invoice_label else ""))
        lines.append(f"       {f.reason}")
        lines.append(f"       → {f.recommended_action}")
    if len(flags) > limit:
        lines.append(f"   ... and {len(flags) - limit} more, not shown.")
    lines.append("")
    return lines


def _text_unapplied(matches: list[UnappliedCandidate], limit: int) -> list[str]:
    lines = ["3. POSSIBLE UNAPPLIED PAYMENTS — verify before anything sends", ""]
    if not matches:
        lines += ["   None found.", ""]
        return lines
    for m in matches[:limit]:
        lines.append(
            f"   {m.ref}  {m.invoice.customer_name} · {m.invoice.label} · "
            f"balance {_money(m.invoice.balance)}"
        )
        lines.append(f"       {m.note}")
        lines.append(
            f"       {m.source} {m.source_id} · {m.source_date} · {_money(m.amount)} · "
            f"{m.account} · {m.confidence} match"
        )
    if len(matches) > limit:
        lines.append(f"   ... and {len(matches) - limit} more, not shown.")
    lines += ["", "   Nothing is applied automatically. Apply it in QBO by hand if it matches.", ""]
    return lines


def _text_stats(result: RunResult) -> list[str]:
    s = result.snapshot
    lines = ["4. WHERE AR STANDS", ""]
    if s:
        lines += [
            f"   Total AR          {_money(s.total_ar)}  ({s.open_invoice_count} open invoices)",
            f"   Past due          {_money(s.past_due_ar)}  ({s.past_due_count} invoices)",
            f"   {_wow_line(s, result.prior_snapshot)}",
        ]
    if result.suppressed_count:
        lines.append(f"   Suppressed        {result.suppressed_count} invoices under a client-level hold")
    if result.errors:
        lines.append("")
        lines.append("   Ran into trouble:")
        for error in result.errors:
            lines.append(f"     - {error}")
    lines.append("")
    return lines


def render_text(result: RunResult, settings: Settings) -> str:
    limit = settings.max_rows_per_section
    link = approval_link(result.digest_id)
    header = [
        f"AR digest — {result.as_of:%A, %B %-d, %Y}",
        f"digest {result.digest_id}",
        "",
        "Nothing below has been sent and nothing has been changed in QuickBooks.",
    ]
    if link:
        header.append(f"Checklist: {link}")
    header.append("")
    body = (
        header
        + _text_reminders(result.reminders, limit)
        + _text_flags(result.flags, limit)
        + _text_unapplied(result.unapplied, limit)
        + _text_stats(result)
        + [
            "—",
            "AR follow-up agent. Read-only against QuickBooks. Sends nothing without "
            "your approval.",
        ]
    )
    return "\n".join(body)


# --- html -------------------------------------------------------------------

_CSS_CARD = (
    "border:1px solid #e4e4e7;border-radius:10px;padding:12px 14px;margin:0 0 10px"
)
_CSS_URGENT = "border-left:4px solid #c2410c"
_CSS_MUTED = "color:#6b7280;font-size:13px;margin:4px 0 0"


def _h(text: str) -> str:
    return html.escape(str(text))


def _html_section(title: str, inner: str) -> str:
    return (
        f'<h2 style="font-size:15px;text-transform:uppercase;letter-spacing:.04em;'
        f'color:#374151;margin:26px 0 10px">{_h(title)}</h2>{inner}'
    )


def render_html(result: RunResult, settings: Settings) -> str:
    limit = settings.max_rows_per_section
    parts: list[str] = [
        '<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
        'font-size:15px;line-height:1.5;color:#111827;max-width:620px;margin:0 auto">',
        f'<h1 style="font-size:19px;margin:0 0 2px">AR digest — {_h(f"{result.as_of:%A, %B %-d}")}</h1>',
        f'<p style="{_CSS_MUTED}">digest {_h(result.digest_id)} · nothing sent, nothing written to QuickBooks</p>',
    ]

    link = approval_link(result.digest_id)
    if link:
        parts.append(
            f'<p style="margin:14px 0"><a href="{_h(link)}" style="background:#111827;color:#fff;'
            'padding:10px 16px;border-radius:8px;text-decoration:none;display:inline-block">'
            "Open approval checklist</a></p>"
        )

    # 1 — reminders
    if result.reminders:
        rows = []
        for r in result.reminders[:limit]:
            cc = f' · cc {_h(", ".join(r.cc))}' if r.cc else ""
            rows.append(
                f'<div style="{_CSS_CARD}">'
                f'<div><strong>{_h(r.ref)}</strong> · {_h(r.invoice.customer_name)} · '
                f'{_h(r.invoice.label)}</div>'
                f'<div style="font-size:17px;margin:4px 0"><strong>{_h(_money(r.invoice.balance))}</strong>'
                f' · {r.days_past_due} days overdue</div>'
                f'<p style="{_CSS_MUTED}">{_h(r.stage_label)} → {_h(r.to)}{cc}</p>'
                "</div>"
            )
        if len(result.reminders) > limit:
            rows.append(f'<p style="{_CSS_MUTED}">… and {len(result.reminders) - limit} more, not shown.</p>')
        rows.append(
            '<p style="background:#f3f4f6;border-radius:8px;padding:10px 12px;font-size:14px">'
            "Reply <strong>APPROVE R1 R2</strong>, <strong>APPROVE ALL</strong>, or "
            "<strong>HOLD R3</strong>. Anything you do not name is not sent.</p>"
        )
        parts.append(_html_section("1 · Reminders queued for approval", "".join(rows)))
    else:
        parts.append(
            _html_section(
                "1 · Reminders queued for approval",
                f'<p style="{_CSS_MUTED}">Nothing queued. No invoice reached a reminder stage cleanly.</p>',
            )
        )

    # 2 — flags
    if result.flags:
        rows = []
        for f in result.flags[:limit]:
            style = _CSS_CARD + (";" + _CSS_URGENT if f.severity == "urgent" else "")
            label = f' · {_h(f.invoice_label)}' if f.invoice_label else ""
            amount = f' · {_h(_money(f.amount))}' if f.amount is not None else ""
            rows.append(
                f'<div style="{style}">'
                f'<div><strong>{_h(f.ref)}</strong> · {_h(f.customer_name)}{label}{amount}</div>'
                f'<div style="margin:4px 0">{_h(f.reason)}</div>'
                f'<p style="{_CSS_MUTED}">→ {_h(f.recommended_action)}</p>'
                "</div>"
            )
        if len(result.flags) > limit:
            rows.append(f'<p style="{_CSS_MUTED}">… and {len(result.flags) - limit} more, not shown.</p>')
        parts.append(_html_section("2 · Flagged — needs you, not the client", "".join(rows)))

    # 3 — unapplied
    if result.unapplied:
        rows = []
        for m in result.unapplied[:limit]:
            rows.append(
                f'<div style="{_CSS_CARD}">'
                f'<div><strong>{_h(m.ref)}</strong> · {_h(m.invoice.customer_name)} · '
                f'{_h(m.invoice.label)} · balance {_h(_money(m.invoice.balance))}</div>'
                f'<div style="margin:4px 0">{_h(m.note)}</div>'
                f'<p style="{_CSS_MUTED}">{_h(m.source)} {_h(m.source_id)} · {_h(m.source_date)} · '
                f'{_h(_money(m.amount))} · {_h(m.account)} · {_h(m.confidence)} match</p>'
                "</div>"
            )
        rows.append(
            f'<p style="{_CSS_MUTED}">Nothing is applied automatically. Apply it in QBO by hand if it matches.</p>'
        )
        parts.append(_html_section("3 · Possible unapplied payments", "".join(rows)))

    # 4 — stats
    s = result.snapshot
    stat_rows = ""
    if s:
        stat_rows = (
            f'<div style="{_CSS_CARD}">'
            f'<div style="font-size:13px;color:#6b7280">Total AR</div>'
            f'<div style="font-size:20px"><strong>{_h(_money(s.total_ar))}</strong> '
            f'<span style="font-size:13px;color:#6b7280">· {s.open_invoice_count} open</span></div>'
            f'<div style="font-size:13px;color:#6b7280;margin-top:10px">Past due</div>'
            f'<div style="font-size:20px"><strong>{_h(_money(s.past_due_ar))}</strong> '
            f'<span style="font-size:13px;color:#6b7280">· {s.past_due_count} invoices</span></div>'
            f'<p style="{_CSS_MUTED}">{_h(_wow_line(s, result.prior_snapshot))}</p>'
            "</div>"
        )
    if result.suppressed_count:
        stat_rows += (
            f'<p style="{_CSS_MUTED}">{result.suppressed_count} invoices are under a client-level hold '
            "(dispute, credit balance, or stacked AR).</p>"
        )
    if result.errors:
        errors = "".join(f"<li>{_h(e)}</li>" for e in result.errors)
        stat_rows += f'<p style="{_CSS_MUTED}">Ran into trouble:</p><ul style="{_CSS_MUTED}">{errors}</ul>'
    parts.append(_html_section("4 · Where AR stands", stat_rows))

    parts.append(
        '<hr style="border:none;border-top:1px solid #e5e7eb;margin:26px 0 12px">'
        f'<p style="{_CSS_MUTED}">AR follow-up agent · read-only against QuickBooks · '
        "sends nothing without your approval</p></div>"
    )
    return "".join(parts)
