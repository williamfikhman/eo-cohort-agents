---
description: Build CMO proposals for upcoming proposal calls, or for one named company
argument-hint: [company name]
---

Run the proposal sweep. Argument (may be empty): `$ARGUMENTS`

## If an argument was given

Treat it as one company name. Build (or rebuild) that proposal even if
`output/` already has a folder for it. Skip the calendar. Dispatch the
`proposal-builder` subagent once with the company name and the note that this is
a named request.

## If no argument was given

1. Read the calendar with the google-calendar MCP server: every event on the
   primary calendar from now through 5 days from now. Keep events whose title
   contains "proposal" (case-insensitive). Ignore declined and cancelled events.
2. For each event, extract the prospect name from the title. Drop the words
   proposal, call, meeting, with, and separators such as "-", ":", "/", "|".
   What remains is the prospect hint. Also collect the attendee email domains that
   are not ours; they help HubSpot matching.
3. Check `output/`. If a folder whose name matches the prospect hint (slugified)
   already exists, skip it as already quoted and say so.
4. For each remaining prospect, dispatch the `proposal-builder` subagent once,
   passing: the prospect hint, the event title, date and time, and the attendee
   emails. Run builders one at a time so the calendar and CRM are not hammered.

## After the builders finish

Print one line per prospect, in this shape and nothing more:

- **Built** Brow Down Studio → `output/brow-down-studio/` (2 pages, $3,500/mo + 8%, no base)
- **Skipped** Acme → already quoted on 2026-09-08
- **Stopped** Foo Co → HubSpot has no company record; not building

Then list any conflicts the builders reported, one line each. No other summary.

Never send anything. Never write to HubSpot.
