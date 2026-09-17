# AR follow-up agent

Every morning: reconcile the money against the invoices first, then report AR on
whatever that leaves, propose the reminders that are genuinely due, and route
everything that looks wrong to William instead of to the client.

The reconciliation step comes first for a reason. An invoice with a balance in
QuickBooks is not evidence that a client owes money. It is evidence that no
payment has been applied to it. Those are different facts, and treating the first
as the second is how a client who paid gets a dunning email.

It sends one email on its own authority — the digest, to William. Client
reminders go out only after he replies with an approval, and only after the
invoice is re-verified against live QuickBooks data a second time.

## The two standing rules

| Rule | How it is enforced |
|---|---|
| Nothing reaches a client without approval | `scan` never emails a client. `send-approved` sends only refs a human named, read from William's reply or passed on the command line, and re-verified first. |
| Nothing is posted to QuickBooks without confirmation | The reading client (`QboClient`) implements GET and nothing else; `post`, `put`, `delete` and friends raise `QboWriteAttempted`. Posting lives in one separate place, `apply-confirmed`, and requires a CONFIRM on a specific match. |

Both are asserted in `config/settings.yaml`. Turning off the approval rule, or
`payment_application.require_confirmed_match`, makes the config fail to load —
the run stops before the first API call rather than proceeding on a changed
policy.

### What posting will and will not do

`apply-confirmed` allocates a Payment that already exists in QuickBooks to the
invoice it belongs to. No money is created, nothing is deposited, the cash total
does not move — only the allocation changes.

It will not post a match sourced from a bank deposit, and
`payment_application.allow_deposit_sources: true` is refused by the config
loader. A deposit is cash already in the register, so creating a Payment for it
books the same money twice. Those go to a human with the fix: undo the
categorization in the bank feed, then use Find match.

Four things stop a double application: the confirmation requirement, the ledger's
record of every payment-invoice pair posted, an audit stamp written into the
Payment's private note in QuickBooks, and a live re-read of both the invoice and
the payment immediately before the write. A failed write is never retried.

## Why the direct Intuit API and not the MCP connector

Three connector limitations are load-bearing here:

- **aging reports serve cached data** — it reported invoices as open that had
  already been paid. This build never calls a report endpoint at all; the client
  refuses paths containing `/reports/`. Live invoice balances are the only
  source of truth.
- **the customer filter on invoice queries does not work** — so invoices are
  pulled in full and filtered in Python, where the filter is testable.
- **there is no credit memo endpoint** — and credit balances decide whether a
  client gets reminded at all.

## What happens on a run

```
scan
 ├─ query live open invoices          (Balance > 0, paged, re-checked in Python)
 ├─ query payments, Chase / QuickBooks Payments deposit lines, credit memos
 │
 ├─ RECONCILE ─ put that money next to those invoices, best evidence first:
 │    1. referenced  a payment citing the invoice number
 │    2. exact       one source equal to one invoice balance
 │    3. split       several sources adding up to one invoice
 │    4. lump        one source covering several invoices
 │    5. near        the same shapes, within tolerance
 │    No dollar is spent twice. Nothing is applied in QuickBooks.
 │
 ├─ AR is then measured on what reconciliation left behind, not on raw balances
 ├─ per client: validate the remaining invoices against the contract in clients.yaml
 ├─ per invoice: pick the cadence stage
 │    └─ before proposing: re-read the invoice live, and run the matcher once
 │       more against the fresh balance. Any hit → no reminder.
 └─ email the digest to William. Nothing else leaves the building.

send-approved
 ├─ read William's reply in the digest thread (or --approve / --confirm on the CLI)
 ├─ record every match decision in the ledger
 ├─ re-read each approved invoice live, again
 ├─ hold if it is paid, if the balance moved at all, if money turned up, or if a
 │  match on the same invoice was confirmed in the same reply
 └─ send through Gmail from the billing mailbox, and log it
```

### Why matching first is the whole point

A client who pays a $9,765.14 invoice in two transfers matches nothing on
amount, because neither transfer equals the invoice. QuickBooks leaves both
deposits in the bank feed for a human, and the invoice stays open at its full
balance. If that deposit was then cleared with **Add** rather than **Find
match**, the cash posted to an income account and the invoice will never close
on its own — and the same dollars are now counted twice, once as revenue and
once as receivable. The agent proposes the split match, flags the double count,
and keeps the invoice out of the cadence until it clears.

## Cadence

Counted from the due date. Net 15 unless a contract in `clients.yaml` says
otherwise.

| Stage | When | What |
|---|---|---|
| friendly | due + 3 | Reminder with a link to the invoice |
| firm | due + 10 | Firmer follow-up, cc Angie |
| escalation | due + 21 | **No email.** Raises a personal call for Angie or William, and automated email stops for that invoice for good |

A stage is sent at most once per invoice, ever. The ledger enforces that even if
the run fires twice in a morning.

## What takes a client out of automation entirely

- **In dispute or renegotiation** — today: Kaaral (revised 8/1 to 50% commission,
  no flat fee, Aug and Sep reissued) and Rust Check (reconciliation in progress).
- **Carrying a credit balance** — today: Rust Check Corp, $6,026.67 from Sept
  2026. The agent also checks the credit is actually netting against new
  invoices and flags it when it is not.
