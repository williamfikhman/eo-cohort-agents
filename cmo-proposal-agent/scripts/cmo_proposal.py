#!/usr/bin/env python3
"""Render a Chief Marketplace Officer proposal from a JSON spec.

    python3 scripts/cmo_proposal.py output/acme/spec.json \
        -t "assets/CMO Proposal Template.docx" \
        -o "output/acme/CMO Proposal - Acme.docx" --pdf --strict

The spec is plain JSON; assets/template_spec.json shows every field. The template
supplies page setup and styles. The body, header and footer are rebuilt from the
spec on every run, so the template stays a blank proposal and the output is always
complete. Without -t (or when the template file does not exist yet) the document is
built from scratch with the built-in styling. That is how the template itself is
made:

    python3 scripts/cmo_proposal.py assets/template_spec.json \
        -t "assets/CMO Proposal Template.docx" \
        -o "assets/CMO Proposal Template.docx"

--pdf converts with LibreOffice (soffice) and counts pages with poppler (pdfinfo).
--strict refuses to write a document that still contains a [bracketed placeholder].

Exit codes: 0 ok, 1 bad input or missing tool, 2 the PDF is longer than max_pages.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor
except ImportError:  # pragma: no cover
    print("FATAL: python-docx is not installed. Run: pip install -r requirements.txt",
          file=sys.stderr)
    sys.exit(1)

PLACEHOLDER_RE = re.compile(r"\[[^\]\n]{1,120}\]")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")

DEFAULT_BRAND = {
    "company": "Chief Marketplace Officer",
    "short": "CMO",
    "sender_name": "",
    "sender_title": "",
    "sender_email": "",
    "website": "",
    "accent": "1F3A5F",
    "font": "Calibri",
}

GREY = RGBColor(0x59, 0x59, 0x59)

# OOXML is order-sensitive inside pPr and tblPr. These are the children that
# may follow the ones we add, so insert_element_before lands them in a valid slot.
_PPR_AFTER_PBDR = (
    "w:shd", "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap",
    "w:overflowPunct", "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN", "w:bidi",
    "w:adjustRightInd", "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing",
    "w:mirrorIndents", "w:suppressOverlap", "w:jc", "w:textDirection",
    "w:textAlignment", "w:textboxTightWrap", "w:outlineLvl", "w:divId", "w:cnfStyle",
    "w:rPr", "w:sectPr", "w:pPrChange",
)
_PPR_AFTER_TABS = _PPR_AFTER_PBDR[2:]
_TBLPR_AFTER_BORDERS = ("w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook",
                        "w:tblCaption", "w:tblDescription")
_TBLPR_AFTER_CELLMAR = ("w:tblLook", "w:tblCaption", "w:tblDescription")


# ----------------------------------------------------------------------------
# Spec loading and formatting helpers
# ----------------------------------------------------------------------------

def load_spec(path: Path) -> dict:
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"FATAL: {path} is not valid JSON: {exc}")
    if not isinstance(spec, dict):
        raise SystemExit(f"FATAL: {path} must contain a JSON object")
    brand = dict(DEFAULT_BRAND)
    brand.update(spec.get("brand") or {})
    spec["brand"] = brand
    spec.setdefault("prospect", {})
    spec.setdefault("pricing", {})
    spec.setdefault("title", "Marketplace Growth Proposal")
    if not str(spec["prospect"].get("company") or "").strip():
        raise SystemExit("FATAL: spec.prospect.company is empty")
    return spec


def walk_strings(value, path="spec"):
    """Yield (path, string) for every string in the spec, internal_notes excluded."""
    if isinstance(value, dict):
        for key, item in value.items():
            if path == "spec" and key == "internal_notes":
                continue
            yield from walk_strings(item, f"{path}.{key}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from walk_strings(item, f"{path}[{i}]")
    elif isinstance(value, str):
        yield path, value


def find_placeholders(spec: dict) -> list[str]:
    found = []
    for path, text in walk_strings(spec):
        for match in PLACEHOLDER_RE.findall(text):
            found.append(f"{path}: {match}")
    return found


def money(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return value
    if float(value).is_integer():
        return f"${int(value):,}"
    return f"${value:,.2f}"


def pct(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return value if value.endswith("%") else f"{value}%"
    if float(value).is_integer():
        return f"{int(value)}%"
    return f"{value:g}%"


def parse_date(raw) -> dt.date:
    if not raw:
        return dt.date.today()
    if isinstance(raw, str):
        for fmt in ("%Y-%m-%d", "%B %d, %Y", "%m/%d/%Y"):
            try:
                return dt.datetime.strptime(raw.strip(), fmt).date()
            except ValueError:
                continue
    raise SystemExit(f"FATAL: cannot read date {raw!r}; use YYYY-MM-DD")


def long_date(value: dt.date) -> str:
    return f"{value.strftime('%B')} {value.day}, {value.year}"


def hex_rgb(value: str) -> RGBColor:
    value = (value or "1F3A5F").lstrip("#")
    return RGBColor(int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


# ----------------------------------------------------------------------------
# Low-level docx helpers
# ----------------------------------------------------------------------------

def clear_body(doc) -> None:
    body = doc.element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)


def clear_paragraphs(container) -> None:
    for p in list(container.paragraphs):
        p._element.getparent().remove(p._element)
    for t in list(container.tables):
        t._element.getparent().remove(t._element)


def set_font(style_or_run, name: str, size=None, bold=None, color=None, italic=None):
    font = style_or_run.font
    font.name = name
    rpr = style_or_run.element.get_or_add_rPr() if hasattr(style_or_run, "element") \
        else style_or_run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(attr), name)
    if size is not None:
        font.size = Pt(size)
    if bold is not None:
        font.bold = bold
    if italic is not None:
        font.italic = italic
    if color is not None:
        font.color.rgb = color


def ensure_styles(doc, brand: dict) -> None:
    """Make sure the styles the body uses exist and carry the brand look.

    Runs on both fresh documents and the template. Deliberately only sets font,
    size, color and spacing; anything else set in Word on the template survives.
    """
    accent = hex_rgb(brand.get("accent"))
    font = brand.get("font") or "Calibri"
    styles = doc.styles

    normal = styles["Normal"]
    set_font(normal, font, size=11, color=RGBColor(0x1A, 0x1A, 0x1A))
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.12

    title = styles["Title"]
    set_font(title, font, size=26, bold=True, color=accent)
    title.paragraph_format.space_before = Pt(0)
    title.paragraph_format.space_after = Pt(4)
    # Word's default Title style carries a bottom border; drop it for a clean block.
    ppr = title.element.get_or_add_pPr()
    for border in ppr.findall(qn("w:pBdr")):
        ppr.remove(border)

    h1 = styles["Heading 1"]
    set_font(h1, font, size=15, bold=True, color=accent)
    h1.paragraph_format.space_before = Pt(16)
    h1.paragraph_format.space_after = Pt(4)
    h1.paragraph_format.keep_with_next = True

    h2 = styles["Heading 2"]
    set_font(h2, font, size=12, bold=True, color=RGBColor(0x1A, 0x1A, 0x1A))
    h2.paragraph_format.space_before = Pt(8)
    h2.paragraph_format.space_after = Pt(2)
    h2.paragraph_format.keep_with_next = True

    for name in ("List Bullet", "List Number"):
        st = styles[name]
        st.paragraph_format.space_after = Pt(3)


def add_text(paragraph, text: str, size=None, color=None, bold=None, italic=None):
    """Append runs to a paragraph, honoring **bold** markup."""
    pos = 0
    for match in BOLD_RE.finditer(text):
        if match.start() > pos:
            _run(paragraph, text[pos:match.start()], size, color, bold, italic)
        _run(paragraph, match.group(1), size, color, True, italic)
        pos = match.end()
    if pos < len(text):
        _run(paragraph, text[pos:], size, color, bold, italic)
    return paragraph


def _run(paragraph, text, size, color, bold, italic):
    run = paragraph.add_run(text)
    if size is not None:
        run.font.size = Pt(size)
    if color is not None:
        run.font.color.rgb = color
    if bold is not None:
        run.font.bold = bold
    if italic is not None:
        run.font.italic = italic
    return run


def para(doc, text="", style=None, align=None, **fmt):
    p = doc.add_paragraph(style=style)
    if text:
        add_text(p, text, **fmt)
    if align is not None:
        p.alignment = align
    return p


def shade(cell, hex_fill: str) -> None:
    tcpr = cell._element.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tcpr.append(shd)


def cell_margins(table, top=60, bottom=60, left=100, right=100) -> None:
    tblpr = table._element.tblPr
    mar = OxmlElement("w:tblCellMar")
    for side, val in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:w"), str(val))
        el.set(qn("w:type"), "dxa")
        mar.append(el)
    tblpr.insert_element_before(mar, *_TBLPR_AFTER_CELLMAR)


def set_borders(table, color="BFBFBF", size=4, inside=True) -> None:
    tblpr = table._element.tblPr
    borders = OxmlElement("w:tblBorders")
    edges = ["top", "left", "bottom", "right"] + (["insideH", "insideV"] if inside else [])
    for edge in edges:
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), str(size))
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), color)
        borders.append(el)
    tblpr.insert_element_before(borders, *_TBLPR_AFTER_BORDERS)


def no_borders(table) -> None:
    tblpr = table._element.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "nil")
        borders.append(el)
    tblpr.insert_element_before(borders, *_TBLPR_AFTER_BORDERS)


def set_col_widths(table, widths_in) -> None:
    """Fixed column widths. Word reads the cell widths, LibreOffice the grid."""
    table.autofit = False
    for idx, width in enumerate(widths_in):
        table.columns[idx].width = Inches(width)
    for row in table.rows:
        for idx, width in enumerate(widths_in):
            row.cells[idx].width = Inches(width)


def bottom_rule(paragraph, color="BFBFBF", size=6) -> None:
    ppr = paragraph._element.get_or_add_pPr()
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(size))
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), color)
    pbdr.append(bottom)
    ppr.insert_element_before(pbdr, *_PPR_AFTER_PBDR)


def add_page_number(paragraph) -> None:
    run = paragraph.add_run()
    for tag, text in (("begin", None), (None, "PAGE"), ("end", None)):
        if tag:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), tag)
        else:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = text
        run._element.append(el)
    run.font.size = Pt(9)
    run.font.color.rgb = GREY


# ----------------------------------------------------------------------------
# Document assembly
# ----------------------------------------------------------------------------

def page_setup(doc) -> None:
    for section in doc.sections:
        section.page_width = Inches(8.5)
        section.page_height = Inches(11)
        section.left_margin = section.right_margin = Inches(1)
        section.top_margin = Inches(0.9)
        section.bottom_margin = Inches(0.9)
        section.header_distance = Inches(0.4)
        section.footer_distance = Inches(0.4)


def build_header_footer(doc, spec: dict) -> None:
    brand = spec["brand"]
    accent = hex_rgb(brand.get("accent"))
    section = doc.sections[0]
    section.different_first_page_header_footer = False

    header = section.header
    header.is_linked_to_previous = False
    clear_paragraphs(header)
    hp = header.add_paragraph()
    hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    add_text(hp, brand["company"].upper(), size=9, color=accent, bold=True)
    hp.paragraph_format.space_after = Pt(0)

    footer = section.footer
    footer.is_linked_to_previous = False
    clear_paragraphs(footer)
    fp = footer.add_paragraph()
    fp.paragraph_format.space_after = Pt(0)
    bits = [b for b in (brand["company"], brand.get("sender_email"), brand.get("website")) if b]
    add_text(fp, "  ·  ".join(bits), size=9, color=GREY)
    fp.add_run("\t")
    add_text(fp, "Page ", size=9, color=GREY)
    add_page_number(fp)
    # Right-aligned tab stop at the text width.
    ppr = fp._element.get_or_add_pPr()
    tabs = OxmlElement("w:tabs")
    tab = OxmlElement("w:tab")
    tab.set(qn("w:val"), "right")
    tab.set(qn("w:pos"), str(int((8.5 - 2) * 1440)))
    tabs.append(tab)
    ppr.insert_element_before(tabs, *_PPR_AFTER_TABS)


def build_title_block(doc, spec: dict) -> None:
    brand, prospect = spec["brand"], spec["prospect"]
    accent = hex_rgb(brand.get("accent"))
    issued = parse_date(spec.get("date"))
    valid_days = int(spec.get("valid_days") or 30)
    valid_through = issued + dt.timedelta(days=valid_days)

    para(doc, spec["title"], style="Title")
    p = para(doc, f"Prepared for **{prospect['company']}**", size=14)
    p.paragraph_format.space_after = Pt(14)

    meta = doc.add_table(rows=0, cols=2)
    meta.alignment = WD_TABLE_ALIGNMENT.LEFT
    no_borders(meta)
    cell_margins(meta, top=20, bottom=20, left=0, right=120)
    rows = [
        ("Prepared for", ", ".join(x for x in (
            prospect.get("contact_name"), prospect.get("contact_title")) if x)
         or prospect["company"]),
        ("Prepared by", ", ".join(x for x in (
            brand.get("sender_name"), brand.get("sender_title"), brand["company"]) if x)),
        ("Date", long_date(issued)),
        ("Valid through", long_date(valid_through)),
    ]
    if prospect.get("contact_email"):
        rows.insert(1, ("", prospect["contact_email"]))
    for label, value in rows:
        cells = meta.add_row().cells
        lp = cells[0].paragraphs[0]
        add_text(lp, label, size=10, color=GREY, bold=True)
        lp.paragraph_format.space_after = Pt(0)
        vp = cells[1].paragraphs[0]
        add_text(vp, value, size=10.5)
        vp.paragraph_format.space_after = Pt(0)
    set_col_widths(meta, (1.4, 5.1))

    rule = para(doc)
    rule.paragraph_format.space_before = Pt(6)
    rule.paragraph_format.space_after = Pt(2)
    bottom_rule(rule, color=brand.get("accent", "1F3A5F"), size=12)


def build_section(doc, heading: str, body) -> None:
    if not body:
        return
    para(doc, heading, style="Heading 1")
    if isinstance(body, str):
        para(doc, body)
    else:
        for item in body:
            para(doc, item, style="List Bullet")


def build_scope(doc, scope: list) -> None:
    if not scope:
        return
    para(doc, "Scope of work", style="Heading 1")
    for block in scope:
        if isinstance(block, str):
            para(doc, block, style="List Bullet")
            continue
        title = block.get("title")
        if title:
            para(doc, title, style="Heading 2")
        if block.get("intro"):
            para(doc, block["intro"])
        for item in block.get("items") or []:
            para(doc, item, style="List Bullet")


def performance_line(pricing: dict) -> str:
    basis = pricing.get("performance_basis") or "attributable marketplace revenue"
    line = f"{pct(pricing.get('performance_pct'))} of {basis}"
    base = pricing.get("performance_base")
    if base not in (None, "", 0):
        line += f" above a {money(base)} monthly base"
    return line


def build_pricing(doc, spec: dict) -> None:
    pricing = spec["pricing"]
    brand = spec["brand"]
    para(doc, "Investment", style="Heading 1")

    table = doc.add_table(rows=1, cols=3)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    set_borders(table)
    cell_margins(table)
    head = table.rows[0].cells
    for cell, label in zip(head, ("Item", "What it covers", "Amount")):
        shade(cell, brand.get("accent", "1F3A5F"))
        hp = cell.paragraphs[0]
        add_text(hp, label, size=10, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF))
        hp.paragraph_format.space_after = Pt(0)

    rows = []
    if pricing.get("retainer_monthly") not in (None, ""):
        rows.append(("Monthly retainer",
                     pricing.get("retainer_covers")
                     or "Strategy, execution, and reporting across the marketplaces in scope.",
                     f"{money(pricing['retainer_monthly'])} / month"))
    if pricing.get("performance_pct") not in (None, ""):
        rows.append(("Performance fee", performance_line(pricing),
                     pct(pricing["performance_pct"])))
    for extra in pricing.get("extra_lines") or []:
        rows.append((extra.get("item", ""), extra.get("detail", ""), extra.get("amount", "")))
    if pricing.get("term_months") not in (None, ""):
        months = pricing["term_months"]
        rows.append(("Term", f"{months} months, then month to month.", "—"))

    for i, (item, detail, amount) in enumerate(rows):
        cells = table.add_row().cells
        if i % 2 == 1:
            for c in cells:
                shade(c, "F3F5F8")
        for cell, text, bold, align in (
            (cells[0], item, True, None),
            (cells[1], detail, None, None),
            (cells[2], amount, True, WD_ALIGN_PARAGRAPH.RIGHT),
        ):
            cp = cell.paragraphs[0]
            add_text(cp, text, size=10.5, bold=bold)
            cp.paragraph_format.space_after = Pt(0)
            if align is not None:
                cp.alignment = align
    set_col_widths(table, (1.5, 3.7, 1.3))

    if pricing.get("notes"):
        p = para(doc, pricing["notes"], size=10, color=GREY)
        p.paragraph_format.space_before = Pt(6)


def build_numbered(doc, heading: str, items: list) -> None:
    if not items:
        return
    para(doc, heading, style="Heading 1")
    for item in items:
        para(doc, item, style="List Number")


def build_signatures(doc, spec: dict) -> None:
    brand, prospect = spec["brand"], spec["prospect"]
    para(doc, "Agreement", style="Heading 1")
    para(doc, "Signing below accepts the scope, investment, and terms in this proposal "
              "and starts the engagement on the kickoff date agreed in writing.")
    table = doc.add_table(rows=1, cols=2)
    no_borders(table)
    cell_margins(table, left=0, right=300)
    parties = (
        (brand["company"], brand.get("sender_name"), brand.get("sender_title")),
        (prospect["company"], prospect.get("contact_name"), prospect.get("contact_title")),
    )
    for cell, (company, name, title) in zip(table.rows[0].cells, parties):
        cp = cell.paragraphs[0]
        add_text(cp, company, bold=True)
        cp.paragraph_format.space_before = Pt(8)
        cp.paragraph_format.space_after = Pt(18)
        for label, value in (("Signature", ""), ("Name", name or ""),
                             ("Title", title or ""), ("Date", "")):
            lp = cell.add_paragraph()
            add_text(lp, f"{label}: ", size=10, color=GREY)
            add_text(lp, value or "", size=10)
            if not value:
                bottom_rule(lp, color="8C8C8C", size=4)
            lp.paragraph_format.space_after = Pt(8)
    set_col_widths(table, (3.25, 3.25))


def build(doc, spec: dict) -> None:
    page_setup(doc)
    ensure_styles(doc, spec["brand"])
    clear_body(doc)
    build_header_footer(doc, spec)
    build_title_block(doc, spec)
    build_section(doc, "Overview", spec.get("summary"))
    build_section(doc, "What we heard", spec.get("situation"))
    build_scope(doc, spec.get("scope") or [])
    build_pricing(doc, spec)
    build_section(doc, "Terms", spec.get("terms"))
    build_numbered(doc, "Next steps", spec.get("next_steps") or [])
    build_signatures(doc, spec)


# ----------------------------------------------------------------------------
# PDF conversion and page check
# ----------------------------------------------------------------------------

def to_pdf(docx_path: Path) -> Path:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise SystemExit("FATAL: soffice not found on PATH. Install LibreOffice "
                         "(brew install --cask libreoffice) and see the README.")
    cmd = [soffice, "--headless", "--convert-to", "pdf",
           "--outdir", str(docx_path.parent), str(docx_path)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    pdf_path = docx_path.with_suffix(".pdf")
    if result.returncode != 0 or not pdf_path.exists():
        raise SystemExit("FATAL: LibreOffice failed to convert to PDF:\n"
                         f"{result.stdout}\n{result.stderr}")
    return pdf_path


def count_pages(pdf_path: Path) -> int | None:
    pdfinfo = shutil.which("pdfinfo")
    if pdfinfo:
        out = subprocess.run([pdfinfo, str(pdf_path)], capture_output=True, text=True)
        match = re.search(r"^Pages:\s+(\d+)", out.stdout, re.MULTILINE)
        if match:
            return int(match.group(1))
    # Fallback without poppler: count page objects in the PDF itself.
    data = pdf_path.read_bytes()
    match = re.search(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", data)
    if match:
        return int(match.group(1))
    pages = len(re.findall(rb"/Type\s*/Page\b", data))
    return pages or None


# ----------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", help="proposal spec JSON")
    ap.add_argument("-t", "--template", help="branded .docx to take page setup and styles from")
    ap.add_argument("-o", "--output", required=True, help="where to write the .docx")
    ap.add_argument("--pdf", action="store_true", help="also convert to PDF with LibreOffice")
    ap.add_argument("--max-pages", type=int, help="fail (exit 2) if the PDF is longer; "
                                                   "defaults to spec.max_pages or 4")
    ap.add_argument("--strict", action="store_true",
                    help="refuse to render if any [bracketed placeholder] remains")
    args = ap.parse_args()

    spec = load_spec(Path(args.spec))

    if args.strict:
        leftovers = find_placeholders(spec)
        if leftovers:
            print("FATAL: placeholders still in the spec:", file=sys.stderr)
            for item in leftovers:
                print(f"  {item}", file=sys.stderr)
            return 1

    template = Path(args.template) if args.template else None
    if template and template.exists():
        doc = Document(str(template))
        print(f"template  {template}")
    else:
        if template:
            print(f"template  {template} not found; building from built-in styling")
        doc = Document()

    build(doc, spec)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    print(f"docx      {out}")

    if not args.pdf:
        return 0

    pdf_path = to_pdf(out)
    pages = count_pages(pdf_path)
    limit = args.max_pages or int(spec.get("max_pages") or 4)
    if pages is None:
        print(f"pdf       {pdf_path} (page count unavailable; install poppler for pdfinfo)")
        return 0
    print(f"pdf       {pdf_path} ({pages} page{'s' if pages != 1 else ''}, limit {limit})")
    if pages > limit:
        print(f"TOO LONG: {pages} pages, limit is {limit}. Trim situation and scope, "
              "not pricing or terms.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
