"""Generate templates/amazon_services_agreement.docx.

The repository did not contain the executed Word agreement, so this script builds
a template with the correct *structure* -- every per-client variable, all five
schedules, both signature blocks and the SignNow anchors -- while leaving the
clause bodies as explicit PENDING markers.

Replacing the scaffold is a paste job, not a rebuild: drop the real clause text in
over each marker and keep the ``{{ ... }}`` placeholders where they sit. Nothing
carrying a PENDING marker can be rendered without ``--allow-scaffold``, so the
scaffold cannot reach a client by accident.

Re-run after editing:  python tools/build_template.py
"""

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "amazon_services_agreement.docx"

PENDING = "[CLAUSE TEXT PENDING — paste the corresponding section of the executed CMO Amazon Services Agreement here.]"

# Sections of the constant contract body. Only the headings are authored here;
# the clause text is William's and gets pasted in.
BODY_SECTIONS = [
    "1. Engagement and Scope of Services",
    "2. Term and Termination",
    "3. Fees and Payment",
    "4. Client Obligations",
    "5. Intellectual Property and Trademark License",
    "6. Confidentiality",
    "7. Representations and Warranties",
    "8. Limitation of Liability",
    "9. Indemnification",
    "10. Independent Contractor",
    "11. Governing Law and Dispute Resolution",
    "12. General Provisions",
]

SCHEDULES = [
    ("Schedule A — Scope of Services", None),
    ("Schedule B — Deliverables", "deliverables"),
    ("Schedule C — Service Level Commitments", None),
    ("Schedule D — Fees and Commission Structure", "fees"),
    ("Schedule E — Trademark and Brand Assets", "trademark"),
]


def _style(doc):
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)


def _heading(doc, text, size=13):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.bold = True
    run.font.size = Pt(size)
    p.space_before = Pt(12)
    return p


def _pending(doc):
    p = doc.add_paragraph()
    run = p.add_run(PENDING)
    run.italic = True
    run.font.color.rgb = RGBColor(0x99, 0x99, 0x99)
    return p


def _anchor(paragraph, jinja_var):
    """Emit a SignNow tag via Jinja, rendered white so it is invisible on the page.

    The tag cannot be written literally: docxtpl would consume ``{{...}}`` as one
    of its own variables. render.py passes the literal SignNow tag in as context.
    """
    run = paragraph.add_run("{{ %s }}" % jinja_var)
    run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    return run


def build():
    doc = Document()
    _style(doc)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("AMAZON SERVICES AGREEMENT")
    run.bold = True
    run.font.size = Pt(16)

    intro = doc.add_paragraph()
    intro.add_run(
        "This Amazon Services Agreement (the “Agreement”) is entered into as of "
    )
    intro.add_run("{{ effective_date }}").bold = True
    intro.add_run(" (the “Effective Date”) by and between ")
    intro.add_run("{{ cmo_legal_name }}").bold = True
    intro.add_run(
        ", a {{ cmo_state_of_incorporation }} {{ cmo_entity_type }} with its principal place of "
        "business at {{ cmo_address }} (“CMO”), and "
    )
    intro.add_run("{{ company_legal_name }}").bold = True
    intro.add_run(", a {{ state_of_incorporation }} {{ entity_type }} with its principal place of business at ")
    intro.add_run("{{ company_address }}")
    intro.add_run(" (“Client”). CMO and Client are each a “Party” and together the “Parties.”")

    recitals = doc.add_paragraph()
    recitals.add_run(
        "The Parties agree that the initial term of this Agreement is "
    )
    recitals.add_run("{{ initial_term_months }}").bold = True
    recitals.add_run(" months from the Effective Date.")

    for heading in BODY_SECTIONS:
        _heading(doc, heading)
        _pending(doc)

    # ------------------------------------------------------------------ signatures
    doc.add_page_break()
    _heading(doc, "SIGNATURES", size=14)
    doc.add_paragraph(
        "IN WITNESS WHEREOF, the Parties have executed this Agreement as of the Effective Date."
    )

    table = doc.add_table(rows=1, cols=2)
    table.autofit = True
    left, right = table.rows[0].cells

    # CMO block -- pre-filled, no signature fields, nothing for the client to touch.
    left.paragraphs[0].add_run("CHIEF MARKETPLACE OFFICER, INC.").bold = True
    left.add_paragraph()
    left.add_paragraph("{{ cmo_signature_mark }}")
    left.add_paragraph("_______________________________")
    left.add_paragraph("Name: {{ cmo_signatory_name }}")
    left.add_paragraph("Title: {{ cmo_signatory_title }}")
    left.add_paragraph("Date: {{ cmo_signature_date }}")

    # Client block -- every field the client fills, each anchored by a SignNow tag.
    right.paragraphs[0].add_run("{{ company_legal_name }}").bold = True
    right.add_paragraph()
    sig = right.add_paragraph()
    _anchor(sig, "sn_client_signature")
    right.add_paragraph("_______________________________")
    name_p = right.add_paragraph("Name: ")
    _anchor(name_p, "sn_client_printed_name")
    right.add_paragraph("Title: {{ signatory_title }}")
    date_p = right.add_paragraph("Date: ")
    _anchor(date_p, "sn_client_date")

    # ------------------------------------------------------------------ schedules
    for heading, kind in SCHEDULES:
        doc.add_page_break()
        _heading(doc, heading, size=14)

        if kind == "deliverables":
            doc.add_paragraph("CMO will provide the following deliverables to Client:")
            # docxtpl loop -- one bullet per entry in schedule_b_deliverables.
            # {%p ...%} makes docxtpl drop the control paragraph itself, so the
            # loop does not leave an empty bullet above and below the list.
            doc.add_paragraph("{%p for item in schedule_b_deliverables %}")
            doc.add_paragraph("{{ item }}", style="List Bullet")
            doc.add_paragraph("{%p endfor %}")
        elif kind == "fees":
            p = doc.add_paragraph()
            p.add_run("Monthly retainer: ").bold = True
            p.add_run("{{ monthly_fee }}")
            p = doc.add_paragraph()
            p.add_run("Commission: ").bold = True
            p.add_run("{{ commission_pct }}")
            p = doc.add_paragraph()
            p.add_run("Commission terms: ").bold = True
            p.add_run("{{ commission_terms }}")
        elif kind == "trademark":
            p = doc.add_paragraph()
            p.add_run("Licensed marks and brand assets: ").bold = True
            p.add_run("{{ trademark_exhibit }}")
        else:
            _pending(doc)

    TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
    doc.save(TEMPLATE)
    return TEMPLATE


if __name__ == "__main__":
    path = build()
    print(f"wrote {path}")
