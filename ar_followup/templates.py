"""Client-facing reminder copy.

These are the only words that ever reach a client, and none of them go out
without William approving the specific invoice they are attached to. Tone
matters: at +3 the most likely explanation is that the invoice is sitting in
someone's approval queue, so the +3 note assumes good faith. Nothing here
mentions late fees, collections, or service interruption — that is a decision a
person makes on a call, not a template.
"""

from __future__ import annotations

from datetime import date

from .config import Settings
from .models import Invoice

SIGNOFF = "Chief Marketplace Officer, Inc."


def _link_line(invoice: Invoice) -> str:
    if invoice.share_link:
        return f"You can view and pay it here: {invoice.share_link}"
    return "Happy to resend the invoice or a payment link if that is easier."


def _header(invoice: Invoice, today: date) -> str:
    days = (today - invoice.due_date).days
    return (
        f"Invoice {invoice.label}\n"
        f"Amount outstanding: ${invoice.balance:,.2f}\n"
        f"Invoice date: {invoice.txn_date:%B %-d, %Y}\n"
        f"Due date: {invoice.due_date:%B %-d, %Y} ({days} days ago)"
    )


def friendly(invoice: Invoice, today: date, settings: Settings) -> tuple[str, str]:
    subject = f"Invoice {invoice.label} from Chief Marketplace Officer"
    body = f"""Hi,

Quick note that invoice {invoice.label} for ${invoice.balance:,.2f} came due on \
{invoice.due_date:%B %-d}. If it is already working its way through your approvals, \
no action needed and thank you.

{_header(invoice, today)}

{_link_line(invoice)}

If anything on the invoice does not look right, reply here and we will sort it out.

Thanks,
{SIGNOFF}
{settings.billing_reply_to}
"""
    return subject, body


def firm(invoice: Invoice, today: date, settings: Settings) -> tuple[str, str]:
    days = (today - invoice.due_date).days
    subject = f"Following up: invoice {invoice.label} ({days} days past due)"
    body = f"""Hi,

Following up on invoice {invoice.label}, now {days} days past due. We have not seen \
payment or a note about a delay, so I want to make sure it did not get stuck somewhere.

{_header(invoice, today)}

{_link_line(invoice)}

Could you confirm the payment date, or tell me who to talk to in AP? Angie is copied \
here so either of us can help.

If the invoice is disputed or you are expecting a credit, say so and we will hold \
everything until it is resolved.

Thanks,
{SIGNOFF}
{settings.billing_reply_to}
"""
    return subject, body


def escalation(invoice: Invoice, today: date, settings: Settings) -> tuple[str, str]:
    """Never sent by the agent. Rendered so William has the context on the call."""
    days = (today - invoice.due_date).days
    subject = f"[CALL — not sent] Invoice {invoice.label}, {days} days past due"
    body = f"""Automated email has stopped for this invoice. It is {days} days past due, \
past the escalation line.

{_header(invoice, today)}

Reminders already sent: friendly (+3) and firm (+10), with no payment and no reply \
that resolved it.

Recommended: a personal call from Angie or William. Ask three things — is the invoice \
disputed, is there a payment date, and is the relationship still active. Stacked AR at \
this age is usually a churn signal, not a paperwork problem.

{SIGNOFF}
{settings.billing_reply_to}
"""
    return subject, body


TEMPLATES = {
    "friendly": friendly,
    "firm": firm,
    "escalation": escalation,
}


def render(template: str, invoice: Invoice, today: date, settings: Settings) -> tuple[str, str]:
    try:
        return TEMPLATES[template](invoice, today, settings)
    except KeyError:
        raise KeyError(
            f"no reminder template named '{template}'. Templates available: "
            f"{', '.join(sorted(TEMPLATES))}"
        ) from None
