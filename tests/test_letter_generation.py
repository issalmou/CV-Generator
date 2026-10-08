"""
Cover letter integration — services + `POST /api/generate-letter`.

The LLM is mocked (conftest `mock_llm` / `client`). These tests check the
deterministic glue: no fabricated values, FR/EN labels, PDF-bytes output
(never a file on disk), and the endpoint contract.
"""

from __future__ import annotations

import json

import pytest

import fitz  # PyMuPDF — read back the rendered letter text

from services.jobs.company_parser import JobCompanyParser
from services.cv.letter_generator import LetterGenerator
from services.cv.letter_pdf_generator import LetterPDFGenerator


def _pdf_text(pdf: bytes) -> str:
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        return "\n".join(page.get_text("text") for page in doc)


# ---------------------------------------------------------------------------
# JobCompanyParser
# ---------------------------------------------------------------------------

def test_job_company_parser_empty_jd_makes_no_llm_call(mock_llm):
    result = JobCompanyParser().extract("   ", language="en")
    assert result == {
        "company_name": "", "position": "", "location": "",
        "recipient": "", "company_address": "",
    }
    assert mock_llm.calls == []


def test_job_company_parser_parses_fields(mock_llm):
    result = JobCompanyParser().extract("Backend Engineer at Globex, Rabat.", language="en")
    assert result["company_name"] == "Globex"
    assert result["position"] == "Backend Engineer"
    assert "letter_job_company" in mock_llm.calls


def test_job_company_parser_degrades_to_empty_on_bad_json(mock_llm):
    mock_llm.set(lambda *a, **k: "not json at all")
    result = JobCompanyParser().extract("some job", language="fr")
    assert result["company_name"] == "" and result["position"] == ""


# ---------------------------------------------------------------------------
# LetterGenerator
# ---------------------------------------------------------------------------

def test_letter_generator_returns_body_and_passes_language(mock_llm):
    seen: dict = {}

    def _router(prompt, **kwargs):
        seen["prompt"] = prompt
        return "Dear Team,\n\nBody.\n\nRegards,\nX"

    mock_llm.set(_router)
    body = LetterGenerator().generate(
        {"name": "John Doe"}, "Backend role at Globex", {"company_name": "Globex"}, language="fr"
    )
    assert "Body." in body
    assert "French" in seen["prompt"]          # language directive is in the prompt
    low = seen["prompt"].lower()
    assert "anti-fabrication" in low
    assert "the only source of facts about the candidate" in low
    assert "not a source of candidate facts" in low   # JD is context, not evidence
    # Phase 5 — the letter must not re-narrate the CV section by section
    assert "do not walk through the cv section by section" in low
    assert "must not copy a sentence from it" in low


# ---------------------------------------------------------------------------
# LetterPDFGenerator
# ---------------------------------------------------------------------------

_LETTER = "Dear Hiring Team,\n\nI am applying for the role.\n\nThank you.\n\nSincerely,\nJohn Doe"


def test_letter_pdf_render_to_bytes_is_a_pdf():
    pdf = LetterPDFGenerator().render_to_bytes(
        {"name": "John Doe", "email": "j@x.com", "phone": "+1", "address": "Rabat"},
        {"company_name": "Globex", "position": "Backend Engineer", "recipient": "Hiring Team"},
        _LETTER, language="en",
    )
    assert isinstance(pdf, bytes) and pdf[:5] == b"%PDF-"


@pytest.mark.parametrize("language, present, absent", [
    ("en", "Subject:", "Objet"),
    ("fr", "Objet", "Subject:"),
])
def test_letter_pdf_labels_follow_language(language, present, absent):
    pdf = LetterPDFGenerator().render_to_bytes(
        {"name": "Jane", "email": "j@x.com", "phone": "", "address": ""},
        {"company_name": "Globex", "position": "Dev"},
        _LETTER, language=language,
    )
    text = _pdf_text(pdf)
    assert present in text
    assert absent not in text


def test_letter_pdf_no_company_still_renders():
    pdf = LetterPDFGenerator().render_to_bytes(
        {"name": "Jane", "email": "j@x.com", "phone": "", "address": ""},
        {"company_name": "", "position": "", "recipient": "", "company_address": ""},
        _LETTER, language="en",
    )
    assert pdf[:5] == b"%PDF-"          # no placeholder like "the Company" needed


# ---------------------------------------------------------------------------
# POST /api/generate-letter  — auth, JSON + presigned URL, row in DB
# ---------------------------------------------------------------------------

from models import GeneratedLetter


def _letter_payload(cv_profile_dict, language="en"):
    return {
        "language": language,
        "cv_profile": cv_profile_dict["cv_profile"],
        "job_description": "We are hiring a Python backend engineer at Globex in Rabat.",
    }


def test_generate_letter_returns_json_and_persists(client, db, test_user, cv_profile_dict, fake_minio):
    r = client.post("/api/generate-letter", json=_letter_payload(cv_profile_dict))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success"
    assert body["language"] == "en"
    assert body["download_url"].startswith("http://minio.test/")
    assert "cover_letter_" in body["filename"]

    row = db.get(GeneratedLetter, body["generated_letter_id"])
    assert row is not None and row.user_id == test_user.id
    assert fake_minio[row.storage_key][:5] == b"%PDF-"


@pytest.mark.parametrize("language", ["fr", "en"])
def test_generate_letter_both_languages(client, cv_profile_dict, language):
    r = client.post("/api/generate-letter", json=_letter_payload(cv_profile_dict, language))
    assert r.status_code == 200
    assert r.json()["language"] == language


def test_generate_letter_rejects_missing_job_description(client, cv_profile_dict):
    payload = _letter_payload(cv_profile_dict)
    payload["job_description"] = ""
    r = client.post("/api/generate-letter", json=payload)
    assert r.status_code == 422


def test_generate_letter_rejects_bad_language(client, cv_profile_dict):
    payload = _letter_payload(cv_profile_dict)
    payload["language"] = "de"
    r = client.post("/api/generate-letter", json=payload)
    assert r.status_code == 422


def test_generate_letter_writes_no_file(client, cv_profile_dict, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client.post("/api/generate-letter", json=_letter_payload(cv_profile_dict))
    assert not any(tmp_path.rglob("*.pdf"))
