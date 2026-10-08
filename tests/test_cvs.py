"""
CV / letter management routes: the owner's full lifecycle (create → list →
get → download → delete) and the cross-user 403 guard.
"""

from __future__ import annotations

import bcrypt
import pytest

from models import User


@pytest.fixture
def user_b(db):
    u = User(email="bob@example.com", password_hash=bcrypt.hashpw(b"Bobpass123", bcrypt.gensalt()).decode())
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _as(client, user):
    import main
    from dependencies.auth import get_current_user

    main.app.dependency_overrides[get_current_user] = lambda: user


def _letter_payload(cv_profile_dict):
    return {
        "language": "en",
        "cv_profile": cv_profile_dict["cv_profile"],
        "job_description": "Hiring a Python backend engineer at Globex.",
    }


# ---------------------------------------------------------------------------
# CVs
# ---------------------------------------------------------------------------

def test_cv_owner_lifecycle(client, cv_profile_dict, fake_minio):
    cv_id = client.post("/api/generate-cv", json=cv_profile_dict).json()["generated_cv_id"]

    listing = client.get("/api/cvs")
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [cv_id]

    got = client.get(f"/api/cvs/{cv_id}")
    assert got.status_code == 200 and got.json()["filename"].endswith(".pdf")

    dl = client.get(f"/api/cvs/{cv_id}/download")
    assert dl.status_code == 200 and dl.json()["download_url"].startswith("http://minio.test/")

    storage_key = f"cv/{cv_id}_en.pdf"
    assert storage_key in fake_minio
    assert client.delete(f"/api/cvs/{cv_id}").status_code == 204
    assert storage_key not in fake_minio
    assert client.get(f"/api/cvs/{cv_id}").status_code == 404


def test_cv_unknown_id_is_404(client):
    assert client.get("/api/cvs/does-not-exist").status_code == 404


def test_cv_of_another_user_is_403(client, user_b, test_user, cv_profile_dict):
    cv_id = client.post("/api/generate-cv", json=cv_profile_dict).json()["generated_cv_id"]

    _as(client, user_b)
    assert client.get(f"/api/cvs/{cv_id}").status_code == 403
    assert client.get(f"/api/cvs/{cv_id}/download").status_code == 403
    assert client.delete(f"/api/cvs/{cv_id}").status_code == 403
    assert client.get("/api/cvs").json() == []  # B sees none of A's CVs

    _as(client, test_user)  # restore


# ---------------------------------------------------------------------------
# Letters
# ---------------------------------------------------------------------------

def test_letter_owner_lifecycle_and_cross_user_403(client, user_b, test_user, cv_profile_dict, fake_minio):
    letter_id = client.post(
        "/api/generate-letter", json=_letter_payload(cv_profile_dict)
    ).json()["generated_letter_id"]

    assert [i["id"] for i in client.get("/api/letters").json()] == [letter_id]
    assert client.get(f"/api/letters/{letter_id}").status_code == 200
    assert client.get(f"/api/letters/{letter_id}/download").json()["download_url"].startswith("http://minio.test/")

    _as(client, user_b)
    assert client.get(f"/api/letters/{letter_id}").status_code == 403
    assert client.delete(f"/api/letters/{letter_id}").status_code == 403
    _as(client, test_user)

    assert client.delete(f"/api/letters/{letter_id}").status_code == 204
    assert client.get(f"/api/letters/{letter_id}").status_code == 404
