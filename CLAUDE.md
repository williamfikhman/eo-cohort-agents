# Working rules for the agreement agent

These come from William directly. Follow them before anything else in this repo.

## The template is William's file, not ours

- `templates/source/Elleebana_Amazon_Services_Agreement.docx` is the agreement
  exactly as CMO sends it. It is the source of record. `tools/build_template.py`
  derives the working template from it by editing only the run text that holds
  per-client values.
- Never author, restyle, re-space or re-title the agreement. Fonts, margins,
  section breaks, the two-column signature block, list glyphs and numbering are
  William's and stay as they are. A rendered agreement is his form with the
  variables swapped and nothing else.
- Change only what William asked to change in that request. If a change seems
  necessary beyond that, say so in one line and leave the document alone.
- When William uploads a newer version of the agreement, replace the file in
  `templates/source/`, re-run the script, and re-render the regression clients.

## Per-client values

- Legal name, entity type, state and address: from the client's own site, state
  registry or HubSpot, in that order of trust. Say which source each came from.
- Signer email: the HubSpot contact record. William has said the shared support
  address is fine when that is what HubSpot has.
- Fees, commission, term, deliverables: from the proposal, quoted, never
  inferred. If the proposal is silent on payment timing, use the 1.2 language of
  the most recent executed agreement and say so.
- Effective date may be conditional ("<date>, or when Seller Central is
  verified, whichever is later"). Write it as given; it is a floor for billing.
- CMO block: name William Fikhman, title CEO, signature mark *W. Fikhman* in
  italics, date in the form's `M.D.YY` style on the day the agreement is built.

## Trademark exhibit, as a rule

- For every agreement, search the USPTO trademark database for the client's
  mark and capture a screenshot of the result. `tools/uspto_trademark.py` does
  this; the image lands in `build/<slug>/trademark-uspto.png` and is placed
  under Schedule C item 4 at render time.
- If no live registration is found, say so and render the exhibit as "TBD".

## Delivery

- William uploads the PDF to SignNow himself. Give him the clean render (no
  hidden anchors), named `<Client>_Amazon_Services_Agreement.pdf`.
- Nothing is ever sent, posted or emailed without his explicit go-ahead.
