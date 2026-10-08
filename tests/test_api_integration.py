"""
Integration tests: the HTTP API (FastAPI TestClient), LLM + MinIO mocked.

Covers /api/health (public), /api/extract-cv (auth + multipart)
and /api/generate-cv (auth, JSON + presigned URL response, a GeneratedCV row,
bytes handed to MinIO).
"""

from __future__ import annotations

import io

import pytest

from models import GeneratedCV


def _txt_upload(text: str = None) -> dict:
    text = text or (
        "JOHN DOE\njohn.doe@example.com | +212 600 112233\n\n"
        "WORK EXPERIENCE\nSoftware Engineer, Acme Corp\n2021 - Present\n\n"
        "EDUCATION\nMaster in Data Science, INSEA, 2024\n\n"
        "SKILLS\nPython, SQL\n"
    )
    return {"file": ("resume.txt", io.BytesIO(text.encode()), "text/plain")}


# ---------------------------------------------------------------------------
# Public monitoring endpoints
# ---------------------------------------------------------------------------

def test_health(anon_client):
    r = anon_client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "cv-generator"
    assert "gemini_configured" in body


def test_public_stats_endpoint_was_removed(anon_client):
    # Phase 6 — GET /api/stats (public LLM counters) is gone. Same data is on
    # GET /api/admin/dashboard / /api/admin/llm behind require_superadmin.
    assert anon_client.get("/api/stats").status_code == 404


def test_llm_counters_still_available_internally():
    from services.gemini_client import get_stats
    body = get_stats()
    for key in ("total_calls", "cache_hits", "total_input_chars",
                "total_output_chars", "cache_size"):
        assert key in body and isinstance(body[key], int)


# ---------------------------------------------------------------------------
# Auth is mandatory on every CV endpoint
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method, path", [
    ("post", "/api/extract-cv"),
    ("post", "/api/generate-cv"),
    ("post", "/api/generate-letter"),
    ("get", "/api/cvs"),
    ("get", "/api/letters"),
])
def test_cv_endpoints_require_a_token(anon_client, method, path):
    r = getattr(anon_client, method)(path)
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# /api/extract-cv
# ---------------------------------------------------------------------------

def test_extract_cv_returns_full_contract(client):
    r = client.post("/api/extract-cv", files=_txt_upload())
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success"
    assert body["language"] in ("fr", "en")
    for key in ("name", "email", "phone", "education", "experience",
                "projects", "skills", "languages", "certifications"):
        assert key in body["cv_profile"]
    assert set(body["confidence_scores"]) == {
        "contact", "experience", "education", "projects", "skills"
    }


def test_extract_cv_accepts_language_field(client):
    r = client.post("/api/extract-cv", files=_txt_upload(), data={"language": "en"})
    assert r.status_code == 200
    assert r.json()["language"] == "en"


def test_extract_cv_rejects_bad_language(client):
    r = client.post("/api/extract-cv", files=_txt_upload(), data={"language": "de"})
    assert r.status_code == 422


def test_extract_cv_rejects_unsupported_file_type(client):
    files = {"file": ("resume.rtf", io.BytesIO(b"whatever"), "application/rtf")}
    r = client.post("/api/extract-cv", files=files)
    assert r.status_code == 415


def test_extract_cv_rejects_empty_file(client):
    files = {"file": ("resume.txt", io.BytesIO(b""), "text/plain")}
    r = client.post("/api/extract-cv", files=files)
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /api/generate-cv  — JSON + presigned URL, stored in MinIO, row in DB
# ---------------------------------------------------------------------------

def test_generate_cv_returns_json_and_persists(client, db, test_user, cv_profile_dict, fake_minio):
    r = client.post("/api/generate-cv", json=cv_profile_dict)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success"
    assert body["language"] == "en"
    assert "x" not in body  # sanity: no header-style keys
    assert body["download_url"].startswith("http://minio.test/")
    assert isinstance(body["ats_score"], (int, float))
    assert body["expires_in"] > 0

    cv_id = body["generated_cv_id"]
    row = db.get(GeneratedCV, cv_id)
    assert row is not None and row.user_id == test_user.id

    # the rendered PDF reached MinIO, not the local disk
    assert row.storage_key in fake_minio
    assert fake_minio[row.storage_key][:4] == b"%PDF"


@pytest.mark.parametrize("language", ["fr", "en"])
def test_generate_cv_both_languages(client, cv_profile_dict, language):
    cv_profile_dict["language"] = language
    r = client.post("/api/generate-cv", json=cv_profile_dict)
    assert r.status_code == 200
    assert r.json()["language"] == language


def test_generate_cv_rejects_bad_language(client, cv_profile_dict):
    cv_profile_dict["language"] = "es"
    r = client.post("/api/generate-cv", json=cv_profile_dict)
    assert r.status_code == 422


def test_generate_cv_requires_mandatory_profile_fields(client, cv_profile_dict):
    del cv_profile_dict["cv_profile"]["email"]
    r = client.post("/api/generate-cv", json=cv_profile_dict)
    assert r.status_code == 422


def test_generate_cv_writes_nothing_to_local_disk(client, cv_profile_dict, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client.post("/api/generate-cv", json=cv_profile_dict)
    assert not any(tmp_path.rglob("*.pdf"))


# ---------------------------------------------------------------------------
# /api/optimize-existing-cv was removed in Phase 2b (functional duplicate of
# /api/generate-cv). Guard against it silently reappearing.
# ---------------------------------------------------------------------------

def test_optimize_existing_cv_is_gone(client, cv_profile_dict):
    assert client.post("/api/optimize-existing-cv", json=cv_profile_dict).status_code == 404
