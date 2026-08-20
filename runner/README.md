# runner/

| File | What it does |
|---|---|
| `run_agents.py` | The shift. Parses every `agents/*.md`, runs its RAFT through Claude with web search on, emails the output to the frontmatter address via Resend. |
| `ingest_sheet.py` | Fallback. Turns exported idea-sheet rows into agent files and commits them to main. |

## run_agents.py

Run by `.github/workflows/first-shift.yml`. Needs `ANTHROPIC_API_KEY` and
`RESEND_API_KEY` in the environment (repo secrets in CI).

- Model: `claude-sonnet-4-6`, `max_tokens` 1500, `web_search_20260209` enabled.
- A file that fails to parse is **skipped**, not fatal. So is an agent that errors
  at the API or the mailer.
- Exits non-zero **only** if zero agents succeeded. The per-agent
  ok / skipped / failed table is printed at the end of the job log.
- `RESEND_FROM` defaults to `EO Cohort Agents <agents@wetutorathome.com>`.
  `wetutorathome.com` is the verified Resend sender domain — `aplustutoring.com`
  is not, so don't switch it without verifying the domain first.

## ingest_sheet.py — the CSV path

**This reads a CSV, not the Google Sheet directly.** That was a deliberate choice:
the booth Worker writes to HubSpot and Resend only, there's no Sheets integration
to reuse, and no portable service-account credential that this repo could carry.
Wiring a service account would mean provisioning a key, sharing the sheet with it,
and storing the JSON as a third secret — more moving parts than a one-night fallback
should have, and every one of them a thing that can fail at 6 AM. A CSV export is two
clicks and cannot fail in a new way.

```bash
# In the sheet: File -> Download -> Comma-separated values (.csv)
python runner/ingest_sheet.py ~/Downloads/ideas.csv --dry-run   # look first
python runner/ingest_sheet.py ~/Downloads/ideas.csv             # write, commit, push
```

It figures out the columns from the sheet's headers (anything containing "email",
"name", "agent", "raft"/"idea"/"task", "schedule"), and falls back to scanning every
cell for an address and for a RAFT-shaped body. A row needs a usable email **and** a
body containing a TASK section to be ingested; everything else is listed as skipped
with a reason. Existing agent files are never overwritten without `--force`, so a
founder whose PR did land keeps their own version.
