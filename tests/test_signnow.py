"""The API client: auth, transport, guardrails and audit hygiene."""

import json

import pytest

from conftest import FakeResponse
from signnow import (
    AuditLog,
    Credentials,
    Role,
    SignNowAPIError,
    SignNowAuthError,
    SignNowClient,
    SignNowConfigError,
    build_invite,
    find_role,
)

TOKEN_OK = FakeResponse(200, {"access_token": "tok-1", "refresh_token": "ref-1", "expires_in": 3600})


def make_client(credentials, session, audit, dry_run=False):
    return SignNowClient(credentials, audit=audit, dry_run=dry_run, session=session)


# ------------------------------------------------------------------ credentials


def test_missing_credentials_are_named(monkeypatch):
    with pytest.raises(SignNowConfigError, match="SIGNNOW_CLIENT_ID"):
        Credentials.from_env({})


def test_sandbox_is_detected_from_the_host():
    assert Credentials(base_url="https://api-eval.signnow.com", access_token="k").is_sandbox
    assert not Credentials(base_url="https://api.signnow.com", access_token="k").is_sandbox


def test_secrets_are_not_in_the_repr(credentials):
    text = repr(credentials)
    assert "csecret" not in text
    assert "hunter2" not in text


# ------------------------------------------------------------------------- auth


def test_token_is_fetched_once_and_cached(credentials, session, audit):
    session.queue("POST", "/oauth2/token", TOKEN_OK)
    client = make_client(credentials, session, audit)
    assert client.token() == "tok-1"
    assert client.token() == "tok-1"
    assert len(session.calls) == 1


def test_token_uses_basic_auth_and_password_grant(credentials, session, audit):
    session.queue("POST", "/oauth2/token", TOKEN_OK)
    make_client(credentials, session, audit).token()
    call = session.calls[0]
    assert call["auth"] == ("cid", "csecret")
    assert call["data"]["grant_type"] == "password"
    assert call["data"]["scope"] == "*"


def test_bad_credentials_raise_auth_error(credentials, session, audit):
    session.queue("POST", "/oauth2/token", FakeResponse(401, {"error": "nope"}))
    with pytest.raises(SignNowAuthError, match="access token"):
        make_client(credentials, session, audit).token()


def test_a_401_refreshes_the_token_and_retries_once(credentials, session, audit):
    session.queue("POST", "/oauth2/token", TOKEN_OK)
    session.queue("GET", "/document/doc-1", FakeResponse(401, {"error": "expired"}))
    session.queue("POST", "/oauth2/token",
                  FakeResponse(200, {"access_token": "tok-2", "expires_in": 3600}))
    session.queue("GET", "/document/doc-1", FakeResponse(200, {"id": "doc-1"}))

    client = make_client(credentials, session, audit)
    assert client.get_document("doc-1") == {"id": "doc-1"}

    retried = [c for c in session.calls if c["method"] == "GET"][-1]
    assert retried["headers"]["Authorization"] == "Bearer tok-2"


def test_an_error_response_raises_with_the_status(credentials, session, audit):
    session.queue("POST", "/oauth2/token", TOKEN_OK)
    session.queue("GET", "/document/doc-1", FakeResponse(422, None, text="bad tags"))
    with pytest.raises(SignNowAPIError) as excinfo:
        make_client(credentials, session, audit).get_document("doc-1")
    assert excinfo.value.status == 422
    assert "bad tags" in str(excinfo.value)


# -------------------------------------------------------------------- dry run


def test_dry_run_blocks_the_invite(credentials, session, audit):
    """The one call that emails a client must not go out on a dry run."""
    client = make_client(credentials, session, audit, dry_run=True)
    result = client.send_invite("doc-1", {"to": [{"email": "a@b.com"}]})
    assert result["dry_run"] is True
    assert session.calls == []


