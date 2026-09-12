# Step 1 — Calendar read + meeting filter

Goal: the list of meetings to research for DATE, printed to the console and saved to disk.

1. Determine DATE and mode from `./run.json` (see prep-brief.md). Create `logs/` if missing.
2. Call the Google Calendar MCP `list-events` tool on the primary calendar with
   `timeMin = DATE 00:00:00 America/Los_Angeles` and `timeMax = DATE+1 00:00:00`
   (ISO 8601 with offset, e.g. `2026-09-14T00:00:00-07:00`), `timeZone = America/Los_Angeles`,
   max results 100. Page through `nextPageToken` if present.
3. Save the raw tool response verbatim to `logs/DATE.events.json` (an object with an `events`
   or `items` array). Do not edit or summarize it. This is the audit trail.
4. Run the deterministic filter and save its output:
   ```
   python3 bin/filter_events.py --date DATE --in logs/DATE.events.json --out logs/DATE.meetings.json
   ```
   Print its console table as-is. It applies the rules: include events with at least one
   attendee outside marketplaceofficer.com, or a Zoom/Meet/Teams link whose organizer is
   external; exclude all-day events, cancelled events, anything William declined, internal
   meetings, and personal blocks. Duplicate bookings (same company, same people) are marked
   `duplicate_of` — research those once and show both times in the brief.
5. If the calendar call fails: write `logs/DATE.meetings.json` as
   `{"date": DATE, "external": [], "internal": [], "excluded": [], "error": "<message>"}`,
   note the failure, and continue. The brief will say the calendar was `[unavailable]`.
6. If `external` is empty, say so on the console ("No external meetings today") and continue —
   the email still goes out with the internal schedule on one line.

If `run.json` says `step: "1"`, stop here after printing the table.
