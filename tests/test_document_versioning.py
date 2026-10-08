"""
LOT 8 — stable document reference + versioning.

- a CV / letter gets a stable reference (CV_xxxxxx / LETTER_xxxxxx);
- passing `reference` on a new generation adds version 2, 3 … (reference
  UNCHANGED), old versions kept;
- history is retrievable by reference; every version downloadable;
- strict owner-scoping (403 by row id, 404 by unknown reference).
"""

from __future__ import annotations

import json

import pytest

from services.documents import document_service


@pytest.fixture
def user_b(db):
    import bcrypt

    from models import User

    u = User(email="userb@example.com",
             password_hash=bcrypt.hashpw(b"Testpass123", bcrypt.gensalt()).decode())
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _gen(client, cv_profile_dict, reference=None):
    payload = dict(cv_profile_dict)
    if reference:
        payload["reference"] = reference
    r = client.post("/api/generate-cv", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def test_first_generation_mints_a_reference(client, cv_profile_dict):
    body = _gen(client, cv_profile_dict)
    assert body["reference"].startswith("CV_")
    assert body["version"] == 1


def test_regeneration_with_reference_bumps_version_keeps_reference(client, cv_profile_dict):
    v1 = _gen(client, cv_profile_dict)
    ref = v1["reference"]

    v2 = _gen(client, cv_profile_dict, reference=ref)
    v3 = _gen(client, cv_profile_dict, reference=ref)
    assert v2["reference"] == ref and v3["reference"] == ref
    assert [v2["version"], v3["version"]] == [2, 3]

    versions = client.get(f"/api/cvs/{ref}/versions").json()
    assert [v["version"] for v in versions] == [1, 2, 3]
    assert versions[-1]["is_latest"] is True
    assert versions[0]["is_latest"] is False

    # every version is still downloadable (old ones kept)
    for n in (1, 2, 3):
        dl = client.get(f"/api/cvs/{ref}/versions/{n}/download")
        assert dl.status_code == 200


def test_get_cv_by_reference_returns_latest(client, cv_profile_dict):
    v1 = _gen(client, cv_profile_dict)
    ref = v1["reference"]
    _gen(client, cv_profile_dict, reference=ref)
    got = client.get(f"/api/cvs/{ref}").json()
    assert got["version"] == 2


def test_structured_source_is_persisted(client, db, cv_profile_dict):
    from models import GeneratedCV

    body = _gen(client, cv_profile_dict)
    row = db.query(GeneratedCV).filter_by(reference=body["reference"]).one()
    src = json.loads(row.structured_source)
    assert src["language"] == "en"
    assert "experience" in src and "skills" in src


def test_unknown_reference_is_404(client):
    assert client.get("/api/cvs/CV_ZZZZZZ/versions").status_code == 404


def test_cross_user_reference_is_403(client, user_b, cv_profile_dict):
    ref = _gen(client, cv_profile_dict)["reference"]
    # user_b client
    import main
    from database import get_db
    from dependencies.auth import get_current_user
    main.app.dependency_overrides[get_current_user] = lambda: user_b
    try:
        # by reference -> latest version row exists but not owned -> 403
        assert client.get(f"/api/cvs/{ref}").status_code == 403
        assert client.get(f"/api/cvs/{ref}/versions").status_code == 403
    finally:
        main.app.dependency_overrides.pop(get_current_user, None)


def test_delete_reference_removes_all_versions(client, cv_profile_dict):
    ref = _gen(client, cv_profile_dict)["reference"]
    _gen(client, cv_profile_dict, reference=ref)
    assert client.delete(f"/api/cvs/{ref}").status_code == 204
    assert client.get(f"/api/cvs/{ref}/versions").status_code == 404


def test_db_rejects_duplicate_reference_version(db, test_user):
    """LOT 8 hardening — the UNIQUE index on (reference, version) closes the
    concurrent-insert race that next_version() alone can't."""
    from sqlalchemy.exc import IntegrityError

    from models import GeneratedCV

    db.add(GeneratedCV(id="d1", user_id=test_user.id, filename="f", storage_key="k",
                       minio_bucket="b", language="en", reference="CV_DUPE01", version=1))
    db.commit()
    db.add(GeneratedCV(id="d2", user_id=test_user.id, filename="f", storage_key="k",
                       minio_bucket="b", language="en", reference="CV_DUPE01", version=1))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_document_service_next_version_is_owner_scoped(db, test_user, cv_profile_dict):
    from models import GeneratedCV

    r = document_service.record_cv(
        db, user_id=test_user.id, cv_id="x1", filename="f", storage_key="k",
        minio_bucket="b", language="en", ats_score=None, structured_source={"a": 1},
    )
    db.commit()
    assert r.version == 1
    ref = r.reference
    assert document_service.next_version(db, test_user.id, ref, "cv") == 2
    assert document_service.next_version(db, "someone-else", ref, "cv") == 1
