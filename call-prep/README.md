# Daily Call Prep Agent (CMO)

Runs every weekday morning, reads William's calendar for the day, researches every external
meeting (HubSpot history → Amazon presence → DTC pricing → news), and emails one prep brief
to william.fikhman@gmail.com before the first call.

It is a Claude Code headless run (`claude -p`) over four MCP connections, with the
error-prone parts pushed into small deterministic Python modules that can be run and tested
on their own:

| Piece | What it owns |
|---|---|
| `prep-brief.md` | The master prompt. `claude -p "$(cat prep-brief.md)"` is the whole agent. |
| `prompts/01..04-*.md` | Step procedures the master prompt follows; each is also runnable alone via `--step N`. |
| `bin/filter_events.py` | Meeting selection rules (external attendee / outside link, no all-day, no declined, dedupe). |
| `bin/research.py` | Amazon + DTC research for one brand. Standalone: `--brand X --site Y`. |
| `bin/build_email.py` | Brief JSON → HTML + text email, subject line, chronological order. |
| `bin/call-prep` | Wrapper: `--date`, `--step`, `--no-send`, `--supplement`, tool allowlist, run logs. |
| `.mcp.json` | Google Calendar, HubSpot, Gmail, fetch. |
| `crontab.example` | 6:00 AM PT weekdays (+ optional 10:00 AM supplement). |

## Setup (once, on the machine that will run cron)

```bash
cd call-prep
pip install -r requirements.txt && playwright install chromium     # Amazon needs a real browser
mkdir -p ~/.config/call-prep
```

Credentials, all read-only except Gmail send:

1. **Google Calendar + Gmail** — create an OAuth Desktop client in Google Cloud (enable the
   Calendar API and Gmail API), download the JSON to `~/.config/call-prep/google-oauth.json`
   and copy it to `gmail-oauth.json`. First run of each server opens a browser for consent
   and stores a refresh token; do that interactively once:
   `npx @cocal/google-calendar-mcp auth` and `npx @gongrzhe/server-gmail-autoauth-mcp auth`.
   Scopes: calendar read-only, gmail.send.
2. **HubSpot** — Settings → Integrations → Private Apps → create one with only
   `crm.objects.contacts.read`, `crm.objects.companies.read`, `crm.objects.deals.read`,
   `crm.schemas.*.read`, `sales-email-read`. Export it as `HUBSPOT_ACCESS_TOKEN`.
   Portal 22483434, pipeline `default`. The wrapper also allowlists only the HubSpot read tools,
   so a headless run cannot call a write tool even if the token allowed it.
3. Put the exports in `~/.config/call-prep/env` (cron sources it):
   ```bash
   export HUBSPOT_ACCESS_TOKEN=pat-na2-...
   export GOOGLE_OAUTH_CREDENTIALS=$HOME/.config/call-prep/google-oauth.json
   export GOOGLE_CALENDAR_MCP_TOKEN_PATH=$HOME/.config/call-prep/calendar-token.json
   export GMAIL_OAUTH_PATH=$HOME/.config/call-prep/gmail-oauth.json
   export GMAIL_CREDENTIALS_PATH=$HOME/.config/call-prep/gmail-token.json
   export PATH=/opt/homebrew/bin:$HOME/.local/bin:$PATH   # node/npx, uvx, python3, claude
   # optional: export CALL_PREP_MODEL=claude-sonnet-5
   ```
   `.mcp.json` reads exactly these variables; `source ~/.config/call-prep/env` before a manual run.

## Running it

```bash
./bin/call-prep --step 1                       # calendar read + filter → console table
./bin/call-prep --step 2                       # + HubSpot enrichment
./bin/call-prep --step 3                       # + Amazon/DTC/news research cards
./bin/call-prep --step 3 --brand "Dr. Squatch" --site drsquatch.com   # research module alone, no Claude
./bin/call-prep --no-send                      # full brief to out/<date>.html, no email
./bin/call-prep --date 2026-09-14              # any day
./bin/call-prep                                # the real thing: research + email
./bin/call-prep --supplement                   # only meetings added since the morning run
```

