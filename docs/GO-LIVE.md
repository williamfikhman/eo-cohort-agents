# Going live

Everything the AR agent needs before it runs on its own, in the order it has to
happen. Steps 1 to 4 are credentials and only William can do them. Step 5 is a
week of watching. Step 6 turns on posting to QuickBooks.

Nothing in steps 1 to 5 can send a client an email or change anything in
QuickBooks. The first two weeks are deliberately read-only.

---

## 1. The Intuit app  (30 minutes, blocking)

The agent talks to the QuickBooks API directly, as its own OAuth app. This is
not the QuickBooks connector, which serves cached aging data and has no endpoint
for payments or deposits.

1. Sign in at developer.intuit.com and create an app.
2. Add the `com.intuit.quickbooks.accounting` scope. This covers both reading and
   posting; the agent's own config decides which it actually does.
3. From the keys tab, copy the **production** client id and client secret.
4. In the OAuth playground, authorize the app against Chief Marketplace Officer
   and copy the **refresh token** and the **realm id** (the company id).

Then, once, on whatever machine will run it:

```bash
export QBO_CLIENT_ID=...
export QBO_CLIENT_SECRET=...
python -m ar_followup auth --refresh-token <token> --realm-id <realm>
```

That writes `state/qbo_token.json`. **Back this file up.** Intuit rotates the
refresh token roughly daily and invalidates the old one, so it is the single
piece of state that cannot be rebuilt from QuickBooks. If it is lost, repeat
step 4.

## 2. The Gmail app  (20 minutes, blocking)

Reminders go out from the billing mailbox so the send log and the thread history
live where Angie and William already work.

1. In Google Cloud, create an OAuth client for `billing@marketplaceofficer.com`.
2. Scopes: `gmail.send` and `gmail.readonly`. The read scope is how the agent
   picks up William's approval reply.
3. Authorize it and keep the refresh token.

## 3. Where it runs  (pick one)

**GitHub Actions** is already wired in `.github/workflows/ar-followup.yml`. It
works, with one caveat worth knowing: the rotated Intuit refresh token lives in
the Actions cache, and if that cache is evicted the agent locks itself out until
someone repeats step 1.4. Caches are evicted after 7 days without a hit, and the
workflow runs daily, so in practice it holds.

**A small always-on box** (a Mac mini, a cheap VM) avoids that entirely, because
the token file just sits on disk. If the agent is going to be load-bearing for
collections, this is the better home.

Either way, these go in as secrets:

```
QBO_CLIENT_ID          QBO_CLIENT_SECRET
QBO_REFRESH_TOKEN      QBO_REALM_ID        (bootstrap only)
GMAIL_CLIENT_ID        GMAIL_CLIENT_SECRET   GMAIL_REFRESH_TOKEN
```

## 4. Fill in the config  (Angie, 1 hour)

`config/clients.yaml` drives suppression, contract validation and who gets the
email. Two things are needed before any reminder can send:

- **Billing contacts.** Kaaral, Rust Check and Zendex have `billing_contact:
  null`. They are suppressed today so it does not block, but every client that
  is not suppressed needs a working address, either on the invoice in QBO or
  here.
- **Exact QuickBooks display names.** Matching is on the display name, so the
  real spelling has to be in `name` or `aliases`. Kaaral USA LLC and Zendex Tool
  Corp are correct as of September 2026. **Rust Check is not confirmed** — check
  it in QuickBooks and add the exact name.

Contract terms can be filled in over time. A client with no terms on file is
still chased normally; the agent just reports that it could not validate the
invoice amounts, which is how the four-month overbilling would have been caught.

Validate after every edit:

```bash
python -m ar_followup validate-config
```

## 5. Two weeks read-only  (the part worth not skipping)

```bash
python -m ar_followup scan --no-email --print    # build the digest, show it, send nothing
```

Read the matches section. This is the agent telling you which invoices it thinks
are already paid, and it is the claim everything else rests on. Check a handful
against QuickBooks by hand.

Then let the morning digest email run, and approve reminders by reply. Sending
is already gated on that reply, so there is no risk in this phase beyond a
reminder going to the wrong person.

One thing to expect on day one. Every client is billed on the 1st, so the whole
batch crosses the 10-day line together: the first live digest will propose
roughly 20 firm reminders at once, each copying Angie. A good share of them are
probably already paid and will show up as matches instead. Read section 1 before
approving section 2.

## 6. Turn on posting to QuickBooks  (after step 5)

In `config/settings.yaml`:

```yaml
payment_application:
  enabled: true
```

Then confirm matches by replying `CONFIRM M1 M2` to the digest, and:

```bash
python -m ar_followup apply-confirmed            # dry run, shows what it would post
python -m ar_followup apply-confirmed --live     # actually posts
```

### What posting does and does not do

It allocates a Payment that **already exists** in QuickBooks to the invoice it
belongs to. No money is created, nothing is deposited, the cash total in the
register does not move. Only the allocation changes, which is the difference
between an invoice that reads "open" and one that reads "paid".

It will not post a match sourced from a bank deposit, and that limit cannot be
configured away. A deposit is cash already sitting in the register; creating a
Payment for it books the same money a second time. Those come to you with the
fix spelled out: undo the categorization in the bank feed and use Find match.
The VI Derm invoice is exactly this case.

Four things stop a double application:

1. a confirmation is required, so the agent never decides a match is right
2. the ledger knows every payment-invoice pair it has posted
3. an audit stamp goes into the Payment's private note in QuickBooks, which
   survives even if the ledger is lost
4. the invoice and the payment are both re-read live immediately before the
   write, and anything that moved cancels it

Plus caps: 10 applications per run and $25,000 per application by default, and a
failed write is never retried, because a second POST after an unclear first one
is how money gets applied twice.

---

## The daily rhythm, once it is all on

| When | What happens |
|---|---|
| 7:00 AM PT | Scan. Reconciles, then emails William the digest. Nothing sent, nothing posted. |
| Whenever William replies | `CONFIRM M1 M2` for matches, `APPROVE R1 R3` for reminders |
| Hourly, 9 AM to 4 PM PT | Posts confirmed matches, then sends approved reminders, in that order |
| Three days after a confirmation | If the invoice is still open, the digest chases it as a confirmed match nobody applied |

## Still open

- **Credit memos are not posted.** A credit sitting on a client's account is
  matched and reported but applied by hand. Rust Check's $6,026.67 is the live
  case. Worth adding once payment posting has a few weeks on it.
- **The Intuit token in GitHub Actions** depends on the cache surviving. See
  step 3.
