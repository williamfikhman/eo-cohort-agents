# Step 4 — Brief assembly + HTML email

Input: `logs/DATE.meetings.json`, `logs/DATE.hubspot.json`, `logs/DATE.research.json`.
Output: `logs/DATE.brief.json` → `out/DATE.html` via `bin/build_email.py` → Gmail send.

## Write `logs/DATE.brief.json`
Schema is documented at the top of `bin/build_email.py`. Content rules:

- `summary`: exactly 3 lines for the whole day. Line 1: how many external calls and the one that
  matters most and why. Line 2: the strongest hook of the day (a price gap, a lost buy box, a
  re-engagement). Line 3: anything to watch (a no-show risk, a call where research came back
  unavailable, a partner joining).
- Per meeting:
  - `start`/`end` as `HH:MM` 24h Pacific; `duration_min`; `also_at` for duplicate bookings.
  - `priority`: 1 = highest. Ties in start time are ordered by this. Higher priority for: open
    deal in Proposal / Follow Up stages, larger catalog, clear price-gap hook.
  - `hook`: 2–3 bullets. Each one specific and factual with a number or a name in it, taken from
    the research JSON. "Your $89 serum is being sold by three unauthorized resellers at $61" is a
    hook; "strong brand presence" is not. If nothing verifiable exists, one bullet stating what
    is unverified and the single most useful question to open with.
  - `status`: one sentence — on Amazon / not on Amazon / on Amazon but doesn't control it —
    copied from the research status line and tightened.
  - `links`: `amazon_search` (always `https://www.amazon.com/s?k=<brand>`), `storefront` (only
    if found), `site`, `hubspot_contact`, `hubspot_deal`, and `other` for LinkedIn profiles and
    the top Amazon listing.
  - `questions`: 3 discovery questions tailored to what the research found (who runs the account,
    who sells to Amazon, how they price across channels, what their contractor/agency is doing).
  - `objections`: 1–2 likely objections with a one-line response each, grounded in the deal
    history (e.g. price sign-off from partners, "we already have someone on it").
  - `history`: prior history with us from HubSpot in one or two sentences, or null.
  - `sources`: label + URL for each claim in the hook and status.
  - `unavailable`: list any of `calendar`, `hubspot`, `amazon`, `dtc`, `signals` that failed.
- `internal`: one entry per internal/personal meeting from the filter, `"H:MM Title"`, in order.
- `failures`: every failure recorded during the run, one line each.
- `mode`: `full` or `supplement`.

If there are no external meetings: `meetings: []`, `summary` = ["No external meetings today.",
"<internal schedule count>", ""], and the internal line still lists the day.

## Render
```
python3 bin/build_email.py logs/DATE.brief.json --print
```
It writes `out/DATE.html` and `out/DATE.txt` and prints `{"subject": ..., "html": ...}`.
The subject is `Call Prep — {Day, Date} — {N} meetings`. Read `out/DATE.html`.

## Send
Only if `run.json` has `send: true` (default when run.json is absent). Use the Gmail MCP
server's `send_email` tool exactly once:
`to: ["william.fikhman@gmail.com"]`, `subject` from the build step, `htmlBody` = the full
contents of `out/DATE.html`, `mimeType: "text/html"`, plain `body` = `out/DATE.txt`.
Record the returned message id in the run log. If the send fails, retry once after 30 seconds,
then record the failure — the HTML is still on disk in `out/`.

If `send` is false, print the text version to the console and say where the HTML is.