Exactly as the spec says, `claude -p "$(cat prep-brief.md)"` from this directory also works
(today, full, send). The wrapper adds the date override, the tool allowlist, the lock file,
and the run log — use it for cron.

### Logs (`logs/`)
- `<date>.json` — written by the agent: inputs, every source URL / tool call, failures, email id.
- `<date>.run.json` — claude's JSON envelope: turns, cost, duration, `is_error`, final text.
- `<date>.events.json` (raw calendar), `.meetings.json` (filtered), `.hubspot.json`,
  `.research.json` + one `.research.<eventid>.json` per brand, `.brief.json`.
- Supplement runs use `<date>.supplement.*`.

A data source that fails does not abort the run; that section is marked `[unavailable]` in the
email and listed under `failures`.

## Meeting selection (section 3)
`bin/filter_events.py` includes an event when it has an attendee outside
`marketplaceofficer.com`, or a Zoom/Meet/Teams link whose organizer is external. It drops
all-day events, cancelled events, anything William declined, internal meetings and personal
blocks (which go into the one-line internal list), and marks a second booking by the same
company + people as `duplicate_of` so it is researched once and shown with both times.
Company name comes from the HubSpot booking form's "Company name:" line, then the event title
(`X & William from Chief Marketplace Officer`), then the attendee's domain (never gmail etc.).

## Research module (section 4C/4D)
`bin/research.py` is the part that decides what the pitch is, so it is the part with tests.
It reads the DTC homepage (platform detection: `cdn.shopify.com` / `myshopify.com` → Shopify,
WooCommerce, BigCommerce, …), pulls prices from Shopify's public `/products.json` (JSON-LD
fallback elsewhere), looks for a Where-to-Buy page that links to Amazon, then reads the Amazon
search page for the brand (brand-matched vs sponsored results, prices, ratings, review counts,
Storefront link), opens the top listing by review count (buy-box seller → 1P/3P, "New (N) from",
A+ / Brand Story, images, availability) and the all-offers panel (distinct sellers), and finally
matches DTC titles to Amazon titles to surface price gaps. Every number carries its URL; every
miss is `unverified` and listed under `failures`. It never estimates.

Amazon blocks plain HTTP, so it drives headless Chromium via Playwright when installed and
falls back to `requests` (`--no-browser`) otherwise. If Amazon still serves a bot check the
report says `blocked` and the prompt tells Claude to confirm presence with one
`site:amazon.com` search and leave seller/review/price fields unverified.

## Tests
```bash
python3 -m unittest discover -s tests -v
python3 bin/research.py --brand "Acme Naturals" --site acmenaturals.com --deep 2 --fixtures tests/fixtures   # full card, offline
```
`--fixtures DIR` (or `RESEARCH_FIXTURE_DIR`) serves pages from `DIR/manifest.json` (URL → file)
instead of the network, so the whole pipeline can be exercised without touching Amazon.
Fixtures: a trimmed copy of a real day's calendar, an Amazon search page, a 3P and a 1P product
page, an all-offers panel, a Shopify homepage + products feed, and a brief JSON.

## Decisions made up front (section 1)
- **6:00 AM PT regardless of first-call time.** The brief has to be there before the first
  call; the earliest calls on the calendar are 8:00–8:45 AM, and the run takes several minutes
  per meeting, so 6:00 leaves headroom. Change the cron line if the first call moves earlier.
- **Same-day additions.** A second cron line at 10:00 AM PT runs `--supplement`: it re-reads the
  calendar, drops every meeting id already in the morning log, and sends a "Call Prep
  Supplement" only if something new was booked. It sends nothing otherwise. It is in
  `crontab.example` and can be deleted if unwanted.
- **Checkpoint before email.** Steps 1–3 print to the console and never email. Nothing lands in
  the inbox until cron is installed or `./bin/call-prep` is run without `--step`/`--no-send`.

## Rules the prompt enforces (section 7)
No invented numbers ("unverified" instead). A source URL for every Amazon or pricing claim.
Short copy. HubSpot read-only — enforced twice: private-app scopes and the wrapper's tool
allowlist. The only write action is the Gmail send.
