"""Derive templates/amazon_services_agreement.docx from William's own Word file.

The source is templates/source/Elleebana_Amazon_Services_Agreement.docx, the
agreement exactly as CMO sends it. This script touches nothing but the text of
the runs that hold per-client values, so every style, font, margin, section and
column of the original survives. It does not add, restyle or reflow anything.

Three deliberate exceptions, each noted inline:
  * the pasted-in Calibri on the client name and address is dropped so those
    runs inherit the paragraph font like the words around them;
  * the last deliverable and the "Schedule C" heading share one paragraph in
    the source (a lost paragraph mark); they are split so the deliverables can
    be looped;
  * CMO's signature mark is set in italics per William, replacing a
    handwriting font that is not installed where the PDF is produced.

Re-run after editing:  python tools/build_template.py
"""

import copy
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.shared import RGBColor

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "templates" / "source" / "Elleebana_Amazon_Services_Agreement.docx"
TEMPLATE = ROOT / "templates" / "amazon_services_agreement.docx"


def _find(paragraphs, needle, start=0):
    for i in range(start, len(paragraphs)):
        if needle in paragraphs[i].text:
            return i
    raise LookupError(f"paragraph containing {needle!r} not found in source")


def _set_runs(p, texts):
    """Assign text run-by-run; unlisted trailing runs are blanked, never removed,
    so their formatting stays in the file for anyone editing it later."""
    for i, run in enumerate(p.runs):
        run.text = texts[i] if i < len(texts) else ""


def _inherit_font(run):
    rpr = run._r.rPr
    if rpr is not None and rpr.find(qn("w:rFonts")) is not None:
        rpr.remove(rpr.find(qn("w:rFonts")))


def _anchor(p, after_run, jinja_var):
    """Insert an invisible (white) SignNow anchor run right after `after_run`."""
    new = copy.deepcopy(after_run._r)
    after_run._r.addnext(new)
    from docx.text.run import Run
    r = Run(new, p)
    r.text = "{{ %s }}" % jinja_var
    r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    return r


def _control_paragraph_before(p, text):
    new = copy.deepcopy(p._p)
    for child in list(new):
        if child.tag != qn("w:pPr"):
            new.remove(child)
    ppr = new.find(qn("w:pPr"))
    if ppr is not None and ppr.find(qn("w:numPr")) is not None:
        ppr.remove(ppr.find(qn("w:numPr")))
    p._p.addprevious(new)
    from docx.text.paragraph import Paragraph
    Paragraph(new, p._parent).add_run(text)