def test_dry_run_blocks_the_upload(credentials, session, audit, tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    client = make_client(credentials, session, audit, dry_run=True)
    assert client.upload_with_tags(pdf, [{"tag_name": "X"}]) == "dry-run-document-id"
    assert session.calls == []


def test_dry_run_still_allows_reads(credentials, session, audit):
    """A dry run should tell you something true about the account."""
    session.queue("POST", "/oauth2/token", TOKEN_OK)
    session.queue("GET", "/document/doc-1", FakeResponse(200, {"id": "doc-1"}))
    client = make_client(credentials, session, audit, dry_run=True)
    assert client.get_document("doc-1") == {"id": "doc-1"}


# --------------------------------------------------------------------- upload


def test_upload_sends_tags_as_json_in_the_documented_part(credentials, session, audit, tmp_path):
    pdf = tmp_path / "agreement.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    session.queue("POST", "/oauth2/token", TOKEN_OK)
    session.queue("POST", "/document/fieldextract", FakeResponse(200, {"id": "doc-9"}))

    client = make_client(credentials, session, audit)
    tags = [{"type": "signature", "tag_name": "ClientSignature"}]
    assert client.upload_with_tags(pdf, tags, document_name="Acme.pdf") == "doc-9"

    call = session.calls[-1]
    assert json.loads(call["data"]["Tags"]) == tags
    assert call["data"]["parse_type"] == "default"
    assert call["files"]["file"][0] == "Acme.pdf"


def test_upload_without_an_id_is_an_error(credentials, session, audit, tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    session.queue("POST", "/oauth2/token", TOKEN_OK)
    session.queue("POST", "/document/fieldextract", FakeResponse(200, {}))
    with pytest.raises(Exception, match="no document id"):
        make_client(credentials, session, audit).upload_with_tags(pdf, [])


def test_missing_pdf_is_caught_before_any_request(credentials, session, audit, tmp_path):
    with pytest.raises(Exception, match="PDF not found"):
        make_client(credentials, session, audit).upload_with_tags(tmp_path / "nope.pdf", [])
    assert session.calls == []


# ---------------------------------------------------------------- roles/fields


DOC_BODY = {
    "id": "doc-1",
    "roles": [{"unique_id": "role-abc", "name": "Client", "signing_order": "1"}],
    "fields": [
        {"id": "f1", "type": "signature", "role": "Client", "role_id": "role-abc",
         "json_attributes": {"page_number": 4, "x": 320.0, "y": 610.0}},
    ],
    "field_invites": [
        {"email": "jordan@example.com", "role": "Client", "status": "pending",
         "updated": "1767571200"},
    ],
}


def test_roles_are_parsed(credentials, session, audit):
    session.queue("POST", "/oauth2/token", TOKEN_OK)
    session.queue("GET", "/document/doc-1", FakeResponse(200, DOC_BODY))
    roles = make_client(credentials, session, audit).roles("doc-1")
    assert roles == [Role(unique_id="role-abc", name="Client", signing_order="1")]


def test_fields_report_where_they_landed(credentials, session, audit):
    session.queue("POST", "/oauth2/token", TOKEN_OK)
    session.queue("GET", "/document/doc-1", FakeResponse(200, DOC_BODY))
    field = make_client(credentials, session, audit).fields("doc-1")[0]
    assert field.page_number == 4
    assert "page 4" in field.describe()


def test_invite_status_is_parsed(credentials, session, audit):
    session.queue("POST", "/oauth2/token", TOKEN_OK)
    session.queue("GET", "/document/doc-1", FakeResponse(200, DOC_BODY))
    states = make_client(credentials, session, audit).invite_status("doc-1")
    assert states[0].status == "pending"


def test_find_role_is_case_insensitive():
    roles = [Role("role-abc", "Client", "1")]
    assert find_role(roles, "client").unique_id == "role-abc"


def test_missing_role_explains_the_likely_cause():
    with pytest.raises(Exception, match="text tags were not extracted"):
        find_role([Role("r", "Signer 1", "1")], "Client")


# --------------------------------------------------------------------- invite


def test_build_invite_matches_the_documented_shape():
    invite = build_invite(
        document_id="doc-1",
        role_id="role-abc",
        role_name="Client",
        signer_email="jordan@example.com",
        sender_email="william@marketplaceofficer.com",
        subject="Sign this",
        message="Please sign.",
    )
    assert invite["document_id"] == "doc-1"
    assert invite["from"] == "william@marketplaceofficer.com"
    assert invite["to"] == [{
        "email": "jordan@example.com",
        "role_id": "role-abc",
        "role": "Client",
        "order": 1,
        "subject": "Sign this",
        "message": "Please sign.",
    }]


def test_build_invite_touches_no_network(session):
    build_invite(
        document_id="d", role_id="r", role_name="Client", signer_email="a@b.com",
        sender_email="c@d.com", subject="s", message="m",
    )
    assert session.calls == []


# ------------------------------------------------------------------ audit log


def test_audit_records_one_json_line_per_call(tmp_path, credentials, session):
    audit = AuditLog(tmp_path / "audit.log")
    session.queue("POST", "/oauth2/token", TOKEN_OK)
    session.queue("GET", "/document/doc-1", FakeResponse(200, DOC_BODY))
    make_client(credentials, session, audit).get_document("doc-1")

    lines = [json.loads(l) for l in audit.path.read_text().splitlines()]
    assert [l["action"] for l in lines] == ["oauth2.token", "document.get"]
    assert lines[1]["document_id"] == "doc-1"
    assert lines[1]["status"] == 200
    assert all(l["timestamp"] for l in lines)


def test_audit_redacts_secrets(tmp_path):
    audit = AuditLog(tmp_path / "audit.log")
    audit.record("t", detail={"password": "hunter2", "nested": {"client_secret": "s3cr3t"}})
    text = audit.path.read_text()
    assert "hunter2" not in text
    assert "s3cr3t" not in text
    assert "<redacted>" in text


def test_audit_never_records_the_account_password(tmp_path, credentials, session):
    audit = AuditLog(tmp_path / "audit.log")
    session.queue("POST", "/oauth2/token", TOKEN_OK)
    make_client(credentials, session, audit).token()
    assert "hunter2" not in audit.path.read_text()


# ------------------------------------------------------------- API key mode


def api_key_credentials():
    return Credentials(base_url="https://api-eval.signnow.com", access_token="key-123")


def test_an_api_key_alone_is_enough():
    creds = Credentials.from_env({"SIGNNOW_ACCESS_TOKEN": "key-123"})
    assert creds.uses_api_key
    assert creds.username is None


def test_api_key_wins_over_password_fields():
    creds = Credentials.from_env({
        "SIGNNOW_ACCESS_TOKEN": "key-123", "SIGNNOW_CLIENT_ID": "i",
        "SIGNNOW_CLIENT_SECRET": "s", "SIGNNOW_USERNAME": "u", "SIGNNOW_PASSWORD": "p",
    })
    assert creds.uses_api_key


def test_missing_everything_explains_both_options():
    with pytest.raises(SignNowConfigError, match="SIGNNOW_ACCESS_TOKEN"):
        Credentials.from_env({})


def test_api_key_goes_straight_into_the_bearer_header(session, audit):
    """No token exchange: the key is the bearer token, as in the SDK's apiKey mode."""
    session.queue("GET", "/document/doc-1", FakeResponse(200, {"id": "doc-1"}))
    make_client(api_key_credentials(), session, audit).get_document("doc-1")
    assert len(session.calls) == 1
    assert session.calls[0]["headers"]["Authorization"] == "Bearer key-123"


def test_a_rejected_api_key_is_not_retried(session, audit):
    """There is nothing to refresh to, so a 401 must surface, not loop."""
    session.queue("GET", "/document/doc-1", FakeResponse(401, {"error": "bad"}))
    with pytest.raises(SignNowAPIError) as excinfo:
        make_client(api_key_credentials(), session, audit).get_document("doc-1")
    assert excinfo.value.status == 401
    assert len(session.calls) == 1


def test_verify_token_uses_the_documented_endpoint(session, audit):
    session.queue("GET", "/oauth2/token", FakeResponse(200, {"scope": "*"}))
    make_client(api_key_credentials(), session, audit).verify_token()
    assert session.calls[0]["url"].endswith("/oauth2/token")
    assert session.calls[0]["method"] == "GET"


def test_verify_token_names_the_rejected_credential(session, audit):
    session.queue("GET", "/oauth2/token", FakeResponse(401, {"error": "bad"}))
    with pytest.raises(SignNowAuthError, match="SIGNNOW_ACCESS_TOKEN"):
        make_client(api_key_credentials(), session, audit).verify_token()


def test_api_key_is_redacted_from_the_repr():
    assert "key-123" not in repr(api_key_credentials())
