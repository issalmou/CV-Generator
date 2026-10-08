"""Phase 27 — auto-apply: CV auto-selection, duplicate prevention, traceability.

The service still **never submits** an application to an external site — this
suite only covers the record it prepares and the metadata it captures.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from _jobs_helpers import make_offer
from models import GeneratedCV, JobApplication, JobOffer, User
from schemas.applications import ApplicationStatus, ApplyRequest
from services.jobs.application_service import JobApplicationService, normalized_job_key


def _now():
    return datetime.now(timezone.utc)


def _store_offer(db, *, sid="1", company="Globex", title="Data Engineer",
                 city="Paris", lang="en", url=None, active=True):
    row = JobOffer(
        source="ashby", source_job_id=sid, content_hash=f"h-{sid}",
        source_url=url or f"https://jobs.ashby.example/{sid}",
        title=title, company=company, city=city, language=lang,
        first_seen_at=_now(), scraped_at=_now(), last_verified_at=_now(),
        is_active=active, freshness="fresh" if active else "expired",
        expires_at=_now() + timedelta(days=10),
    )
    db.add(row)
    db.commit()
    return row


def _cv(db, user, *, lang="en", ats=50.0, days_old=0):
    cv = GeneratedCV(
        user_id=user.id, filename=f"cv_{lang}_{ats}.pdf",
        storage_key=f"cv/{lang}-{ats}.pdf", minio_bucket="cv-files",
        language=lang, ats_score=ats,
        created_at=_now() - timedelta(days=days_old),
    )
    db.add(cv)
    db.commit()
    return cv


# ---------------------------------------------------------------------------
# CV auto-selection
# ---------------------------------------------------------------------------

def test_best_cv_prefers_language_match(db, test_user):
    _cv(db, test_user, lang="en", ats=95.0)          # higher ATS, wrong language
    fr_cv = _cv(db, test_user, lang="fr", ats=40.0)  # lower ATS, right language
    offer = _store_offer(db, lang="fr")

    resp = JobApplicationService(db).apply(test_user, offer.id, ApplyRequest())
    assert resp.cv_id == fr_cv.id


def test_best_cv_falls_back_to_ats_then_recency(db, test_user):
    _cv(db, test_user, lang="en", ats=60.0, days_old=1)
    best = _cv(db, test_user, lang="en", ats=90.0, days_old=5)
    offer = _store_offer(db, lang="en")

    resp = JobApplicationService(db).apply(test_user, offer.id, ApplyRequest())
    assert resp.cv_id == best.id


def test_explicit_cv_id_is_respected(db, test_user):
    a = _cv(db, test_user, lang="en", ats=90.0)
    b = _cv(db, test_user, lang="en", ats=10.0)
    offer = _store_offer(db, lang="en")

    resp = JobApplicationService(db).apply(test_user, offer.id, ApplyRequest(cv_id=b.id))
    assert resp.cv_id == b.id


def test_no_cv_on_file_is_not_an_error(db, test_user):
    offer = _store_offer(db)
    resp = JobApplicationService(db).apply(test_user, offer.id, ApplyRequest())
    assert resp.cv_id is None
    assert resp.application_status == ApplicationStatus.manual_required


# ---------------------------------------------------------------------------
# duplicate prevention
# ---------------------------------------------------------------------------

def test_second_apply_to_same_offer_returns_the_existing_record(db, test_user):
    offer = _store_offer(db)
    svc = JobApplicationService(db)
    first = svc.apply(test_user, offer.id, ApplyRequest())
    second = svc.apply(test_user, offer.id, ApplyRequest())

    assert second.application_status == ApplicationStatus.duplicate
    assert second.is_duplicate is True
    assert second.application_id == first.application_id
    assert db.query(JobApplication).count() == 1


def test_duplicate_detected_across_two_offer_rows_for_the_same_posting(db, test_user):
    a = _store_offer(db, sid="a", company="Globex", title="Data Engineer", city="Paris")
    b = _store_offer(db, sid="b", company="Globex", title="Data Engineer", city="Paris")
    assert normalized_job_key(a) == normalized_job_key(b)

    svc = JobApplicationService(db)
    svc.apply(test_user, a.id, ApplyRequest())
    second = svc.apply(test_user, b.id, ApplyRequest())
    assert second.application_status == ApplicationStatus.duplicate
    assert db.query(JobApplication).count() == 1


def test_different_users_can_apply_to_the_same_posting(db, test_user):
    import bcrypt
    other = User(email="c@example.com",
                 password_hash=bcrypt.hashpw(b"Cpass1234", bcrypt.gensalt()).decode())
    db.add(other)
    db.commit()
    offer = _store_offer(db)

    svc = JobApplicationService(db)
    svc.apply(test_user, offer.id, ApplyRequest())
    svc.apply(other, offer.id, ApplyRequest())
    assert db.query(JobApplication).count() == 2


# ---------------------------------------------------------------------------
# traceability
# ---------------------------------------------------------------------------

def test_application_row_captures_provenance(db, test_user):
    offer = _store_offer(db, title="Senior Python Engineer")
    resp = JobApplicationService(db).apply(
        test_user, offer.id, ApplyRequest(context="Looking for a senior python role"),
    )
    row = db.get(JobApplication, resp.application_id)
    assert row.source == "ashby"
    assert row.normalized_job_key == normalized_job_key(offer)
    assert row.match_score is not None and 0.0 <= row.match_score <= 1.0
    assert row.attempt_count == 1
    assert row.last_attempt_at is not None
    assert row.duration_ms is not None and row.duration_ms >= 0


def test_expired_offer_records_failure_detail(db, test_user):
    offer = _store_offer(db, active=False)
    resp = JobApplicationService(db).apply(test_user, offer.id, ApplyRequest())
    assert resp.application_status == ApplicationStatus.unavailable
    row = db.get(JobApplication, resp.application_id)
    assert row.error_detail and "no longer active" in row.error_detail


# ---------------------------------------------------------------------------
# additive-migration helper  (fully isolated engine — never touches the shared DB)
# ---------------------------------------------------------------------------

def test_ensure_additive_columns_adds_missing_columns_and_indexes(tmp_path, monkeypatch):
    """An old deployment created `job_applications` with the v2.4 column set.
    `_ensure_additive_columns` must add every column/index the model gained
    since, without dropping or altering anything, and be idempotent."""
    from sqlalchemy import create_engine, inspect, text
    import database as dbmod

    old_engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    # the pre-Phase-27 shape of the table
    with old_engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE job_applications ("
            " id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL,"
            " job_offer_id VARCHAR(36) NOT NULL, cv_id VARCHAR(36),"
            " letter_id VARCHAR(36), status VARCHAR(32) NOT NULL,"
            " application_url VARCHAR(1024), context JSON, answers JSON,"
            " created_at DATETIME, updated_at DATETIME)"
        ))
        conn.execute(text(
            "INSERT INTO job_applications (id, user_id, job_offer_id, status)"
            " VALUES ('a1', 'u1', 'o1', 'manual_required')"
        ))

    monkeypatch.setattr(dbmod, "engine", old_engine)
    dbmod._ensure_additive_columns()
    dbmod._ensure_additive_columns()  # idempotent

    insp = inspect(old_engine)
    cols = {c["name"] for c in insp.get_columns("job_applications")}
    assert {"source", "normalized_job_key", "match_score", "error_detail",
            "attempt_count", "last_attempt_at", "duration_ms"} <= cols
    idx = {i["name"] for i in insp.get_indexes("job_applications")}
    assert "ix_job_applications_user_normkey" in idx
    # existing row untouched
    with old_engine.begin() as conn:
        row = conn.execute(text("SELECT status, duration_ms FROM job_applications")).one()
        assert row[0] == "manual_required" and row[1] is None
    old_engine.dispose()
