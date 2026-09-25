"""Generate templates/amazon_services_agreement.docx from the executed agreement.

The body text is transcribed from the revised Aminomega agreement (the current
form: Gross Amazon Sales definition, 14-day commission terms, competitor notice
in section 4). The three executed agreements were diffed against each other to
confirm that everything outside the per-client variables is constant.

Re-running overwrites the template. Edit the text here, not in Word, so the
template stays reproducible.
"""

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "amazon_services_agreement.docx"


# ------------------------------------------------------------------ helpers


# Measured from the executed Aminomega agreement: Liberation/Times 12pt body,
# 12pt bold headings, 14pt bold centred title, 1" margins, single spacing, 6pt
# between paragraphs, 4pt between list items, list levels stepping 0.25".
FONT = "Times New Roman"


def _style(doc):
    normal = doc.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(12)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.line_spacing = 1.0
    rpr = normal.element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts"); rpr.append(fonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(attr), FONT)


def h1(doc, text, centered=False):
    p = doc.add_paragraph()
    p.add_run(text).bold = True
    p.paragraph_format.space_before = Pt(6)
    if centered:
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    return p


def h2(doc, text):
    p = doc.add_paragraph()
    p.add_run(text).bold = True
    p.paragraph_format.space_before = Pt(6)
    return p


def para(doc, text, centered=False):
    p = doc.add_paragraph(text)
    if centered:
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    return p


def bullet(doc, text):
    return indented(doc, text, level=1, marker="●")


def indented(doc, text, level=1, marker=None):
    """A hanging-indent list line, typed rather than auto-numbered so the
    glyphs and positions match the executed agreement exactly:
    level 1 glyph at 0.25" and text at 0.5"; each level steps a further 0.5"."""
    p = doc.add_paragraph()
    if marker:
        p.add_run(f"{marker} ")
    p.add_run(text)
    p.paragraph_format.left_indent = Inches(0.5 * level)
    p.paragraph_format.first_line_indent = Inches(-0.25)
    p.paragraph_format.space_after = Pt(4)
    return p


def anchor(paragraph, jinja_var):
    """Emit a SignNow tag through Jinja, in white so it is invisible on the page.

    Written literally, ``{{ClientSignature}}`` would be consumed by docxtpl as
    one of its own variables. render.py passes the literal tag in as context.
    """
    run = paragraph.add_run("{{ %s }}" % jinja_var)
    run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    return run


# ------------------------------------------------------------------ document


