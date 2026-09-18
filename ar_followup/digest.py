"""The daily digest: what reconciled, what is genuinely owed, what looks wrong.

Written to be read on a phone, standing up, before coffee. Section order is
fixed, and it starts where the money is:

  1. matches to confirm       money found, waiting to be applied in QBO
  2. reminders to approve     invoices still owed after that money is accounted for
  3. flagged                  anything that needs a person, not a client email
  4. money with no home       cash that matched nothing
  5. where AR stands          gross, pending application, and what is really owed

Sections 1 and 2 are in that order on purpose. Every reminder in section 2 has
already survived section 1, so approving one cannot dun a client who paid.

Replying:
  CONFIRM M1 M2   these matches are right, they will be applied in QBO
  APPROVE R1 R3   send these reminders
  APPROVE ALL     send every reminder in section 2
  HOLD R2         do not send this one
Anything not named is not acted on. Silence sends nothing.
"""

from __future__ import annotations

import html
import os
from decimal import Decimal

from .config import Settings
from .models import Flag, ProposedMatch, ReminderCandidate, RunResult, Snapshot, UnexplainedMoney

ZERO = Decimal("0.00")


def _money(value: Decimal | None) -> str:
    return f"${value:,.2f}" if value is not None else "—"


def approval_link(digest_id: str) -> str | None:
    base = os.environ.get("AR_APPROVAL_LINK_BASE")
    return f"{base.rstrip('/')}?digest={digest_id}" if base else None


def subject(result: RunResult, settings: Settings) -> str:
    bits = []
    if result.matches:
        bits.append(f"{len(result.matches)} to match")
    bits.append(f"{len(result.reminders)} to approve")
    urgent = [f for f in result.flags if f.severity == "urgent"]
    if urgent:
        bits.append(f"{len(urgent)} urgent")
    owed = result.snapshot.past_due_ar if result.snapshot else ZERO
    return (
        f"{settings.digest_subject_prefix} {result.as_of:%a %b %-d} — "
        f"{', '.join(bits)} · {_money(owed)} really past due"
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


def _text_matches(matches: list[ProposedMatch], limit: int) -> list[str]:
    lines = ["1. MATCH THESE FIRST — money received, not yet applied in QBO", ""]
    if not matches:
        lines += ["   Nothing loose. Every payment found is already on an invoice.", ""]
        return lines
    total = sum((m.amount for m in matches), ZERO)
    for m in matches[:limit]:
        lines.append(
            f"   {m.ref}  {m.customer_name} · {m.invoice_labels} · {_money(m.amount)} · "
            f"{m.confidence} confidence ({m.strategy})"
        )
        lines.append(f"       {m.rationale}")
        if m.double_counted > 0:
            lines.append(
                f"       !! {_money(m.double_counted)} of this was booked to income, "
                "so revenue and AR are both overstated."
            )
    if len(matches) > limit:
        lines.append(f"   ... and {len(matches) - limit} more, not shown.")
    lines += [
        "",
        f"   {_money(total)} found in total. Reply CONFIRM M1 M2 for the ones that are right.",
        "   The agent never applies these itself. Confirming means you or Angie will.",
        "",
    ]
    return lines


def _text_reminders(reminders: list[ReminderCandidate], limit: int) -> list[str]:
    lines = ["2. REMINDERS QUEUED FOR APPROVAL — owed after the matches above", ""]
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
    lines = ["3. FLAGGED — needs you, not the client", ""]
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


def _text_unexplained(items: list[UnexplainedMoney], limit: int) -> list[str]:
    lines = ["4. MONEY WITH NO HOME — arrived, matched no open invoice", ""]
    if not items:
        lines += ["   None.", ""]
        return lines
    for u in items[:limit]:
        who = u.source.customer_name or "unattributed"
        lines.append(f"   {u.ref}  {who} · {_money(u.source.amount)} · {u.source.txn_date}")
        lines.append(f"       {u.reason}")
        lines.append(f"       {u.source.label} · {u.source.account or 'unknown account'}")
    if len(items) > limit:
        lines.append(f"   ... and {len(items) - limit} more, not shown.")
    lines += ["", "   Usually a prepayment, a refund, or money posted to the wrong place.", ""]
    return lines


def _text_stats(result: RunResult) -> list[str]:
    s = result.snapshot
    lines = ["5. WHERE AR STANDS", ""]
    if s:
        lines += [
            f"   Gross AR              {_money(s.total_ar)}  ({s.open_invoice_count} open invoices)",
            f"   Less money in hand   -{_money(s.pending_application)}  "
            f"({s.pending_invoice_count} invoices waiting on bookkeeping)",
            f"   Really owed           {_money(s.net_ar)}",
            f"   Of that, past due     {_money(s.past_due_ar)}  ({s.past_due_count} invoices)",
            f"   {_wow_line(s, result.prior_snapshot)}",
        ]
    if result.suppressed_count:
        lines.append(f"   Suppressed            {result.suppressed_count} invoices under a client-level hold")
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
        "Reconciled first, then reported. Nothing below has been sent and nothing "
        "has been changed in QuickBooks.",
    ]
    if link:
        header.append(f"Checklist: {link}")
    header.append("")
    body = (
        header
        + _text_matches(result.matches, limit)
        + _text_reminders(result.reminders, limit)
        + _text_flags(result.flags, limit)
        + _text_unexplained(result.unexplained, limit)
        + _text_stats(result)
        + [
            "—",
            "AR follow-up agent. Read-only against QuickBooks. Applies nothing, "
            "sends nothing, without you.",
        ]
    )
    return "\n".join(body)


