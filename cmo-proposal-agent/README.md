# CMO Proposal Agent - Setup

A Claude Code project that watches your calendar for proposal calls, pulls the
prospect from HubSpot, checks Granola for what you quoted, and hands you a
branded Word doc and PDF.

## 1. Put the folder somewhere permanent

```bash
mv cmo-proposal-agent ~/cmo-proposal-agent
cd ~/cmo-proposal-agent
```

## 2. Install the two system dependencies

PDF conversion needs LibreOffice. The page check needs poppler.

macOS:

```bash
brew install --cask libreoffice
brew install poppler
```

Confirm `soffice` is on your PATH:

```bash
soffice --version
```

If it is not, add it:

```bash
echo 'export PATH="/Applications/LibreOffice.app/Contents/MacOS:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

The document builder is Python and needs one package:

```bash
pip3 install -r requirements.txt
```

## 3. Connect the MCP servers

`.mcp.json` declares HubSpot, Google Calendar and Granola. Start Claude Code in
the folder and approve them when prompted:

```bash
cd ~/cmo-proposal-agent
claude
```

Then check they authenticated:

```
/mcp
```

Each will walk you through OAuth in the browser the first time. If one does not
appear, add it directly:

```bash
claude mcp add --transport http hubspot https://mcp.hubspot.com/anthropic
claude mcp add --transport http granola https://mcp.granola.ai/mcp
```

Google Calendar is the one exception: Anthropic's hosted Calendar connector only
does OAuth from claude.ai, not from Claude Code, so `.mcp.json` runs the
`@cocal/google-calendar-mcp` server locally instead. It needs a Google Cloud OAuth
client (Desktop app type, Calendar API enabled). Download the client JSON and put it
at `~/.config/cmo-proposal-agent/gcp-oauth.keys.json`, or point
`GOOGLE_OAUTH_CREDENTIALS` at wherever you keep it. The first calendar call opens
the browser to authorize.

## 4. Run it

```
/proposals
```

Scans the next 5 days for events with "Proposal" in the title, builds one
proposal per new prospect, drops the .docx and .pdf in `output/`.

For a single company, or to re-quote one:

```
/proposals Brow Down Studio
```

## 5. Make it run every morning (optional)

Headless mode plus cron. Add to `crontab -e`:

```
0 7 * * 1-5 cd ~/cmo-proposal-agent && /usr/local/bin/claude -p "Run the proposal sweep for today using the proposal-builder subagent" >> ~/cmo-proposal-agent/output/run.log 2>&1
```

Use `which claude` to get the right path for your machine. Proposals land in
`output/` before you sit down.

Headless runs cannot ask for permission, so `.claude/settings.json` pre-approves
the three MCP servers, the render script, and writes under `output/`. Everything
else is denied in that mode. Run `/proposals` interactively once first so the MCP
OAuth tokens exist before cron tries to use them.

---

## What it will and will not do

It pulls the company name from HubSpot rather than the calendar title, so
"Brow Down" becomes "Brow Down Studio" on the document.

It defaults to $3,500/mo plus 8%, and applies a base only when one came up in
the notes.

It will not invent pricing. If Granola and HubSpot disagree, it uses the more
recent source and tells you about the conflict.

It will not email anything. You review and send.

## Adjusting the standard rate later

Two places, both plain text:

- `CLAUDE.md`, the Standard pricing section
- `.claude/agents/proposal-builder.md`, Step 4

Change the rate in both and every future proposal picks it up. To change the
default Terms in the blank template itself, edit `assets/template_spec.json`
and regenerate:

```bash
python3 scripts/cmo_proposal.py assets/template_spec.json \
  -t "assets/CMO Proposal Template.docx" \
  -o "assets/CMO Proposal Template.docx"
```
