# Step 2 — HubSpot enrichment (read-only, portal 22483434, pipeline `default`)

Input: `logs/DATE.meetings.json` → `external[]`. Output: `logs/DATE.hubspot.json`, keyed by
calendar event id. Use only search/read tools. Never create or update anything.

For each external meeting (skip ones with `duplicate_of`):

1. **Contacts.** Search CONTACT by each external attendee `email` (filter `email` EQ). If no
   hit, search by the company name in `company`. Request properties:
   `firstname,lastname,email,jobtitle,company,lifecyclestage,hs_lead_status,hs_analytics_source,
   hs_latest_source,createdate,notes_last_contacted,notes_last_updated,hs_last_sales_activity_timestamp,num_associated_deals`.
   Record `https://app.hubspot.com/contacts/22483434/record/0-1/{contactId}` for each.
2. **Company.** Search COMPANY by `domain` EQ the attendee's email domain (skip free-mail
   domains like gmail.com), else by name. Properties: `name,domain,website,industry,lifecyclestage,
   createdate,num_associated_deals`. Note the `website` — step 3 uses it as the DTC site.
   Company record URL: `https://app.hubspot.com/contacts/22483434/record/0-2/{companyId}`.
3. **Deals.** List deals associated with the contact (and company). Properties:
   `dealname,dealstage,pipeline,amount,deal_currency_code,createdate,closedate,closed_lost_reason,hs_lastmodifieddate`.
   Map `dealstage` ids to labels — the `default` pipeline uses:
   `appointmentscheduled`=Discovery Scheduled, `42566467`=Follow Up Scheduled,
   `presentationscheduled`=Proposal Scheduled, `168300579`=Proposal Follow Up, `42459793`=No Show,
   `decisionmakerboughtin`=Not Scheduled A, `197273963`=Not Scheduled B, `197273964`=Not Scheduled C,
   `contractsent`=Contract sent, `213606765`=On Hold, `closedwon`=Won, `closedlost`=Lost,
   `180893347`=Lost Client, `196450364`=Trade Show Meeting, `1082480191`=Not Scheduled - Referral.
   (If an id is not in this list, fetch the `dealstage` property definition once and use its label.)
   Deal URL: `https://app.hubspot.com/contacts/22483434/record/0-3/{dealId}`.
4. **Last activity.** Fetch the most recent NOTE, EMAIL, MEETING and CALL engagements associated
   with the contact (sort by `hs_timestamp` desc, limit 3 each). Write a one-line summary of the
   most recent one: date, type, direction, and what it said. Prefer a logged call/meeting note
   over an automated sequence email when both exist within a few days.
5. **Re-engagement flag.** Set `re_engagement: true` when any of: a deal in Lost / Lost Client /
   No Show / On Hold / Not Scheduled stages; a deal `createdate` older than 90 days; contact
   `createdate` older than 180 days with a prior Won/Lost deal. In that case write one sentence:
   what we pitched before and what happened (from the deal stage, `closed_lost_reason`, and notes).
6. If HubSpot is unreachable or a lookup errors, record `{"error": "..."}` for that meeting and
   continue. The brief marks HubSpot `[unavailable]` for that meeting.

Write `logs/DATE.hubspot.json`:
```json
{"<event id>": {"contacts": [...], "company": {...}, "deals": [...],
  "last_activity": "2026-09-10 · outbound email · sent deck + 90-day process after discovery call",
  "re_engagement": false, "prior_history": null,
  "urls": {"contact": "...", "company": "...", "deal": "..."}}}
```
Print one block per meeting on the console: contact + title, lifecycle, deal stage/amount/created,
last activity line, re-engagement flag.

If `run.json` says `step: "2"`, stop here.