def build():
    doc = Document()
    _style(doc)
    for section in doc.sections:
        section.left_margin = section.right_margin = Inches(1)
        section.top_margin = section.bottom_margin = Inches(1)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = title.add_run("AMAZON SERVICES AGREEMENT")
    r.bold = True
    r.font.size = Pt(14)

    para(doc, "Effective Date: {{ effective_date }}", centered=True)

    para(
        doc,
        "This Amazon Consulting Agreement (the “Agreement”) is made between Chief "
        "Marketplace Officer, Inc. (“CMO”), located at {{ cmo_address }}, and "
        "{{ company_legal_name }}, {{ entity_article }} {{ state_of_incorporation }} "
        "{{ entity_type_long }}, with address of {{ company_address }} (“Company”). "
        "Each may be referred to as a “Party” and collectively as the “Parties.”",
    )

    h1(doc, "RECITALS", centered=True)
    para(doc, "WHEREAS, Company operates in the business of manufacturing, marketing, and "
              "selling products through various sales platforms, and;")
    para(doc, "WHEREAS, CMO provides consulting services on branding and marketplace "
              "strategy for third-party platforms, and;")
    para(doc, "WHEREAS, Company desires to engage CMO for its expertise in navigating "
              "third-party marketplaces.")
    para(doc, "THEREFORE, in consideration of the mutual promises set forth below, the "
              "Parties agree as follows:")

    # ---- 1
    h1(doc, "1. Scope of Services and Payment Terms")
    h2(doc, "1.1 Services and Payment Terms")
    para(doc, "(a) Services Provided: Chief Marketplace Officer, Inc. (“CMO”) will perform "
              "services on the specified third-party marketplaces listed in Schedule A, as "
              "requested by the Company or as needed. These services include protecting the "
              "Company’s intellectual property rights and preventing unauthorized sales on "
              "these marketplaces and other consulting services on branding and marketplace "
              "strategy for Amazon.com.")
    para(doc, "(b) Company’s Responsibilities:")
    bullet(doc, "The Company will provide a list of its trademarks, logos, and copyrighted "
                "materials (Schedule C).")
    bullet(doc, "It will implement a Reseller Policy (Schedule E) and notify all resellers "
                "about it. The Company must keep proof of these notifications.")
    bullet(doc, "The Company will introduce a warranty for products sold by authorized "
                "resellers on these marketplaces.")
    bullet(doc, "It will establish a Minimum Advertised Price Policy.")
    bullet(doc, "The Company will maintain and update a list of authorized and unauthorized "
                "resellers (the “Reseller List”).")
    para(doc, "(c) Weekly Reports: CMO will provide the Company with a weekly list of parties "
              "infringing on the Company’s intellectual property. If the Company doesn’t "
              "respond, CMO will not take enforcement action unless later instructed to do so.")
    para(doc, "(d) Enforcement Procedures: CMO will follow enforcement procedures as requested "
              "by the Company. This includes notifying unauthorized sellers and the "
              "marketplace about infringing products. CMO will only use a Company-assigned "
              "email for these activities and comply with any brand requirements.")
    para(doc, "(e) Reimbursement: CMO can be reimbursed for reasonable pre-approved expenses "
              "related to brand protection, like test purchases, provided receipts are "
              "submitted with invoices.")

    h2(doc, "1.2 Payment")
    para(doc, "Company will pay CMO a monthly service fee of {{ monthly_fee }} plus a "
              "commission equal to {{ commission_pct }} of monthly Gross Amazon Sales. "
              "{{ commission_terms }} Payments can be made via Zelle, Venmo, Check, PayPal, "
              "or ACH.")
    para(doc, "For purposes of this Agreement, Gross Amazon Sales are defined before any "
              "fees, discounts, or other deductions of any kind, including Amazon referral "
              "fees, FBA and fulfillment fees, promotional discounts, shipping, and credits.")

    # ---- 2
    h1(doc, "2. Intellectual Property Rights")
    h2(doc, "2.1 Ownership of Intellectual Property")
    para(doc, "CMO retains ownership of its intellectual property, including all methods, "
              "techniques, and tools used during the consulting process. Company retains "
              "ownership of its own intellectual property and trademarks, granting CMO a "
              "limited, non-exclusive license solely for the purpose of fulfilling this "
              "Agreement.")
    h2(doc, "2.2 Work for Hire")
    para(doc, "All Work Product created specifically for the Company by CMO under this "
              "Agreement will be considered a “work for hire” as defined in U.S. law. CMO "
              "assigns any rights, title, and interest in such Work Product to Company, "
              "effective upon full payment.")

    # ---- 3
    h1(doc, "3. Term and Termination")
    h2(doc, "3.1 Term")
    para(doc, "This Agreement begins on the Effective Date and continues for an initial term "
              "of {{ initial_term_months }} months. The Agreement will automatically renew "
              "for subsequent one-month terms unless terminated by either Party as outlined "
              "below.")
    h2(doc, "3.2 Termination for Cause")
    para(doc, "Either Party may terminate this Agreement with immediate effect by providing "
              "written notice if the other Party materially breaches any provision of this "
              "Agreement and fails to remedy such breach within 30 business days.")
    h2(doc, "3.3 Termination for Convenience")
    para(doc, "Either Party may terminate the Agreement without cause by providing written "
              "notice at least 30 days before the end of the current term. Upon termination, "
              "all licenses and rights granted to CMO under this Agreement shall cease.")
    h2(doc, "3.4 Effect of Termination")
    para(doc, "Upon termination, both Parties must return or destroy the other Party’s "
              "confidential information. Any outstanding fees owed to CMO must be settled "
              "within 30 days of termination.")

    # ---- 4
    h1(doc, "4. Independent Contractor Relationship")
    para(doc, "This Agreement does not create any employment, agency, partnership, or joint "
              "venture relationship between the Parties. Both Parties agree that CMO is an "
              "independent contractor, free to provide similar services to other clients. If "
              "CMO engages a client that sells products directly competing with the Company’s "
              "products in the same product category, CMO will provide the Company with "
              "written notice of that engagement.")

    # ---- 5
    h1(doc, "5. Confidentiality")
    h2(doc, "5.1 Definition of Confidential Information")
    para(doc, "Each Party may disclose Confidential Information to the other, which includes "
              "proprietary data, trade secrets, business strategies, and other sensitive "
              "information.")
    h2(doc, "5.2 Obligations of Confidentiality")
    para(doc, "Each Party agrees to use the Confidential Information solely for the purposes "
              "of this Agreement. Upon termination, each Party shall return or destroy the "
              "other Party’s Confidential Information as requested.")
    h2(doc, "5.3 Exceptions")
    para(doc, "Confidential Information does not include information that is publicly known, "
              "disclosed by a third party without restriction, or independently developed by "
              "the receiving Party without use of the disclosing Party’s information.")

    # ---- 6
    h1(doc, "6. Indemnification and Limitation of Liability")
    h2(doc, "6.1 Indemnification")
    para(doc, "Both Parties agree to indemnify and hold harmless the other Party from claims "
              "arising out of any breach, negligence, or wrongful acts under this Agreement. "
              "Specific terms are detailed in Schedule D.")
    h2(doc, "6.2 Limitation of Liability")
    para(doc, "Neither Party shall be liable for indirect, incidental, or consequential "
              "damages, including loss of profits, even if advised of the possibility of such "
              "damages.")

    # ---- 7
    h1(doc, "7. Dispute Resolution and Governing Law")
    h2(doc, "7.1 Pre-Litigation Mediation")
    para(doc, "In the event of a dispute, the Parties agree to seek resolution through "
              "non-binding mediation with a mediator from JAMS in Los Angeles, California. "
              "Should mediation not resolve the dispute, either Party may pursue legal "
              "remedies as detailed below.")
    h2(doc, "7.2 Governing Law")
    para(doc, "This Agreement shall be governed by the laws of the State of California. Both "
              "Parties consent to the exclusive jurisdiction of state or federal courts in "
              "Los Angeles County for the resolution of any disputes.")

    # ---- 8
    h1(doc, "8. General Provisions")
    h2(doc, "8.1 Notices")
    para(doc, "All notices related to this Agreement shall be in writing and sent to the "
              "contact details provided by each Party via email")
    h2(doc, "8.2 Entire Agreement")
    para(doc, "This Agreement, including all schedules, represents the entire understanding "
              "between the Parties and supersedes all prior agreements, written or oral. Any "
              "modification must be in writing and signed by both Parties.")
    h2(doc, "8.3 Force Majeure")
    para(doc, "Neither Party shall be held liable for failure to perform obligations under "
              "this Agreement if caused by events beyond their control, such as natural "
              "disasters or pandemics.")
    h2(doc, "8.4 Severability")
    para(doc, "If any provision of this Agreement is found invalid, the remaining provisions "
              "will remain in full force and effect.")
    h2(doc, "8.5 Assignment")
    para(doc, "Neither Party may assign this Agreement without the prior written consent of "
              "the other Party.")

    # ---- 9 signatures
    h1(doc, "9. Execution and Signature")
    para(doc, "The Parties acknowledge that they have read and understood this Agreement, "
              "and by signing below, agree to its terms.")

    # Client block, stacked above CMO's as in the executed agreements. Every
    # line the client fills is anchored by a SignNow tag; Title is left blank
    # for the signer to complete, exactly as the executed form leaves it.
    para(doc, "").paragraph_format.space_after = Pt(0)
    p = doc.add_paragraph(); p.add_run("{{ company_legal_name }}").bold = True
    p.paragraph_format.space_after = Pt(4)
    sig = doc.add_paragraph("Signature: "); anchor(sig, "sn_client_signature")
    sig.add_run("________________________"); sig.paragraph_format.space_after = Pt(4)
    name = doc.add_paragraph("Name: "); anchor(name, "sn_client_printed_name")
    name.add_run("{{ signatory_name }}____________"); name.paragraph_format.space_after = Pt(4)
    t = doc.add_paragraph("Title: ____________________________"); t.paragraph_format.space_after = Pt(4)
    date = doc.add_paragraph("Date: "); anchor(date, "sn_client_date")
    date.add_run("____________________________")

    # CMO block: pre-filled, no fields.
    para(doc, "").paragraph_format.space_after = Pt(0)
    p = doc.add_paragraph(); p.add_run("Chief Marketplace Officer, Inc.").bold = True
    p.paragraph_format.space_after = Pt(4)
    sig = doc.add_paragraph("Signature: ____ ")
    sig.add_run("{{ cmo_signature_mark }}").italic = True   # William's typed mark
    sig.add_run("____")
    sig.paragraph_format.space_after = Pt(4)
    for line in ("Name: {{ cmo_signatory_name }}", "Title: {{ cmo_signatory_title }}"):
        doc.add_paragraph(line).paragraph_format.space_after = Pt(4)
    doc.add_paragraph("Date: {{ cmo_signature_date }}")

    # ---- Schedules
    h1(doc, "Schedule A – Designated Third Party Marketplaces", centered=True)
    para(doc, "Chief Marketplace Officer, Inc. agrees to provide Consulting Services for the "
              "following third party marketplaces:", centered=True)
    indented(doc, "Amazon.com", marker="1.")
    indented(doc, "Additional marketplaces may be included as agreed upon by both Parties "
                  "through a written amendment to this Schedule.", level=1)

    h1(doc, "Schedule B – Consulting Services", centered=True)
    para(doc, "As part of the Consulting Services, Chief Marketplace Officer, Inc. will "
              "provide the following services to the Company:")
    para(doc, "Deliverables:")
    # {%p ...%} makes docxtpl drop the control paragraph itself, so the loop
    # leaves no blank line above or below the list.
    doc.add_paragraph("{%p for item in schedule_b_deliverables %}")
    indented(doc, "{{ item }}", marker="{{ loop.index }}.")
    doc.add_paragraph("{%p endfor %}")

    h1(doc, "Schedule C – Intellectual Property", centered=True)
    para(doc, "The following outlines the Intellectual Property rights granted under this "
              "Agreement:")
    indented(doc, "Chief Marketplace Officer, Inc. IP", marker="1.")
    indented(doc, "CMO retains ownership of all methods, tools, and processes used in "
                  "providing the Consulting Services. The Company receives no rights to these "
                  "assets, except as required for the delivery of the Services.",
             level=2, marker="○")
    indented(doc, "Company IP", marker="2.")
    indented(doc, "Company retains all rights to its trademarks, logos, and proprietary "
                  "content, which CMO may use solely for the purpose of providing the "
                  "Consulting Services.", level=2, marker="○")
    indented(doc, "Work Product", marker="3.")
    indented(doc, "Any deliverables created specifically for the Company, such as reports, "
                  "analyses, images/graphic designs and other materials, will be considered "
                  "“work for hire” and owned by the Company upon full payment.",
             level=2, marker="○")
    indented(doc, "Trademarks/Word Marks", marker="4.")
    indented(doc, "The following comprise the trademarks and logos of Company for which it "
                  "grants to Chief Marketplace Officer, Inc. a non-exclusive, limited, and "
                  "revocable license to allow Chief Marketplace Officer, Inc. to perform the "
                  "Consulting Services.", level=2, marker="○")
    para(doc, "{{ trademark_exhibit }}", centered=True)

    h1(doc, "Schedule D – Indemnification Details", centered=True)
    indented(doc, "Company Indemnification", marker="1.")
    indented(doc, "Company agrees to indemnify and hold harmless CMO from claims arising "
                  "from:", level=2, marker="○")
    indented(doc, "Breach of this Agreement by the Company.", level=3, marker="a.")
    indented(doc, "The Company’s negligence or misconduct.", level=3, marker="b.")
    indented(doc, "Any claims related to the Company’s products, including intellectual "
                  "property disputes.", level=3, marker="c.")
    indented(doc, "CMO Indemnification", marker="2.")
    indented(doc, "CMO agrees to indemnify and hold harmless the Company from claims "
                  "arising from:", level=2, marker="○")
    indented(doc, "Breach of this Agreement by CMO.", level=3, marker="a.")
    indented(doc, "CMO’s negligence or misconduct in providing the Services.",
             level=3, marker="b.")
    indented(doc, "Notice of Claim", marker="3.")
    indented(doc, "The indemnified Party shall promptly notify the indemnifying Party of any "
                  "claims or actions. Failure to notify will not relieve the indemnifying "
                  "Party of its obligations except to the extent that the delay has "
                  "prejudiced the defense of the claim.", level=2, marker="○")

    h1(doc, "Schedule E – Sample Reseller Policy", centered=True)
    h1(doc, "INSERT BRAND Reseller Policy", centered=True)
    para(doc, "To protect our trademarks, logos, copyrighted materials, and ensure clarity "
              "for customers, INSERT BRAND has created this Reseller Policy. All authorized "
              "resellers (referred to as “Reseller”) who purchase INSERT BRAND products (the "
              "“Products”) for resale or distribution are required to follow this Policy. "
              "Unauthorized use of INSERT BRAND’s trademarks may violate federal, state, or "
              "international laws regarding infringement and unfair competition. If you are "
              "an approved Reseller, please refer to your reseller agreement for any "
              "additional requirements related to using INSERT BRAND trademarks.")
    h2(doc, "1. Guidelines for Trademark Usage")
    para(doc, "You are permitted to use INSERT BRAND trademarks (but not logos or taglines) "
              "to identify our products, services, and programs in your packaging, "
              "promotions, or advertising materials, provided the following rules are met:")
    indented(doc, "Company Names and Domains: INSERT BRAND trademarks cannot be used in your "
                  "company name, product name, or domain name.", marker="1.")
    indented(doc, "Logos and Taglines: Unless you have a separate licensing agreement with "
                  "INSERT BRAND, our logos cannot be used.", marker="2.")
    indented(doc, "Avoiding Confusion: Your company name or product name must not be similar "
                  "enough to any INSERT BRAND trademark to create confusion. Also, you cannot "
                  "use INSERT BRAND trademarks in a way that implies any form of sponsorship, "
                  "affiliation, certification, or endorsement by INSERT BRAND unless such an "
                  "arrangement exists", marker="3.")
    indented(doc, "Proper Use:", marker="4.")
    indented(doc, "Do not shorten, modify, or abbreviate any INSERT BRAND trademark.",
             level=2, marker="i)")
    indented(doc, "Trademark Attribution: When using INSERT BRAND trademarks, include this "
                  "attribution: “[List of marks used] are trademarks of INSERT BRAND.”",
             level=2, marker="ii)")
    indented(doc, "Original Packaging: You must sell all INSERT BRAND products in their "
                  "original packaging, without altering labels, specifications, or any "
                  "advertising content that came with the Products.", level=2, marker="iii)")
    h2(doc, "2. Reporting Unauthorized Sellers")
    para(doc, "If you become aware of or suspect anyone reselling or distributing INSERT "
              "BRAND products without authorization, please inform INSERT BRAND immediately, "
              "including any relevant contact or company information.")
    h2(doc, "3. Restrictions on Online Marketplaces")
    para(doc, "Resellers are prohibited from advertising or selling INSERT BRAND products on "
              "any third-party online marketplaces without prior written consent from INSERT "
              "BRAND. This includes selling to other distributors or resellers who may in "
              "turn list the Products on such platforms.")
    h2(doc, "4. Handling and Storage of Products")
    para(doc, "Resellers are required to handle and store INSERT BRAND products according to "
              "our handling and storage guidelines, ensuring safety and quality control.")
    h2(doc, "5. Compliance with INSERT BRAND Policies")
    para(doc, "As a reseller, you must comply with INSERT BRAND’s Minimum Advertised Pricing "
              "Policy (MAP), as well as other business practices, including warranty terms.")
    h2(doc, "6. Warranty Support")
    para(doc, "You are authorized to extend INSERT BRAND’s original manufacturer warranty to "
              "your customers under the terms set by INSERT BRAND. You may not alter or "
              "misrepresent the manufacturer’s warranty, nor offer additional warranties "
              "without authorization. All warranty and customer support obligations must be "
              "met in accordance with INSERT BRAND guidelines, which may change over time.")
    h2(doc, "7. Use of Copyrighted Material")
    para(doc, "Any use of INSERT BRAND’s copyrighted materials, such as website content, "
              "images, videos, and text, is strictly prohibited unless prior approval is "
              "granted. If authorized, proper copyright notices must accompany any use of "
              "these materials.")
    h2(doc, "8. General Provisions")
    para(doc, "This Policy is considered part of any agreement you have with INSERT BRAND "
              "regarding your purchase or sale of the Products. In case of any conflict "
              "between this Policy and your agreement, the terms of the agreement will take "
              "precedence. INSERT BRAND reserves the right to update this Policy at any time "
              "and may prohibit any use of its trademarks that it deems unlawful or improper.")
    para(doc, "By signing below, you acknowledge and accept the terms and conditions outlined "
              "in the INSERT BRAND Reseller Policy, including our right to discontinue "
              "business with any reseller who violates this policy. Additionally, "
              "international sales of INSERT BRAND products are prohibited. Approved "
              "resellers may only sell INSERT BRAND products to end customers in the United "
              "States and Canada.")

    para(doc, "").paragraph_format.space_after = Pt(0)
    p = doc.add_paragraph(); p.add_run("INSERT BRAND").bold = True; p.paragraph_format.space_after = Pt(4)
    for line in ("Signature: _______________________", "Name: __________________________",
                 "Title: ____________________________"):
        doc.add_paragraph(line).paragraph_format.space_after = Pt(4)
    doc.add_paragraph("Date: ___________________________")
    para(doc, "").paragraph_format.space_after = Pt(0)
    p = doc.add_paragraph(); p.add_run("Reseller Information").bold = True; p.paragraph_format.space_after = Pt(4)
    for line in ("Company Name: _________________", "Signature: _______________________",
                 "Name: __________________________", "Title: ____________________________"):
        doc.add_paragraph(line).paragraph_format.space_after = Pt(4)
    doc.add_paragraph("Date: ___________________________")

    TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
    doc.save(TEMPLATE)
    return TEMPLATE


if __name__ == "__main__":
    print(f"wrote {build()}")