# --- html -------------------------------------------------------------------

_CSS_CARD = "border:1px solid #e4e4e7;border-radius:10px;padding:12px 14px;margin:0 0 10px"
_CSS_URGENT = "border-left:4px solid #c2410c"
_CSS_MATCH = "border-left:4px solid #047857"
_CSS_MUTED = "color:#6b7280;font-size:13px;margin:4px 0 0"
_CSS_NOTE = "background:#f3f4f6;border-radius:8px;padding:10px 12px;font-size:14px"


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
        f'<p style="{_CSS_MUTED}">digest {_h(result.digest_id)} · reconciled first · '
        "nothing sent, nothing written to QuickBooks</p>",
    ]

    link = approval_link(result.digest_id)
    if link:
        parts.append(
            f'<p style="margin:14px 0"><a href="{_h(link)}" style="background:#111827;color:#fff;'
            'padding:10px 16px;border-radius:8px;text-decoration:none;display:inline-block">'
            "Open approval checklist</a></p>"
        )

    # 1 — matches
    if result.matches:
        rows = []
        for m in result.matches[:limit]:
            warn = ""
            if m.double_counted > 0:
                warn = (
                    f'<p style="{_CSS_MUTED};color:#c2410c">{_h(_money(m.double_counted))} of this '
                    "was booked to income. Revenue and AR are both overstated by it.</p>"
                )
            rows.append(
                f'<div style="{_CSS_CARD};{_CSS_MATCH}">'
                f'<div><strong>{_h(m.ref)}</strong> · {_h(m.customer_name)} · {_h(m.invoice_labels)}</div>'
                f'<div style="font-size:17px;margin:4px 0"><strong>{_h(_money(m.amount))}</strong>'
                f' <span style="font-size:13px;color:#6b7280">· {_h(m.confidence)} confidence'
                f' ({_h(m.strategy)})</span></div>'
                f'<p style="{_CSS_MUTED}">{_h(m.rationale)}</p>{warn}'
                "</div>"
            )
        if len(result.matches) > limit:
            rows.append(f'<p style="{_CSS_MUTED}">… and {len(result.matches) - limit} more, not shown.</p>')
        found = sum((m.amount for m in result.matches), ZERO)
        rows.append(
            f'<p style="{_CSS_NOTE}">{_h(_money(found))} found in total. Reply '
            "<strong>CONFIRM M1 M2</strong> for the ones that are right. The agent never "
            "applies these itself, so confirming means you or Angie will.</p>"
        )
        parts.append(_html_section("1 · Match these first", "".join(rows)))
    else:
        parts.append(
            _html_section(
                "1 · Match these first",
                f'<p style="{_CSS_MUTED}">Nothing loose. Every payment found is already on an invoice.</p>',
            )
        )

    # 2 — reminders
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
            f'<p style="{_CSS_NOTE}">Reply <strong>APPROVE R1 R2</strong>, '
            "<strong>APPROVE ALL</strong>, or <strong>HOLD R3</strong>. Anything you do not "
            "name is not sent.</p>"
        )
        parts.append(_html_section("2 · Reminders queued for approval", "".join(rows)))
    else:
        parts.append(
            _html_section(
                "2 · Reminders queued for approval",
                f'<p style="{_CSS_MUTED}">Nothing queued. No invoice reached a reminder stage cleanly.</p>',
            )
        )

    # 3 — flags
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
        parts.append(_html_section("3 · Flagged — needs you, not the client", "".join(rows)))

    # 4 — unexplained
    if result.unexplained:
        rows = []
        for u in result.unexplained[:limit]:
            who = u.source.customer_name or "unattributed"
            rows.append(
                f'<div style="{_CSS_CARD}">'
                f'<div><strong>{_h(u.ref)}</strong> · {_h(who)} · '
                f'{_h(_money(u.source.amount))} · {_h(u.source.txn_date)}</div>'
                f'<div style="margin:4px 0">{_h(u.reason)}</div>'
                f'<p style="{_CSS_MUTED}">{_h(u.source.label)} · '
                f'{_h(u.source.account or "unknown account")}</p>'
                "</div>"
            )
        if len(result.unexplained) > limit:
            rows.append(f'<p style="{_CSS_MUTED}">… and {len(result.unexplained) - limit} more, not shown.</p>')
        parts.append(_html_section("4 · Money with no home", "".join(rows)))

    # 5 — stats
    s = result.snapshot
    stat_rows = ""
    if s:
        stat_rows = (
            f'<div style="{_CSS_CARD}">'
            f'<div style="font-size:13px;color:#6b7280">Really owed, after matching</div>'
            f'<div style="font-size:22px"><strong>{_h(_money(s.net_ar))}</strong></div>'
            f'<p style="{_CSS_MUTED}">Gross AR {_h(_money(s.total_ar))} across {s.open_invoice_count} '
            f'invoices, less {_h(_money(s.pending_application))} already received and waiting on '
            f'bookkeeping ({s.pending_invoice_count} invoices).</p>'
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
    parts.append(_html_section("5 · Where AR stands", stat_rows))

    parts.append(
        '<hr style="border:none;border-top:1px solid #e5e7eb;margin:26px 0 12px">'
        f'<p style="{_CSS_MUTED}">AR follow-up agent · read-only against QuickBooks · '
        "applies nothing and sends nothing without you</p></div>"
    )
    return "".join(parts)
