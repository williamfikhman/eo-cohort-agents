# CMO Proposal Agent

Chief Marketplace Officer (CMO) helps consumer and CPG brands win on marketplaces
(Amazon, Walmart, TikTok Shop). After a proposal call, the prospect gets a short,
branded proposal. This project builds that document. It does not send it.

Pipeline: Google Calendar finds the call → HubSpot names the company and contact →
Granola says what was discussed and quoted → you write `output/<slug>/spec.json` →
`scripts/cmo_proposal.py` renders the .docx and .pdf into `output/<slug>/`.

## Where things live

| Path | What |
|---|---|
| `.claude/commands/proposals.md` | `/proposals` — the sweep. Finds prospects, dispatches the builder once per prospect. |
| `.claude/agents/proposal-builder.md` | The subagent that builds one proposal end to end. |
| `scripts/cmo_proposal.py` | Renders a spec JSON into .docx (+ .pdf with `--pdf`). |
| `assets/template_spec.json` | The blank proposal: brand, standard pricing, standard scope, default terms. Copy it, never edit it per prospect. |
| `assets/CMO Proposal Template.docx` | Branded blank document. Supplies page setup and styles. Regenerate it from the spec (see README). |
| `output/<slug>/` | One folder per prospect: `spec.json`, the .docx, the .pdf. Git-ignored. |

## Standard pricing

- Retainer: **$3,500 per month**.
- Performance fee: **8%** of attributable marketplace revenue.
- Base: **none by default.** A base is a monthly revenue baseline; when one is set,
  the 8% applies only to revenue above it. Add a base only when one actually came up
  in the Granola notes or the HubSpot deal, and use that number verbatim.
- Term: 6 months, then month to month.

To change the standard rate, edit the numbers here **and** in Step 4 of
`.claude/agents/proposal-builder.md`. Defaults for a blank document live in
`assets/template_spec.json`.

## Rules

1. **The company name comes from HubSpot**, not the calendar title. "Brow Down" on
   the calendar is "Brow Down Studio" on the document if that is the HubSpot record.
2. **Never invent pricing, scope, or facts.** Everything in the proposal traces to
   Granola, HubSpot, or the standard pricing above. If a number was quoted on the
   call, that number goes in the document, not the standard rate.
3. **Conflicts:** if Granola and HubSpot disagree (price, base, scope, contact), use
   the more recent source, record the conflict in `internal_notes.conflicts` in the
   spec, and say it plainly in the run report.
4. **Nothing gets sent.** No email, no HubSpot writes, no e-signature. William reviews
   and sends. HubSpot is read-only for this project.
5. **No placeholders in a finished document.** Render with `--strict`; the script
   refuses any remaining `[bracketed]` text.
6. **Short.** The rendered PDF must fit within `max_pages` (default 4). If the page
   check fails, cut scope bullets and situation bullets, never pricing or terms.
7. **One proposal per new prospect per sweep.** A prospect already has a folder in
   `output/` means it was quoted. Rebuild only when `/proposals <Company>` names it.
8. **Fictional or unresolvable prospects stop the run for that prospect**, with a
   one-line reason. Do not build a document for a company HubSpot cannot find.

## Spec conventions

- `slug`: lowercase, hyphens, from the HubSpot company name (`brow-down-studio`).
- Files: `output/<slug>/spec.json`, `output/<slug>/CMO Proposal - <Company>.docx`,
  and the matching `.pdf`.
- `date` empty means today. `valid_days` sets "Valid through".
- Simple `**bold**` markup works inside any text string.
- `internal_notes` is never rendered. Put sources, dates, and conflicts there.

## Rendering

```bash
python3 scripts/cmo_proposal.py output/<slug>/spec.json \
  -t "assets/CMO Proposal Template.docx" \
  -o "output/<slug>/CMO Proposal - <Company>.docx" --pdf --strict
```

Exit codes: 0 ok, 1 bad input or missing tool, 2 the PDF is over `max_pages`.
