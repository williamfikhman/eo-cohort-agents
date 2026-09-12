"""Render and export. These run LibreOffice, so they are the slow ones."""

import shutil

import pytest
from docx import Document

from config import ClientConfig
from fields import CLIENT_FIELDS
from render import RenderError, render

pytestmark = pytest.mark.skipif(
    not (shutil.which("soffice") or shutil.which("libreoffice")),
    reason="LibreOffice is needed to export a PDF",
)


def make_config(values, tmp_path, slug="acme"):
    return ClientConfig(slug=slug, path=tmp_path / f"{slug}.yaml", values=values)


def test_round_trip_produces_a_pdf_with_every_tag(
    client_values, cmo_values, tmp_path, template_path
):
    result = render(
        make_config(client_values, tmp_path),
        cmo_values,
        template_path=template_path,
        build_dir=tmp_path / "build",
        allow_scaffold=True,
    )
    assert result.pdf_path.is_file()
    assert result.page_count > 1
    assert set(result.tags_found) == {f.tag_name for f in CLIENT_FIELDS}


def test_client_values_reach_the_document(client_values, cmo_values, tmp_path, template_path):
    from pdfminer.high_level import extract_text

    result = render(
        make_config(client_values, tmp_path), cmo_values,
        template_path=template_path, build_dir=tmp_path / "build", allow_scaffold=True,
    )
    text = extract_text(str(result.pdf_path))
    normalised = " ".join(text.split())

    assert "Acme Sandbox Brands, LLC" in normalised
    assert "January 5, 2026" in normalised
    assert "$8,500 per month" in normalised
    assert "Weekly reporting" in normalised          # Schedule B loop ran
    assert "TBD" in normalised                        # trademark exhibit


def test_cmo_block_is_prefilled(client_values, cmo_values, tmp_path, template_path):
    from pdfminer.high_level import extract_text

    result = render(
        make_config(client_values, tmp_path), cmo_values,
        template_path=template_path, build_dir=tmp_path / "build", allow_scaffold=True,
    )
    normalised = " ".join(extract_text(str(result.pdf_path)).split())
    assert "/s/ William Fikhman" in normalised
    assert "Title: CEO" in normalised


def test_no_jinja_syntax_survives_into_the_document(
    client_values, cmo_values, tmp_path, template_path
):
    result = render(
        make_config(client_values, tmp_path), cmo_values,
        template_path=template_path, build_dir=tmp_path / "build", allow_scaffold=True,
    )
    doc = Document(str(result.docx_path))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "{%" not in text
    # The only {{...}} left standing must be SignNow tags.
    for fragment in text.split("{{")[1:]:
        name = fragment.split("}}")[0]
        assert name in {f.tag_name for f in CLIENT_FIELDS}, name


def test_scaffold_template_is_refused_by_default(
    client_values, cmo_values, tmp_path, template_path
):
    """The generated scaffold must not be able to reach a client."""
    with pytest.raises(RenderError, match="still the generated scaffold"):
        render(
            make_config(client_values, tmp_path), cmo_values,
            template_path=template_path, build_dir=tmp_path / "build",
        )


def test_todo_in_cmo_config_blocks_the_build(
    client_values, cmo_values, tmp_path, template_path
):
    cmo_values["address"] = "TODO: CMO principal place of business"
    with pytest.raises(RenderError, match="TODO"):
        render(
            make_config(client_values, tmp_path), cmo_values,
            template_path=template_path, build_dir=tmp_path / "build", allow_scaffold=True,
        )


def test_an_unfilled_placeholder_stops_the_build(cmo_values, tmp_path):
    """A template variable with nothing to fill it must never reach a PDF."""
    from docx import Document as NewDocument

    template = tmp_path / "leaky.docx"
    doc = NewDocument()
    doc.add_paragraph("Client: {{ company_legal_name }}")
    doc.add_paragraph("Unmapped: {{ variable_nobody_supplies }}")
    doc.save(str(template))

    config = make_config({"company_legal_name": "Acme"}, tmp_path)
    with pytest.raises(RenderError, match="nothing supplies"):
        render(config, cmo_values, template_path=template, build_dir=tmp_path / "build")


def test_missing_template_is_reported(client_values, cmo_values, tmp_path):
    with pytest.raises(RenderError, match="template not found"):
        render(
            make_config(client_values, tmp_path), cmo_values,
            template_path=tmp_path / "nope.docx", build_dir=tmp_path / "build",
        )
