"""Tag payloads must match the shapes verified in docs/signnow-api-notes.md.

The expected JSON here is copied from assertions in airSlate's own SDK unit tests
(SignNow.NET ComplexTagsTest.cs). If SignNow changes the contract, these fail.
"""

from fields import CLIENT_FIELDS, CLIENT_ROLE, VALIDATOR_DATE_US, tag_context, tags_payload


def test_signature_tag_matches_the_documented_shape():
    signature = next(f for f in CLIENT_FIELDS if f.type == "signature")
    assert signature.payload() == {
        "type": "signature",
        "tag_name": "ClientSignature",
        "role": CLIENT_ROLE,
        "required": True,
        "width": 200,
        "height": 20,
    }


def test_date_tag_is_a_text_field_with_the_us_validator():
    """SignNow has no date type; a date is a text field plus a validator."""
    date = next(f for f in CLIENT_FIELDS if f.tag_name == "ClientSignDate")
    payload = date.payload()
    assert payload["type"] == "text"
    assert payload["validator_id"] == VALIDATOR_DATE_US
    assert payload["lock_to_sign_date"] is True


def test_no_tag_sends_coordinates():
    """Position comes from the tag's place in the document, so reflow is harmless."""
    for payload in tags_payload():
        assert "x" not in payload
        assert "y" not in payload
        assert "page_number" not in payload


def test_every_tag_is_assigned_to_the_client_role():
    assert {p["role"] for p in tags_payload()} == {CLIENT_ROLE}


def test_prefill_is_applied_only_to_the_named_tag():
    payloads = {p["tag_name"]: p for p in tags_payload({"ClientPrintedName": "Jordan Rivera"})}
    assert payloads["ClientPrintedName"]["prefilled_text"] == "Jordan Rivera"
    assert "prefilled_text" not in payloads["ClientSignature"]


def test_tag_literals_use_signnow_not_jinja_names():
    """What lands in the document is {{TagName}}, not the Jinja variable."""
    context = tag_context()
    assert context["sn_client_signature"] == "{{ClientSignature}}"
    assert set(context) == {f.jinja_var for f in CLIENT_FIELDS}


def test_tag_names_are_unique():
    names = [f.tag_name for f in CLIENT_FIELDS]
    assert len(names) == len(set(names))
