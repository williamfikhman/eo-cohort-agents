"""agreement -- prepare, review and send a CMO Amazon Services Agreement.

The approval gate is the point of this tool. ``prepare`` does everything up to and
including uploading the document and placing the fields, then stops and prints a
link. ``send`` is the only command that emails the client, and it will not run
without the client's legal name typed back.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import ConfigError, load_client, load_cmo, list_clients  # noqa: E402
from fields import CLIENT_ROLE, tags_payload  # noqa: E402
from render import RenderError, render  # noqa: E402
from signnow import (  # noqa: E402
    AuditLog,
    SignNowClient,
    SignNowError,
    build_invite,
    find_role,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = REPO_ROOT / "build"

load_dotenv(REPO_ROOT / ".env")


def _state_path(slug: str) -> Path:
    return BUILD_DIR / slug / "prepared.json"


def _load_state(slug: str) -> dict[str, Any]:
    path = _state_path(slug)
    if not path.is_file():
        raise click.ClickException(
            f"{slug} has not been prepared yet. Run:\n\n    agreement prepare {slug}"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(slug: str, state: dict[str, Any]) -> Path:
    path = _state_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _fail(message: str) -> None:
    raise click.ClickException(message)


@click.group()
@click.version_option("1.0.0", prog_name="agreement")
def cli() -> None:
    """Prepare and send CMO Amazon Services Agreements through SignNow."""


@cli.command("clients")
def clients_cmd() -> None:
    """List the client configs available."""
    found = list_clients()
    if not found:
        click.echo("No client configs in clients/.")
        return
    for slug in found:
        click.echo(slug)


@cli.command()
@click.argument("slug")
@click.option("--dry-run", is_flag=True, help="Do everything except write to SignNow.")
@click.option(
    "--allow-scaffold",
    is_flag=True,
    help="Build even though the template still has PENDING clause bodies.",
)
@click.option("--sandbox", is_flag=True, help="Force the sandbox host for this run.")
def prepare(slug: str, dry_run: bool, allow_scaffold: bool, sandbox: bool) -> None:
    """Render, export, upload, place fields, build the invite -- then stop."""
    try:
        client = load_client(slug)
        cmo = load_cmo()
    except ConfigError as exc:
        _fail(str(exc))

    click.echo(f"Client   : {client.company_legal_name}")
    click.echo(f"Signer   : {client['signatory_name']} <{client.signatory_email}>")

    try:
        result = render(client, cmo, allow_scaffold=allow_scaffold)
    except RenderError as exc:
        _fail(str(exc))

    click.echo(f"Rendered : {result.docx_path.name}")
    click.echo(f"PDF      : {result.pdf_path.name} ({result.page_count} pages)")
    click.echo(f"Tags     : {', '.join(result.tags_found)} (all present in the PDF)")
    if result.trademark_screenshot:
        click.echo("TM       : USPTO screenshot placed under Schedule C item 4")
    else:
        click.secho(
            f"TM       : no USPTO screenshot -- Schedule C reads '{client['trademark_exhibit']}'. "
            f"Run: python tools/uspto_trademark.py {slug}", fg="yellow")

    environ = None
    if sandbox:
        import os

        environ = dict(os.environ)
        environ["SIGNNOW_BASE_URL"] = "https://api-eval.signnow.com"

    try:
        signnow = SignNowClient.from_env(dry_run=dry_run, environ=environ)
    except SignNowError as exc:
        _fail(str(exc))

    host = signnow.credentials.base_url
    mode = "API key" if signnow.credentials.uses_api_key else "password grant"
    click.echo(f"SignNow  : {host}{'  [SANDBOX]' if signnow.credentials.is_sandbox else ''}  ({mode})")
    if dry_run:
        click.secho("DRY RUN  : nothing will be written to SignNow.", fg="yellow")

    try:
        signnow.verify_token()
    except SignNowError as exc:
        _fail(str(exc))
    click.echo("Auth     : credentials accepted")

    prefill = {"ClientPrintedName": client["signatory_name"]}
    tags = tags_payload(prefill=prefill)

    try:
        document_id = signnow.upload_with_tags(
            result.pdf_path,
            tags,
            document_name=f"{client.company_legal_name} — Amazon Services Agreement",
        )
    except SignNowError as exc:
        _fail(str(exc))

    click.echo(f"Uploaded : document_id={document_id}")

    role_id = "dry-run-role-id"
    placed: list[str] = []
    if not dry_run:
        try:
            document_fields = signnow.fields(document_id)
            role = find_role(signnow.roles(document_id), CLIENT_ROLE)
            role_id = role.unique_id
        except SignNowError as exc:
            _fail(str(exc))

        if len(document_fields) < len(tags):
            _fail(
                f"SignNow created {len(document_fields)} field(s) but {len(tags)} tags "
                "were sent. The text tags were not all extracted, so this document is "
                "not safe to send. See docs/signnow-api-notes.md."
            )
        placed = [f.describe() for f in document_fields]
        click.echo(f"Fields   : {len(document_fields)} placed, role '{role.name}'")
        for line in placed:
            click.echo(f"           {line}")
    else:
        click.echo(f"Fields   : {len(tags)} tags would be sent (dry run, none placed)")

    subject = f"{cmo['legal_name']} — Amazon Services Agreement for signature"
    message = (
        f"Hi {client['signatory_name']},\n\n"
        f"Please review and sign the Amazon Services Agreement between "
        f"{cmo['legal_name']} and {client.company_legal_name}.\n\n"
        f"Thank you,\n{cmo['signatory_name']}\n{cmo['signatory_title']}, {cmo['legal_name']}"
    )

    invite = build_invite(
        document_id=document_id,
        role_id=role_id,
        role_name=CLIENT_ROLE,
        signer_email=client.signatory_email,
        sender_email=cmo["sender_email"],
        subject=subject,
        message=message,
    )

    state = {
        "slug": slug,
        "company_legal_name": client.company_legal_name,
        "document_id": document_id,
        "base_url": host,
        "sandbox": signnow.credentials.is_sandbox,
        "dry_run": dry_run,
        "pdf_path": str(result.pdf_path),
        "page_count": result.page_count,
        "fields": placed,
        "invite": invite,
        "review_url": signnow.document_url(document_id),
    }
    state_path = _save_state(slug, state)

    click.echo()
    click.secho("PREPARED — nothing has been sent.", fg="green", bold=True)
    click.echo(f"Review   : {state['review_url']}")
    click.echo(f"Invite   : built and saved to {state_path.relative_to(REPO_ROOT)}")
    click.echo(f"Recipient: {client.signatory_email}")
    click.echo()
    click.echo(f"When it looks right:\n\n    agreement send {slug}")


@cli.command()
@click.argument("slug")
def preview(slug: str) -> None:
    """Re-print the review link and the field map for a prepared agreement."""
    state = _load_state(slug)

    click.echo(f"Client   : {state['company_legal_name']}")
    click.echo(f"Document : {state['document_id']}")
    click.echo(f"Host     : {state['base_url']}{'  [SANDBOX]' if state.get('sandbox') else ''}")
    click.echo(f"PDF      : {state['pdf_path']} ({state['page_count']} pages)")
    if state.get("dry_run"):
        click.secho("This was a DRY RUN — the document was never uploaded.", fg="yellow")
    click.echo(f"Review   : {state['review_url']}")

    click.echo()
    click.echo("Fields:")
    for line in state.get("fields") or ["  (none recorded)"]:
        click.echo(f"  {line}")

    invite = state["invite"]
    click.echo()
    click.echo("Invite (not sent):")
    click.echo(f"  from    : {invite['from']}")
    for recipient in invite["to"]:
        click.echo(f"  to      : {recipient['email']}  role={recipient['role']}")
    click.echo(f"  subject : {invite['subject']}")


@cli.command()
@click.argument("slug")
@click.option("--dry-run", is_flag=True, help="Confirm, but do not actually send.")
@click.option(
    "--yes",
    "confirmation",
    default=None,
    help="Type the client's legal name to confirm non-interactively.",
)
def send(slug: str, dry_run: bool, confirmation: str | None) -> None:
    """Send the invite. Requires typing the client's legal name."""
    state = _load_state(slug)

    if state.get("dry_run"):
        _fail(
            f"{slug} was prepared with --dry-run, so no document exists in SignNow.\n"
            f"Re-run:  agreement prepare {slug}"
        )

    expected = state["company_legal_name"]
    recipient = state["invite"]["to"][0]["email"]

    click.echo(f"About to email the agreement to {recipient}")
    click.echo(f"Client   : {expected}")
    click.echo(f"Document : {state['document_id']}")
    click.echo(f"Host     : {state['base_url']}{'  [SANDBOX]' if state.get('sandbox') else ''}")
    click.echo()

    typed = confirmation
    if typed is None:
        typed = click.prompt("Type the client's legal name to confirm", default="", show_default=False)

    if typed.strip() != expected:
        _fail(
            f"confirmation did not match.\n  expected: {expected}\n  got     : {typed.strip()!r}\n"
            "Nothing was sent."
        )

    signnow = SignNowClient(
        credentials=_credentials_for(state),
        audit=AuditLog(),
        dry_run=dry_run,
    )

    if dry_run:
        click.secho("DRY RUN — confirmed, but not sending.", fg="yellow")

    try:
        response = signnow.send_invite(state["document_id"], state["invite"])
    except SignNowError as exc:
        _fail(str(exc))

    state["sent"] = not dry_run
    _save_state(slug, state)

    if dry_run:
        click.secho("Not sent (dry run).", fg="yellow")
    else:
        click.secho(f"Sent to {recipient}.", fg="green", bold=True)
    click.echo(f"Response : {json.dumps(response)[:300]}")
    click.echo(f"Status   : agreement status {slug}")


@cli.command()
@click.argument("slug")
def status(slug: str) -> None:
    """Show the invite status for a prepared agreement."""
    state = _load_state(slug)
    if state.get("dry_run"):
        _fail(f"{slug} was prepared with --dry-run; there is nothing in SignNow to check.")

    signnow = SignNowClient(credentials=_credentials_for(state), audit=AuditLog())

    try:
        invites = signnow.invite_status(state["document_id"])
    except SignNowError as exc:
        _fail(str(exc))

    click.echo(f"Client   : {state['company_legal_name']}")
    click.echo(f"Document : {state['document_id']}")
    if not invites:
        click.echo("No invite has been sent yet.")
        return
    click.echo()
    for invite in invites:
        click.echo(f"  {invite.email:<40} {invite.role:<10} {invite.status}")


def _credentials_for(state: dict[str, Any]):
    """Credentials pinned to the host the document was prepared against."""
    import os

    from signnow import Credentials

    environ = dict(os.environ)
    environ["SIGNNOW_BASE_URL"] = state["base_url"]
    return Credentials.from_env(environ)


if __name__ == "__main__":
    cli()
