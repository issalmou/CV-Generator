"""Job application service + API — ownership, freshness, never-submitted."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import pytest

from _jobs_helpers import FakeJobProvider, make_offer
from models import GeneratedCV, GeneratedLetter, JobApplication, JobOffer, User
from services.providers.base import ApplicationMethod


@pytest.fixture
def stored_offer(db):
    offer = make_offer("arbeitnow", "1", url="https://arbeitnow.example/1")
    row = JobOffer(
        source=offer.source, source_job_id=offer.source_job_id, content_hash="h1",
        source_url=offer.source_url, title=offer.title, company=offer.company,
        first_seen_at=datetime.now(timezone.utc), scraped_at=datetime.now(timezone.utc),
        last_verified_at=datetime.now(timezone.utc), is_active=True, freshness="fresh",
        expires_at=datetime.now(timezone.utc) + timedelta(days=10),
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def expired_offer(db):
    row = JobOffer(
        source="arbeitnow", source_job_id="2", content_hash="h2",
        source_url="https://arbeitnow.example/2", title="Old Role",
        first_seen_at=datetime.now(timezone.utc), scraped_at=datetime.now(timezone.utc),
        last_verified_at=datetime.now(timezone.utc), is_active=False, freshness="expired",
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def user_cv(db, test_user):
    cv = GeneratedCV(user_id=test_user.id, filename="cv.pdf", storage_key="cv/1_en.pdf",
                     minio_bucket="cv-files", language="en", ats_score=80.0)
    db.add(cv)
    db.commit()
    return cv


@pytest.fixture
def other_user_cv(db):
    other = User(email="bob@example.com",
                 password_hash=bcrypt.hashpw(b"Bobpass123", bcrypt.gensalt()).decode())
    db.add(other)
    db.commit()
    cv = GeneratedCV(user_id=other.id, filename="cv.pdf", storage_key="cv/x_en.pdf",
                     minio_bucket="cv-files", language="en")
    db.add(cv)
    db.commit()
    return cv


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

def test_apply_unauthenticated_401(anon_client, stored_offer):
    assert anon_client.post(f"/api/jobs/{stored_offer.id}/apply", json={}).status_code == 401


def test_apply_unknown_job_404(client):
    assert client.post("/api/jobs/nope/apply", json={}).status_code == 404


def test_apply_returns_manual_required_with_external_url(client, swap_providers, stored_offer):
    swap_providers([FakeJobProvider("arbeitnow")])
    r = client.post(f"/api/jobs/{stored_offer.id}/apply", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["application_status"] == "manual_required"
    assert body["application_url"] == stored_offer.source_url
    assert "submitted" not in body["application_status"]


def test_apply_platform_login_required(client, swap_providers, stored_offer):
    swap_providers([FakeJobProvider(
        "arbeitnow",
        application=ApplicationMethod("platform_login_required", stored_offer.source_url),
    )])
    r = client.post(f"/api/jobs/{stored_offer.id}/apply", json={})
    assert r.json()["application_status"] == "requires_user_action"


def test_apply_expired_offer_is_unavailable(client, swap_providers, expired_offer):
    swap_providers([FakeJobProvider("arbeitnow")])
    r = client.post(f"/api/jobs/{expired_offer.id}/apply", json={})
    assert r.status_code == 200
    assert r.json()["application_status"] == "unavailable"


def test_apply_never_reports_submitted(client, swap_providers, stored_offer):
    swap_providers([FakeJobProvider("arbeitnow")])
    r = client.post(f"/api/jobs/{stored_offer.id}/apply", json={"context": "ready in October"})
    assert r.json()["application_status"] in (
        "manual_required", "requires_user_action", "unavailable", "failed",
    )


def test_apply_with_cv_of_another_user_is_403(client, swap_providers, stored_offer, other_user_cv):
    swap_providers([FakeJobProvider("arbeitnow")])
    r = client.post(f"/api/jobs/{stored_offer.id}/apply", json={"cv_id": other_user_cv.id})
    assert r.status_code == 403


def test_apply_stores_context_cv_and_letter(client, swap_providers, db, stored_offer,
                                            user_cv, test_user):
    letter = GeneratedLetter(user_id=test_user.id, filename="l.pdf", storage_key="letter/1_en.pdf",
                             minio_bucket="cv-files", language="en")
    db.add(letter)
    db.commit()
    swap_providers([FakeJobProvider("arbeitnow")])
    r = client.post(f"/api/jobs/{stored_offer.id}/apply", json={
        "cv_id": user_cv.id, "letter_id": letter.id,
        "context": "Available from October; highlight Python experience.",
        "answers": {"why": "I love data"},
    })
    app_id = r.json()["application_id"]
    record = db.get(JobApplication, app_id)
    assert record.cv_id == user_cv.id and record.letter_id == letter.id
    assert "October" in record.context["notes"]
    assert record.answers["why"] == "I love data"

    detail = client.get(f"/api/jobs/applications/{app_id}")
    assert detail.status_code == 200
    assert detail.json()["context"]["notes"].startswith("Available from October")


# ---------------------------------------------------------------------------
# application history — ownership
# ---------------------------------------------------------------------------

def test_application_list_is_owner_scoped(client, swap_providers, db, stored_offer, test_user):
    swap_providers([FakeJobProvider("arbeitnow")])
    client.post(f"/api/jobs/{stored_offer.id}/apply", json={})

    other = User(email="carol@example.com",
                 password_hash=bcrypt.hashpw(b"Carolpass1", bcrypt.gensalt()).decode())
    db.add(other)
    db.commit()
    db.add(JobApplication(user_id=other.id, job_offer_id=stored_offer.id, status="manual_required"))
    db.commit()

    listing = client.get("/api/jobs/applications").json()
    assert len(listing) == 1
    assert all(a["job_offer_id"] == stored_offer.id for a in listing)


def test_get_application_of_another_user_is_403(client, db, stored_offer):
    other = User(email="dave@example.com",
                 password_hash=bcrypt.hashpw(b"Davepass12", bcrypt.gensalt()).decode())
    db.add(other)
    db.commit()
    rec = JobApplication(user_id=other.id, job_offer_id=stored_offer.id, status="manual_required")
    db.add(rec)
    db.commit()
    assert client.get(f"/api/jobs/applications/{rec.id}").status_code == 403


# ---------------------------------------------------------------------------
# saved jobs
# ---------------------------------------------------------------------------

def test_save_is_idempotent_and_owner_scoped(client, db, stored_offer):
    assert client.post(f"/api/jobs/{stored_offer.id}/save").status_code == 204
    assert client.post(f"/api/jobs/{stored_offer.id}/save").status_code == 204   # idempotent

    saved = client.get("/api/jobs/saved").json()
    assert [s["id"] for s in saved] == [stored_offer.id]

    assert client.delete(f"/api/jobs/{stored_offer.id}/save").status_code == 204
    assert client.get("/api/jobs/saved").json() == []


def test_save_unknown_job_404(client):
    assert client.post("/api/jobs/nope/save").status_code == 404
