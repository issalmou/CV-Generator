"""
Spec §4 / §23 — PDF hyperlink extraction & classification.

The critical case: the visible text is only "LinkedIn" but the PDF holds
a link annotation with the real profile URL. The pipeline must return
that exact URL.
"""

from __future__ import annotations

import io

import pytest

from services.cv.link_extractor import (
    ClassifiedLinks,
    PdfLink,
    classify_links,
    links_from_text,
)


def _pdf_with_link(url: str, visible: str = "LinkedIn") -> bytes:
    """A one-page PDF whose only clickable text is `visible`, linking to `url`."""
    pytest.importorskip("reportlab")
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawString(72, 720, "JANE DOE")
    c.drawString(72, 700, "jane.doe@example.com")
    text_obj_x, text_obj_y = 72, 680
    c.drawString(text_obj_x, text_obj_y, visible)
    # rectangle around the visible word
    c.linkURL(url, (text_obj_x, text_obj_y - 2, text_obj_x + 60, text_obj_y + 12), relative=0)
    c.showPage()
    c.save()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# classification of the five written URL formats (§23)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "written",
    [
        "https://www.linkedin.com/in/example",
        "https://linkedin.com/in/example",
        "www.linkedin.com/in/example",
        "linkedin.com/in/example",
        "https://www.linkedin.com/in/example/",
        "https://fr.linkedin.com/in/example",
    ],
)
def test_all_written_linkedin_formats_are_recognised(written):
    result = classify_links(text_urls=links_from_text(written))
    assert result.linkedin is not None
    assert "linkedin.com/in/example" in result.linkedin.lower()
    assert result.linkedin.startswith("https://")


def test_github_and_portfolio_and_email_classification():
    urls = [
        "github.com/janedoe",
        "https://janedoe.dev",
        "mailto:jane@doe.dev",
        "https://twitter.com/janedoe",
    ]
    r = classify_links(text_urls=urls)
    assert r.github == "https://github.com/janedoe"
    assert r.portfolio == "https://janedoe.dev"
    assert r.email == "jane@doe.dev"
    assert any("twitter" in o for o in r.others)


def test_pdf_annotation_url_wins_over_text():
    r = classify_links(
        pdf_links=[PdfLink(url="https://www.linkedin.com/in/real-profile", text="LinkedIn")],
        text_urls=["linkedin.com/in/some-other-guess"],
    )
    assert r.linkedin == "https://www.linkedin.com/in/real-profile"


def test_extra_github_and_portfolio_urls_go_to_others_never_dropped():
    # a 2nd github (a project repo) and a 2nd site must survive in `others`
    r = classify_links(text_urls=[
        "github.com/janedoe",                       # profile -> github slot
        "github.com/janedoe/coolproject",           # repo    -> others
        "https://janedoe.dev",                      # portfolio slot
        "https://coolproject.vercel.app",           # demo    -> others
    ])
    assert r.github == "https://github.com/janedoe"
    assert r.portfolio == "https://janedoe.dev"
    assert "https://github.com/janedoe/coolproject" in r.others
    assert "https://coolproject.vercel.app" in r.others


# ---------------------------------------------------------------------------
# end-to-end: hidden hyperlink -> profile.linkedin (§23)
# ---------------------------------------------------------------------------

def test_linkedin_recovered_from_pdf_annotation_only(mock_llm):
    from services.cv.resume_parser_pipeline import ResumeParserPipeline

    url = "https://www.linkedin.com/in/issalmou-adaaiche-1390bb281"
    pdf = _pdf_with_link(url, visible="LinkedIn")

    result = ResumeParserPipeline().parse_bytes(pdf, "cv.pdf", language="en")

    # the URL is NOT in the extracted text — only in the annotation
    assert url not in result.cv_profile.professional_summary if result.cv_profile.professional_summary else True
    assert result.cv_profile.linkedin == url
    assert result.cv_profile.email == "jane.doe@example.com"


def test_pdf_link_extraction_reads_annotations():
    from services.cv.pdf_layout_reader import PDFLayoutReader

    pdf = _pdf_with_link("https://github.com/jane", visible="GitHub")
    doc = PDFLayoutReader().read_bytes(pdf)
    assert any("github.com/jane" in l.url for l in doc.links)


def test_classified_links_to_dict():
    d = ClassifiedLinks(linkedin="x").to_dict()
    assert set(d) == {"linkedin", "github", "portfolio", "email", "others"}
