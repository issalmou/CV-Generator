"""Unit tests: services.cv.contact_extractor (regex only, no LLM, no fabrication)."""

from __future__ import annotations

import pytest

from services.cv.contact_extractor import ContactExtractor


@pytest.fixture
def extractor() -> ContactExtractor:
    return ContactExtractor()


def test_extracts_core_fields(extractor):
    contact_block = (
        "JOHN DOE\n"
        "john.doe@example.com | +212 600 112233\n"
        "linkedin.com/in/johndoe | github.com/johndoe\n"
        "johndoe.dev\n"
    )
    result = extractor.extract(contact_block)
    assert result["name"] == "John Doe"
    assert result["email"] == "john.doe@example.com"
    assert "600" in result["phone"]
    assert "linkedin.com/in/johndoe" in result["linkedin"]
    assert "github.com/johndoe" in result["github"]
    assert result["portfolio"].endswith("johndoe.dev")


def test_missing_fields_are_none_never_fabricated(extractor):
    result = extractor.extract("SOMEONE\n(no contact info here)\n")
    assert result["email"] is None
    assert result["phone"] is None
    assert result["linkedin"] is None
    assert result["github"] is None
    assert result["portfolio"] is None
    assert result["address"] is None
    assert result["nationality"] is None


@pytest.mark.parametrize(
    "line, expected",
    [
        ("JOHN DOE", "John Doe"),
        ("Jean-Pierre Dupont", "Jean-Pierre Dupont"),
        ("MARIA GARCIA LOPEZ", "Maria Garcia Lopez"),
        ("SARAH O'CONNOR", "Sarah O'Connor"),
        ("JEAN-PIERRE O'BRIEN", "Jean-Pierre O'Brien"),
    ],
)
def test_name_single_line_variants(extractor, line, expected):
    assert extractor.extract(f"{line}\nfoo@bar.com\n")["name"] == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("+33 6 12 34 56 78", "+33 6 12 34 56 78"),   # French mobile: lone leading digit
        ("+212 640 065 118", "+212 640 065 118"),
        ("+44 20 7946 0958", "+44 20 7946 0958"),
        ("+1 415 555 0100", "+1 415 555 0100"),
        ("0640065118", "0640065118"),
    ],
)
def test_phone_formats_kept_with_country_code(extractor, raw, expected):
    result = extractor.extract(f"JANE ROE\njane@x.com  |  {raw}  |  Paris\n")
    assert result["phone"] == expected


def test_name_split_across_two_lines(extractor):
    # narrow-sidebar template: first / last name on separate lines
    result = extractor.extract("ISSALMOU\nADAAICHE\nDeveloper\nx@y.com\n")
    assert result["name"] == "Issalmou Adaaiche"


def test_address_and_nationality_only_from_labelled_lines(extractor):
    block = (
        "JOHN DOE\n"
        "Nationality: Moroccan\n"
        "Address: 12 Rue de la Paix, Rabat, Morocco\n"
        "john@x.com\n"
    )
    result = extractor.extract(block)
    assert result["nationality"] == "Moroccan"
    assert result["address"].startswith("12 Rue de la Paix")


def test_french_labels_for_address_and_nationality(extractor):
    block = "JEANNE\nNationalite : Francaise\nAdresse : 5 avenue Victor Hugo, Lyon\nj@x.fr\n"
    result = extractor.extract(block)
    assert result["nationality"] == "Francaise"
    assert "Victor Hugo" in result["address"]


def test_nationality_not_inferred_from_place_or_language(extractor):
    block = "JOHN DOE\njohn@x.com\nBased in Casablanca\nSpeaks Arabic and French\n"
    result = extractor.extract(block)
    assert result["nationality"] is None


def test_phone_not_confused_with_year(extractor):
    result = extractor.extract("JOHN DOE\njohn@x.com\nGraduated 2019\n")
    assert result["phone"] is None


def test_phone_on_a_crowded_icon_contact_bar(extractor):
    # email + phone + linkedin on one line, no '|' separator, icon glyphs
    block = "AMINE BENNANI\nI +212 661 22 33 44  I amine@example.com  I linkedin.com/in/amine\n"
    result = extractor.extract(block)
    assert result["phone"] == "+212 661 22 33 44"
    assert result["email"] == "amine@example.com"


@pytest.mark.parametrize(
    "line, expected",
    [
        ("+ Rabat, Morocco", "Rabat, Morocco"),
        ("Casablanca, Morocco", "Casablanca, Morocco"),
        ("12 Rue de la Paix, Lyon, France", "12 Rue de la Paix, Lyon, France"),
    ],
)
def test_unlabelled_geo_address_on_contact_bar(extractor, line, expected):
    result = extractor.extract(f"JOHN DOE\njohn@x.com\n{line}\n")
    assert result["address"] == expected


def test_geo_address_only_from_the_contact_bar_not_later_lines(extractor):
    # a comma list further down (after the blank line that ends the contact
    # bar) is never treated as an address
    block = "JOHN DOE\njohn@x.com\n\nReact, Redux, Node\n"
    assert extractor.extract(block)["address"] is None


def test_name_found_in_document_body_for_two_column_layout(extractor):
    # Canva/sidebar template: splitter reads the sidebar first, so the name
    # (top of the main column) lands deep in the reconstructed text, after
    # the sidebar's Profile / Languages / Interests blocks.
    contact_section = ""  # nothing before the first detected header
    full_text = (
        "Profile\nPassionate developer.\n"
        "0640065118\nsomeone@example.com\n"
        "LANGUAGES\nArabic (Native)\n"
        "ISSALMOU\nADAAICHE\n"
        "Full Stack Developer\n"
        "EDUCATION\n2025-Present Master\n"
    )
    result = extractor.extract(contact_section, full_text=full_text)
    assert result["name"] == "Issalmou Adaaiche"


def test_document_name_scan_does_not_match_section_headers(extractor):
    full_text = "CENTRES\nD INTERET\nSport\nMusique\nEDUCATION\nSKILLS\n"
    assert extractor.extract("", full_text=full_text)["name"] is None


def test_full_text_fallback_for_email(extractor):
    # contact block has no email; full text does
    result = extractor.extract("JOHN DOE\n", full_text="JOHN DOE\n...\nReach me at john@x.com\n")
    assert result["email"] == "john@x.com"
