# Step 3 — Research per meeting (people, Amazon, DTC, signals)

Input: `logs/DATE.meetings.json` + `logs/DATE.hubspot.json`. Output: `logs/DATE.research.json`
keyed by event id, and a console card per meeting. Do the sub-steps in order. Amazon first,
because the DTC price comparison depends on it.

## A. Who's on the call
- From the calendar record: attendee names, emails, email domain, the meeting link, and the
  booking link (`booking_link` — William's HubSpot meeting links). Titles from HubSpot `jobtitle`.
- LinkedIn: one web search per person, `"<name>" "<company>" linkedin`. Only record a URL that
  actually appears in results for that name; otherwise leave `linkedin: null`. Do not guess URLs.
- Company name: `company` from the filter (booking form "Company name:" field), else the HubSpot
  company name. If the booking form value is not a real name (an EIN, a URL, a person's name),
  use the HubSpot company name or the email domain and note the mismatch.

## B. Brand + site
- DTC site = HubSpot company `website`/`domain`, else the attendee's email domain (never a
  free-mail domain), else one web search `"<company>" official site`. Record where it came from.

## C. Amazon + DTC — run the module, do not eyeball pages
```
python3 bin/research.py --brand "<Brand>" --site <site> --deep 2 --out logs/DATE.research.<eventid>.json
```
Print the card it outputs. It reports, with a source URL for each: platform (Shopify /
WooCommerce / BigCommerce / custom), 3–5 DTC prices, Where-to-Buy page and whether it links
to Amazon, the Amazon search link, storefront link (means Brand Registry), brand-matched
result count (catalog lower bound), sponsored count, top 3 by review count, average rating,
top listing's buy-box seller (1P if Sold by Amazon, else 3P + seller name), other-offer count,
distinct sellers from the all-offers panel, listing quality flags (no A+, no Brand Story, thin
title, few images, out of stock), and DTC-vs-Amazon price gaps.

Rules for reading its output:
- Anything the module lists under `failures` or prints as `unverified` stays unverified in the
  brief. Do not fill it in from memory or from a web search snippet.
- If the module reports `blocked` for Amazon (bot check), retry once with `--pages 1 --deep 1`.
  If still blocked, use ONE web search `site:amazon.com "<Brand>"` to confirm whether ASINs exist
  (presence only, cite the result URLs) and mark seller/review/price fields unverified.
- If the brand is NOT on Amazon: say so plainly, then run one web search
  `<category> best sellers amazon` and name 1–2 competitor brands that are (cite URLs).
- Storefront link only if found on a page (`amazon.com/stores/...`). Never construct one.

## D. Where-to-buy / price gaps
Take the module's `price_comparison`. A gap where Amazon is cheaper than DTC (or a third-party
seller undercuts) is the single best opener — it goes first in the hook. Quote exact prices and
both URLs. If there are no matched pairs, say "no DTC/Amazon price match found" rather than
implying parity.

## E. Signals (web search, last 6 months only)
- `"<Brand>" funding OR launch OR retail OR "chief" OR appoints 2026` — keep only items dated
  within 6 months of DATE, with URL and date. Otherwise write "no recent news found".
- Category + rough price tier from the DTC prices (value < $20, mid $20–60, premium > $60 for
  most CPG; use judgment for the category and say what anchor you used).
- Dominant competitor in their Amazon category: from the search page results that are NOT
  brand-matched (the module's `results_on_page` minus `brand_matched_results`), or one web search.
  Name it only if you can cite a URL.

Write `logs/DATE.research.json` = `{"<event id>": {"people": [...], "brand": "...", "site": "...",
"site_source": "...", "module": <the research.py JSON>, "signals": {...}, "sources": [...]}}`.

Console card per meeting, in this order: company · attendees · status line · hook candidates
(3 bullets, each with a URL) · price gaps · Amazon facts · DTC facts · signals · unverified list.

If `run.json` says `step: "3"`, stop here.
