# CMO Agreement Agent

Turns a client config file into a SignNow document with the signature fields
already placed, then stops and waits for you.

```
agreement prepare  <slug>   render → PDF → upload → place fields → build invite → STOP
agreement preview  <slug>   re-print the review link and the field map
agreement send     <slug>   send the invite (types-the-client-name to confirm)
agreement status   <slug>   invite status
agreement clients           list configured clients
```

## Setup

```bash
pip install -e .
cp .env.example .env          # then fill in your SignNow credentials
```

LibreOffice does the PDF export and **the Writer component is required** —
`libreoffice-core` on its own cannot read a .docx and fails with a misleading
"source file could not be loaded".

```bash
sudo apt-get install libreoffice-writer   # Debian/Ubuntu
brew install --cask libreoffice           # macOS
```

Then fill in `config/cmo.yaml`. It ships with `TODO:` markers in the fields only
you can supply, and nothing will render while a marker remains.

## Adding a client

Copy `clients/acme-sandbox.yaml`, rename it to your client's slug, and edit. Every
variable is required; a missing one stops the run before anything is rendered.

| Variable | Notes |
|---|---|
| `company_legal_name` | |
| `entity_type` | `LLC` or `Corporation` |
| `state_of_incorporation` | |
| `company_address` | |
| `signatory_name` | Also pre-fills the printed-name field |
| `signatory_title` | |
| `signatory_email` | Where the invite goes |
| `effective_date` | **Quote it**, or YAML turns it into `2026-01-05` |
| `monthly_fee` | Free text, e.g. `$8,500 per month` |
| `commission_pct` | Free text, e.g. `4%` |
| `commission_terms` | Free-form sentence; baselines and prepayment terms go here |
| `initial_term_months` | Whole number |
| `schedule_b_deliverables` | List; one bullet each in Schedule B |
| `trademark_exhibit` | Often `TBD` |

A typo'd key is an error, not a shrug: an unrecognised variable would otherwise be
dropped silently and you would ship an agreement missing a term.

## The approval gate

`prepare` goes all the way through uploading and field placement, then stops. It
prints a review link and writes the exact invite payload to
`build/<slug>/prepared.json`. Nothing has been emailed at that point.

`send` is the only command that emails a client. It requires the client's full
legal name typed back — the slug is not accepted — and refuses outright if the
document was prepared with `--dry-run`.

`--dry-run` works on both commands and blocks every write to SignNow while still
letting reads through, so a dry run tells you something true about the account.

## The template

`templates/amazon_services_agreement.docx` is a **scaffold**. The executed Word
agreement was not in the repository, so it has the right structure — every
variable, all five schedules, both signature blocks, the SignNow anchors — with
`[CLAUSE TEXT PENDING ...]` where your clause bodies belong.

Replacing it is a paste job. Open it, paste the real text over each marker, leave
the `{{ ... }}` placeholders where they sit, save. Nothing carrying a PENDING
marker will render without `--allow-scaffold`, so the scaffold cannot reach a
client by accident.

`python tools/build_template.py` regenerates the scaffold from scratch, which
overwrites your edits — only run it if you want to start over.

### Why the tags are written the way they are

docxtpl and SignNow both use `{{ }}`. Writing `{{ClientSignature}}` straight into
the Word file would make docxtpl treat it as one of its own variables and render
it away. So the template holds `{{ sn_client_signature }}` and `render.py` fills
that with the literal SignNow tag. The tag names live in `src/fields.py`, once,
and are used both to write the document and to build the upload payload.

Tags are set in white so they are invisible on the page.

## Field placement

Fields are placed by SignNow's **complex text tags**: the tag sits in the document
where the field belongs, and no x/y coordinate is ever sent. Reflowing the
contract by a line moves the tag and the field follows. See
`docs/signnow-api-notes.md`.

Three checks run before anything is sent:

1. No unfilled placeholder survives into the rendered document. Undefined
   variables raise rather than rendering blank.
2. Every tag is still findable in the PDF's text layer after export — a tag split
   across formatting runs would upload cleanly and silently produce a document
   with no signature field.
3. SignNow reports back at least as many fields as tags sent, and the client role
   exists. `prepare` prints each field with the page and coordinates SignNow
   assigned it.

## Audit log

Every API call appends one JSON line to `logs/signnow-audit.log`: timestamp,
action, method, path, document id, HTTP status, dry-run flag. Credentials are
redacted before writing, so the log is safe to read and share. Both `logs/` and
`build/` are gitignored — they hold client data.

## Tests

```bash
pytest
```

The render tests run LibreOffice and take a few seconds; they skip if it is
missing. No test touches the network.