def build():
    doc = Document(str(SOURCE))
    P = doc.paragraphs

    # Effective date line: "Effective Date: 8-28-26" -> the date runs.
    p = P[_find(P, "Effective Date:")]
    _set_runs(p, ["\n", "Effective Date:", " ", "{{ effective_date }}"])

    # Intro sentence: client entity, state, form, address.
    p = P[_find(P, "is made between Chief Marketplace Officer")]
    _set_runs(p, [
        p.runs[0].text,
        "{{ company_legal_name }} {{ entity_article }} {{ state_of_incorporation }} "
        "{{ entity_type }} with address of {{ company_address }} ",
    ] + [""] * 13 + [p.runs[15].text])
    _inherit_font(p.runs[1])

    # 1.2 Payment.
    p = P[_find(P, "Company will pay CMO a monthly")]
    _set_runs(p, [
        "Company will pay CMO a monthly service fee of {{ monthly_fee }} plus a "
        "commission equal to {{ commission_pct }} of monthly Gross Amazon Sales. "
        "{{ commission_terms }} Payments can be made via Zelle, Venmo, Check, PayPal, or ACH.",
    ])

    # 3.1 Term.
    p = P[_find(P, "continues for an initial term of")]
    _set_runs(p, [p.runs[0].text, "{{ initial_term_months }}", " ", "months", p.runs[4].text, "  "])

    # Signature block, client side.
    i = _find(P, "9. Execution and Signature")
    p = P[_find(P, "DBA Elleebana", i)]
    _set_runs(p, ["{{ company_legal_name }}"])
    _inherit_font(p.runs[0])

    p = P[_find(P, "Gilbert Valverde", i)]
    r = p.runs
    # Split "Signature: ____" so the anchor sits after the label, not at the end of
    # the underscores where the narrow signature column would wrap it in two.
    _set_runs(p, ["Signature: ", r[1].text, r[2].text, r[3].text, "{{ signatory_name }}", r[5].text,
                  r[6].text, r[7].text, r[8].text, "{{ signatory_title }}", r[10].text, r[11].text,
                  r[12].text, r[13].text, r[14].text, "", "", "", r[18].text])
    sig = _anchor(p, p.runs[0], "sn_client_signature")          # after "Signature: "
    tail = _anchor(p, sig, "sn_client_signature_tail")            # the underscores
    tail.text = "________________________"; tail.font.color.rgb = None
    _anchor(p, p.runs[3 + 2], "sn_client_printed_name")          # after "Name: __ __"
    _anchor(p, p.runs[14 + 3], "sn_client_date")                 # after "Date: ___"

    # Signature block, CMO side: italic mark and the date, both from config.
    p = P[_find(P, "Name: William Fikhman", i)]
    r = p.runs
    _set_runs(p, [r[0].text, r[1].text, "{{ cmo_signature_mark }}", "", r[4].text, r[5].text,
                  r[6].text, r[7].text, r[8].text, "{{ cmo_signature_date }}", "", "", r[12].text])
    _inherit_font(p.runs[2])
    p.runs[2].italic = True

    # Schedule B deliverables: loop over the first numbered item, drop the rest.
    first = _find(P, "Manage USA Seller Central Account")
    last = _find(P, "Bi-Weekly status meeting")
    item = P[first]
    _set_runs(item, ["{{ item }}"])
    _control_paragraph_before(item, "{%p for item in schedule_b_deliverables %}")
    for j in range(first + 1, last):
        P[j]._p.getparent().remove(P[j]._p)
    merged = P[last]
    # Split "…for both sides" + "Schedule C – Intellectual Property": the heading
    # becomes its own centred, un-numbered paragraph; the item paragraph goes.
    heading = copy.deepcopy(merged._p)
    for child in list(heading):
        if child.tag == qn("w:r"):
            heading.remove(child)
    ppr = heading.find(qn("w:pPr"))
    if ppr is not None and ppr.find(qn("w:numPr")) is not None:
        ppr.remove(ppr.find(qn("w:numPr")))
    heading.append(copy.deepcopy(merged.runs[2]._r))
    merged._p.addnext(heading)
    endfor = copy.deepcopy(item._p)
    for child in list(endfor):
        if child.tag != qn("w:pPr"):
            endfor.remove(child)
    eppr = endfor.find(qn("w:pPr"))
    if eppr is not None and eppr.find(qn("w:numPr")) is not None:
        eppr.remove(eppr.find(qn("w:numPr")))
    merged._p.addnext(endfor)
    from docx.text.paragraph import Paragraph
    Paragraph(endfor, merged._parent).add_run("{%p endfor %}")
    merged._p.getparent().remove(merged._p)

    # Schedule C item 4: the trademark exhibit. One paragraph after the item,
    # centred like the "TBD" line on the executed agreements. At render time it
    # holds the USPTO screenshot when one was captured, otherwise "TBD".
    p = P[_find(P, "Trademarks/Word Marks")]
    j = _find(P, "to perform the Consulting Services", P.index(p))
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.text.paragraph import Paragraph
    exhibit = copy.deepcopy(P[j]._p)
    for child in list(exhibit):
        if child.tag != qn("w:pPr"):
            exhibit.remove(child)
    eppr = exhibit.find(qn("w:pPr"))
    if eppr is not None and eppr.find(qn("w:numPr")) is not None:
        eppr.remove(eppr.find(qn("w:numPr")))
    P[j]._p.addnext(exhibit)
    ep = Paragraph(exhibit, P[j]._parent)
    ep.alignment = WD_ALIGN_PARAGRAPH.CENTER
    ep.paragraph_format.left_indent = None
    ep.add_run("{{ trademark_exhibit }}")

    doc.save(str(TEMPLATE))
    return TEMPLATE


if __name__ == "__main__":
    print(f"wrote {build()}")
