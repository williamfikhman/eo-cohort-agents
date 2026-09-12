# AR follow-up agent

Every morning: read every open invoice out of QuickBooks Online, work out which
ones are genuinely unpaid, propose the reminders that are due, and route
everything that looks wrong to William instead of to the client.

It sends one email on its own authority — the digest, to William. Client
reminders go out only after he replies with an approval, and only after the
invoice is re-verified against live QuickBooks data a second time.

## The two standing rules

| Rule | How it is enforced |
|---|---|
| No writes to QuickBooks | `QboClient` only implements GET. `post`, `put`, `delete`, `create`, `update` and `send` raise `QboWriteAttempted`. Every QBO write is a POST, so there is no code path to one. |
| No client email without approval | `scan` never emails a client. `send-approved` sends only refs a human named, read from William's reply or passed on the command line, and re-verified first. |

Both are also asserted in `config/settings.yaml` under `policy:`. Flipping either
one to the unsafe value makes the config fail to load — the run stops before the
first API call rather than sending on a changed policy.

Payments are never applied automatically either. Deposit-to-invoice matching has
real double-payment risk, so proposed matches go in the digest and a person
applies them in QBO.

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
 ├─ query payments + Chase / QuickBooks Payments deposits, last 21 days
 ├─ query open credit memos
 ├─ per client: validate each invoice against the contract in clients.yaml
 ├─ per invoice: pick the cadence stage
 │    └─ before proposing: re-read the invoice live, then look for money
 │       that already arrived. Any candidate → review queue, never a send.
 └─ email the digest to William. Nothing else leaves the building.

send-approved
 ├─ read William's reply in the digest thread (or --approve on the CLI)
 ├─ re-read each approved invoice live, again
 ├─ hold if it is paid, if the balance moved at all, or if a payment turned up
 └─ send through Gmail from the billing mailbox, and log it
```

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
python -m ar_followup send-approved --dry-run   # verify approvals, send nothing
python -m ar_followup send-approved             # send what William approved
python -m ar_followup status                    # what the ledger knows
```

`--date YYYY-MM-DD` on either command runs the day as if it were that date,
which is how the cadence gets rehearsed before it goes live.

## Approving

The digest numbers every proposal `R1`, `R2`, and so on. William replies:

```
APPROVE R1 R3
HOLD R2
```

or `APPROVE ALL`. Anything not named is not sent. A hold always beats an approve.
A qualified approve-all (`approve all except R3`) is refused rather than guessed
at, and reported back in the next `send-approved` run. Only replies from the
digest recipient count.

Set `AR_APPROVAL_LINK_BASE` if you want the digest to carry a checklist link
alongside the reply instructions.

## Config Angie maintains

| File | What |
|---|---|
| `config/clients.yaml` | Per-client contract terms with effective dates, aliases, billing contacts, credit balances, suppression status and carry-forward flags |
| `config/settings.yaml` | Cadence, tolerances, bank accounts swept, identities, digest options |

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
already paid.
