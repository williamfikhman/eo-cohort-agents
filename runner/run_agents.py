#!/usr/bin/env python3
"""Wake every agent in agents/, run its RAFT, email the founder the output.

One bad agent never kills the batch: a file that fails to parse is logged and
skipped, and a file that fails at the API or the mailer is logged and skipped.
The process exits non-zero only if ZERO agents succeeded.
"""

from __future__ import annotations

import html
import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

import anthropic
import markdown as markdown_lib
import requests
import yaml

AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1500
# Dynamic-filtering web search. Supported on Sonnet 4.6; do NOT also declare
# code_execution — this tool version runs it under the hood.
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search"}

SYSTEM_PROMPT = (
    "You are the agent described below, on your first scheduled shift. "
    "Execute the TASK now, honoring ROLE, AUDIENCE, and FORMAT exactly. "
    "Real research, real links, no placeholders. "
    "If the TASK says to stay quiet when nothing happened, honor that in one line."
)

EMAIL_FOOTER = (
    "— your agent, via the EO cohort runner. It runs again if Roman keeps the "
    "lights on. Reply to this email with feedback and Roman will tune it."
)

REQUIRED_FIELDS = ("name", "email", "agent", "schedule")


@dataclass
class Agent:
    path: Path
    name: str
    email: str
    agent: str
    schedule: str
    raft: str


class AgentParseError(Exception):
    """The file is not a usable agent definition."""


def parse_agent(path: Path) -> Agent:
    """Parse frontmatter + RAFT body. Raises AgentParseError on anything unusable."""
    text = path.read_text(encoding="utf-8")

    if not text.startswith("---"):
        raise AgentParseError("file does not start with a '---' frontmatter fence")

    # Split on the closing fence of the frontmatter block.
    parts = text.split("\n---", 1)
    if len(parts) != 2:
        raise AgentParseError("frontmatter block is never closed with '---'")

    raw_front = parts[0][3:]  # drop the opening '---'
    body = parts[1].lstrip("-").strip()

    try:
        front = yaml.safe_load(raw_front)
    except yaml.YAMLError as exc:
        raise AgentParseError(f"frontmatter is not valid YAML: {exc}") from exc

    if not isinstance(front, dict):
        raise AgentParseError("frontmatter did not parse to a key/value mapping")

    missing = [f for f in REQUIRED_FIELDS if not str(front.get(f) or "").strip()]
    if missing:
        raise AgentParseError(f"missing required frontmatter field(s): {', '.join(missing)}")

    email = str(front["email"]).strip()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise AgentParseError(f"'{email}' is not a usable email address")

    if not body:
        raise AgentParseError("no RAFT body below the frontmatter")

    return Agent(
        path=path,
        name=str(front["name"]).strip(),
        email=email,
        agent=str(front["agent"]).strip(),
        schedule=str(front["schedule"]).strip(),
        raft=body,
    )


def run_agent(client: anthropic.Anthropic, agent: Agent) -> str:
    """Hand the RAFT to Claude with web search on. Returns the agent's output text."""
    messages = [{"role": "user", "content": agent.raft}]

    # Server-side tools run a sampling loop that can stop with pause_turn;
    # re-send to resume. Bounded so a pathological run can't spin.
    for _ in range(6):
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=[WEB_SEARCH_TOOL],
            messages=messages,
        )

        if response.stop_reason == "refusal":
            raise RuntimeError("model declined the request (stop_reason: refusal)")

        if response.stop_reason == "pause_turn":
            messages = [
                {"role": "user", "content": agent.raft},
                {"role": "assistant", "content": response.content},
            ]
            continue

        text = "\n".join(b.text for b in response.content if b.type == "text").strip()
        if not text:
            raise RuntimeError(f"model returned no text (stop_reason: {response.stop_reason})")
        return text

    raise RuntimeError("web search never settled after 6 continuations")


def send_email(agent: Agent, output: str) -> None:
    api_key = os.environ["RESEND_API_KEY"]
    sender = os.environ.get(
        "RESEND_FROM", "EO Cohort Agents <agents@wetutorathome.com>"
    )

    text_body = f"{output}\n\n---\n{EMAIL_FOOTER}\n"
    html_body = (
        '<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
        'font-size:15px;line-height:1.55;color:#1a1a1a;max-width:640px">'
        + markdown_lib.markdown(output, extensions=["extra", "sane_lists", "nl2br"])
        + '<hr style="border:none;border-top:1px solid #e3e3e3;margin:28px 0 14px">'
        + f'<p style="font-size:13px;color:#666">{html.escape(EMAIL_FOOTER)}</p>'
        + "</div>"
    )

    resp = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": sender,
            "to": [agent.email],
            "subject": f"⚡ First shift report: {agent.agent}",
            "text": text_body,
            "html": html_body,
        },
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Resend {resp.status_code}: {resp.text[:300]}")


def main() -> int:
    for var in ("ANTHROPIC_API_KEY", "RESEND_API_KEY"):
        if not os.environ.get(var):
            print(f"FATAL: {var} is not set", file=sys.stderr)
            return 1

    files = sorted(AGENTS_DIR.glob("*.md"))
    if not files:
        print(f"FATAL: no agent files found in {AGENTS_DIR}", file=sys.stderr)
        return 1

    client = anthropic.Anthropic()
    results: list[tuple[str, str, str]] = []  # (status, filename, detail)

    for path in files:
        print(f"\n=== {path.name} ===", flush=True)
        try:
            agent = parse_agent(path)
        except AgentParseError as exc:
            print(f"SKIP  {path.name}: {exc}", flush=True)
            results.append(("skipped", path.name, str(exc)))
            continue

        print(f"agent={agent.agent} owner={agent.name} <{agent.email}>", flush=True)
        try:
            output = run_agent(client, agent)
            print(f"      got {len(output)} chars back", flush=True)
            send_email(agent, output)
            print(f"OK    emailed {agent.email}", flush=True)
            results.append(("ok", path.name, agent.email))
        except Exception as exc:  # one agent's bad day is its own
            print(f"FAIL  {path.name}: {exc}", flush=True)
            traceback.print_exc()
            results.append(("failed", path.name, str(exc)))

    ok = [r for r in results if r[0] == "ok"]
    skipped = [r for r in results if r[0] == "skipped"]
    failed = [r for r in results if r[0] == "failed"]

    print("\n" + "=" * 60)
    print("SHIFT SUMMARY")
    print("=" * 60)
    for status, filename, detail in results:
        print(f"  {status.upper():<8} {filename:<40} {detail}")
    print("-" * 60)
    print(f"  {len(ok)} ok / {len(skipped)} skipped / {len(failed)} failed "
          f"({len(results)} agent files)")
    print("=" * 60)

    if not ok:
        print("\nFATAL: zero agents succeeded.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