- **Stacked AR** — two or more open invoices across different months. That is
  what silent churn looks like from the inside: Labeldaddy, Silko/CocoChoco and
  BeLAGU all looked like slow payers first. A dunning email makes it worse, so a
  human calls instead.
- **An invoice that disagrees with its contract** — wrong commission rate, a flat
  fee billed after a step-down, wrong currency or FX rate. Held and flagged.
- **A short pay, a partial payment, or a payment referencing another invoice.**

Suppressed never means invisible. Every one of these still appears in the digest.

## Setup

### 1. Intuit app

Create an app at developer.intuit.com, add the `com.intuit.quickbooks.accounting`
scope, and get a refresh token plus the realm (company) id from the OAuth
playground.

```bash
export QBO_CLIENT_ID=...
export QBO_CLIENT_SECRET=...
python -m ar_followup auth --refresh-token <token> --realm-id <realm>
```

That writes `state/qbo_token.json` (mode 0600). **Back it up.** Intuit rotates
the refresh token roughly daily and the old one stops working, so this file is
the one piece of state that cannot be rebuilt from QuickBooks.

### 2. Gmail

A Google Cloud OAuth client for the billing mailbox, with
`gmail.send` and `gmail.readonly`. Then:

```bash
export GMAIL_CLIENT_ID=...
export GMAIL_CLIENT_SECRET=...
export GMAIL_REFRESH_TOKEN=...
```

Sending through Gmail rather than QBO's native reminder is deliberate: the send
log and the thread history end up in the mailbox Angie and William already work
in, and a client's reply lands under the reminder that prompted it.

### 3. Check the config

```bash
python -m ar_followup validate-config
```

## Running it

```bash
python -m ar_followup scan --no-email --print   # build the digest, show it, send nothing
python -m ar_followup scan                      # the morning run
python -m ar_followup apply-confirmed           # dry run: what it would post to QBO
python -m ar_followup apply-confirmed --live    # post confirmed matches
python -m ar_followup send-approved --dry-run   # verify approvals, send nothing
python -m ar_followup send-approved             # send what William approved
python -m ar_followup status                    # what the ledger knows
```

Posting runs before sending, so a reminder never goes out against an invoice
whose payment just landed.

**Going live for the first time: see [`../docs/GO-LIVE.md`](../docs/GO-LIVE.md).**

`--date YYYY-MM-DD` on either command runs the day as if it were that date,
which is how the cadence gets rehearsed before it goes live.

## Approving

The digest numbers matches `M1`, `M2` and reminders `R1`, `R2`. William replies:

```
CONFIRM M1 M2
APPROVE R1 R3
HOLD R2
```

or `APPROVE ALL` / `CONFIRM ALL`. The prefix decides which list a ref belongs to,
so the verb barely matters. Anything not named is not acted on, and a negative
always beats a positive for the same ref.

**Confirming a match applies nothing in QuickBooks.** The agent cannot write
there. A confirmation records that a person read the proposal and agreed, which
takes the invoice out of the cadence and starts a clock: if the invoice is still
open three days later, the digest chases it as a confirmed match nobody applied.
A qualified approve-all (`approve all except R3`) is refused rather than guessed
at, and reported back in the next `send-approved` run. Only replies from the
digest recipient count.

Set `AR_APPROVAL_LINK_BASE` if you want the digest to carry a checklist link
alongside the reply instructions.

## Config Angie maintains

| File | What |
|---|---|
| `config/clients.yaml` | Per-client contract terms with effective dates, aliases, billing contacts, credit balances, suppression status and carry-forward flags |
| `config/settings.yaml` | Cadence, matching rules and tolerances, bank accounts swept, identities, digest options |

The `matching:` block is where reconciliation is tuned: how far back to sweep,
how many pieces a split may have, how close a near miss may be, and which
account names count as income for the double-count check. A split in more pieces
than `max_sources_per_match` is deliberately left for a person rather than
guessed at.

Contract terms are a **list of periods**, oldest first. A step-down is a new
period, not an edit to the old one — keeping the history is what lets the agent
notice an invoice billed on terms that had already expired. That is the check
that would have caught four months of $3,000/mo + 8% under a contract that had
stepped down to 10%-commission-only, which came to $7,328 in credits.

Run `validate-config` after every edit. Overlapping periods, unclosed periods,
duplicate aliases and bad dates all fail loudly.

## State and logs

Everything lives under `state/` (override with `AR_STATE_DIR`):

| File | What |
|---|---|
| `qbo_token.json` | The OAuth token triple. Secret. Never commit. |
| `ledger.jsonl` | Every digest, approval decision and sent reminder |
| `snapshots.jsonl` | Daily AR totals, which is where week-over-week comes from |
| `digests/<id>.json` | The full proposal set, so a later send can be checked against what was approved |

## Tests

```bash
python -m pytest tests/ -q
```

The fake QuickBooks in `tests/conftest.py` can serve one set of rows from a list
query and a different set from a single-invoice read, which reproduces the exact
failure this build exists to prevent: a list that says open, an invoice that is
already paid. `tests/test_matching.py` covers the reconciliation engine, including
the two-transfer split that started it.
