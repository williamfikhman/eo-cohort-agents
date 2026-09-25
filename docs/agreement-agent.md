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
cp .env.example .env
```

Then put a SignNow **API key** in `.env` as `SIGNNOW_ACCESS_TOKEN`. The key
comes from the SignNow developer dashboard and is used directly as the bearer
token, so a Google sign-in account works without ever setting a SignNow
password. Keys are per-environment: generate one for the sandbox
(`api-eval.signnow.com`) to test with, and a separate one for production.

The password grant (`SIGNNOW_CLIENT_ID`, `SIGNNOW_CLIENT_SECRET`,
`SIGNNOW_USERNAME`, `SIGNNOW_PASSWORD`) is still supported for an account that
has a native password, but it is not needed when an API key is set.

`prepare` verifies the credentials against SignNow before rendering anything,
so a bad or expired key is the first line of output, not a failure after the
upload.

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

`templates/amazon_services_agreement.docx` is derived from William's own Word
file, `templates/source/Elleebana_Amazon_Services_Agreement.docx`, by
`tools/build_template.py`. The script edits only the text of the runs that hold
per-client values and leaves every style, font, margin, section and column of
the original in place, so a rendered agreement is the sent form with the
variables swapped and nothing else.

To change contract language, edit the source .docx in Word and re-run the
script. The two real client files, `clients/aminomega.yaml` and
`clients/elleebana.yaml`, double as regression checks.

### Why the tags are written the way they are

docxtpl and SignNow both use `{{ }}`. Writing `{{ClientSignature}}` straight into
the Word file would make docxtpl treat it as one of its own variables and render
it away. So the template holds `{{ sn_client_signature }}` and `render.py` fills
that with the literal SignNow tag. The tag names live in `src/fields.py`, once,
and are used both to write the document and to build the upload payload.

Tags are set in white so they are invisible on the page.

## Trademark exhibit

Every agreement carries a screenshot of the client's USPTO trademark record
under Schedule C item 4. Capture it before preparing:

```bash
python tools/uspto_trademark.py <slug>            # brand name from the config
python tools/uspto_trademark.py <slug> --mark "EXACT MARK"
```

It saves `build/<slug>/trademark-uspto.png`, which the render picks up
automatically. Without it the exhibit reads whatever `trademark_exhibit` says
in the client file, normally `TBD`, and `prepare` says so in yellow.

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
