# eo-cohort-agents

Twenty-one agents were hired at the EO LA Valley workshop on August 20, 2026. This
is their office. Each one lives in a single Markdown file under `agents/`, gets woken
up by a GitHub Action, does its job, and emails the founder who hired it. They do not
take breaks, they do not have opinions about the thermostat, and they have never once
asked about equity. Minion #23 is very proud of them.

Every agent is one file: YAML frontmatter on top (who owns it, where the report goes,
when it's supposed to work), then a RAFT below — **R**ole, **A**udience, **F**ormat,
**T**ask. The frontmatter is how the runner finds you; the RAFT is the whole job
description. Copy `agents/eolav-event-promo.md`, change the four frontmatter fields to
yours, rewrite the RAFT for your agent, save it as `agents/your-agent-name.md`, and open
a PR. Roman merges it live. Tomorrow at 7:00 AM PT the runner wakes every agent in this
folder, hands its RAFT to Claude with web search switched on, and emails you whatever
comes back. A file that doesn't parse gets skipped with a note in the log — one broken
agent never takes down the rest of the shift.

## The file format

```markdown
---
name: Your Name
email: you@yourcompany.com
agent: kebab-case-agent-name
schedule: Mondays 8 AM
---

ROLE: You are a ...

AUDIENCE: ...

FORMAT: ...

TASK: ...
```

All four frontmatter fields are required. `agent` should match the filename
(`agents/<agent>.md`) — it's what shows up in your email subject line. `schedule` is
documentation for now: the runner is on a single cron, and honoring per-agent schedules
is a tomorrow problem.

## Running it

| What | How |
|---|---|
| The real shift | Cron in `.github/workflows/first-shift.yml` — 14:00 UTC on Aug 21 (7:00 AM PDT) |
| A test run | Actions tab → **First shift** → *Run workflow* |
| Backfill from the idea sheet | `python runner/ingest_sheet.py ideas.csv` (see `runner/README.md`) |

Needs two repo secrets: `ANTHROPIC_API_KEY` and `RESEND_API_KEY`. Both are already set.

## The one that isn't a Markdown file

`ar_followup/` is the AR follow-up agent for Chief Marketplace Officer. It does not
live in `agents/` and it is not a RAFT: it holds OAuth credentials, talks to the
QuickBooks Online and Gmail APIs directly, and has an approval gate between what it
proposes and what a client ever sees. The runner would only hand it to a web search,
which is not what it does.

It runs on its own cron in `.github/workflows/ar-followup.yml` — a morning scan that
reconciles loose payments against open invoices, emails William a digest of what
matched and what is genuinely still owed, then hourly passes that send only what he
approved by reply. It never writes to QuickBooks.
Setup, cadence and the config Angie maintains are in
[`ar_followup/README.md`](ar_followup/README.md); a sample digest is in
[`docs/sample-digest.txt`](docs/sample-digest.txt).
