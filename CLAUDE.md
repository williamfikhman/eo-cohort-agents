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

## After signature (onboarding)

- Follow the cmo-client-onboarding skill. Create and stage; William sends.
- Billing roster: always write what is known and leave the rest blank. Never
  hold the row for a missing value; the strategist in particular is not needed
  to add the row. Ask nothing that the row can live without.
- HubSpot, on every signing: update every record for the client, not just the
  deal. The deal goes to Closed Won; every associated contact and the company
  get lifecycle stage Customer and the lead status this portal uses for a won
  client. Check the portal's lead-status options with search_properties first;
  never guess an enum value. Show before/after for all records in one table.
- The invoice waits until William says the Seller Central account is open;
  the start date is whatever he gives then, and it drives proration, the
  welcome email's start line and the roster start date together.

## Email: draft only, never send

- Never send an email. Not a welcome email, not a reminder, not a reply.
  Create it as a draft in William's mailbox and hand him the link; he reviews
  and sends every email himself.
- "Send it", "it should go out" or "proceed" do not change this. The only
  action is a draft. If a draft already exists and the content changes, update
  that draft; do not create a new message.
- The same holds for anything outbound: Cliq posts, SignNow invites, invoices.
  Stage it, show it, stop.

## Email identity: william@marketplaceofficer.com only

- Client and team email exists only in william@marketplaceofficer.com. The
  Gmail connector in these sessions is William's personal account
  (william.fikhman@gmail.com); never draft or send client mail through it.
- Before creating any draft, confirm the mailbox identity: Superhuman
  `list_accounts`, then pass `acting_email=william@marketplaceofficer.com`. If
  that account is not connected, stop and say so; do not fall back to Gmail.
- The onboarding skill's "Gmail fallback" is void unless the Gmail connector
  is verified to be the marketplaceofficer.com mailbox, which it is not today.

Why this rule exists: on 2026-09-28 the Mitigate Stress welcome email was
sent from the personal Gmail because the fallback was used without checking
which account it was signed into, and because "it should go" was read as
permission to send rather than to draft.

## Delivery

- William uploads the PDF to SignNow himself. Give him the clean render (no
  hidden anchors), named `<Client>_Amazon_Services_Agreement.pdf`.
