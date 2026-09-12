"""The signature-block fields, defined once.

render.py needs the literal tag text to drop into the Word template; signnow.py
needs the matching JSON to send with the upload. Both read this module, so a tag
can never drift out of sync with its field definition.

Shapes are taken from airSlate's own SDK -- see docs/signnow-api-notes.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The role the client signer is assigned to. Must match the role in every tag.
CLIENT_ROLE = "Client"

#: US date validator, from SignNow.Net/Model/DataValidator.cs.
VALIDATOR_DATE_US = "13435fa6c2a17f83177fcbb5c4a9376ce85befeb"


@dataclass(frozen=True)
class TagField:
    """One SignNow complex text tag and the field it becomes."""

    #: Jinja variable in the .docx that emits this tag.
    jinja_var: str
    #: SignNow tag name; the ``{{tag_name}}`` written into the document.
    tag_name: str
    #: SignNow field type. A date is a `text` field with a validator, not its own type.
    type: str
    role: str
    required: bool
    width: int
    height: int
    label: str | None = None
    validator_id: str | None = None
    lock_to_sign_date: bool = False

    @property
    def literal(self) -> str:
        """What actually goes into the document body."""
        return "{{%s}}" % self.tag_name

    def payload(self, prefilled_text: str | None = None) -> dict[str, Any]:
        """The JSON object sent in the upload's Tags part.

        No x/y is emitted: SignNow positions a complex tag where its tag text sits
        in the document, which is what keeps fields correct when the contract
        reflows.
        """
        body: dict[str, Any] = {
            "type": self.type,
            "tag_name": self.tag_name,
            "role": self.role,
            "required": self.required,
            "width": self.width,
            "height": self.height,
        }
        if self.label:
            body["label"] = self.label
        if self.lock_to_sign_date:
            body["lock_to_sign_date"] = True
        if self.validator_id:
            body["validator_id"] = self.validator_id
        if prefilled_text:
            body["prefilled_text"] = prefilled_text
        return body


#: The client signature block. CMO's block is pre-filled text and has no fields.
CLIENT_FIELDS: tuple[TagField, ...] = (
    TagField(
        jinja_var="sn_client_signature",
        tag_name="ClientSignature",
        type="signature",
        role=CLIENT_ROLE,
        required=True,
        width=200,
        height=20,
    ),
    TagField(
        jinja_var="sn_client_printed_name",
        tag_name="ClientPrintedName",
        type="text",
        role=CLIENT_ROLE,
        required=True,
        width=200,
        height=14,
        label="Printed name",
    ),
    TagField(
        jinja_var="sn_client_date",
        tag_name="ClientSignDate",
        type="text",
        role=CLIENT_ROLE,
        required=True,
        width=120,
        height=14,
        label="Date signed",
        validator_id=VALIDATOR_DATE_US,
        lock_to_sign_date=True,
    ),
)


def tag_context() -> dict[str, str]:
    """Jinja context that renders each tag literal into the document."""
    return {f.jinja_var: f.literal for f in CLIENT_FIELDS}


def tags_payload(prefill: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """The full Tags array for the upload, in document order."""
    prefill = prefill or {}
    return [f.payload(prefilled_text=prefill.get(f.tag_name)) for f in CLIENT_FIELDS]
