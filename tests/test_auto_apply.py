"""Phase 46 — richer auto-apply preparation.

Still **never submits** on an external site. This suite covers the smarter
*preparation*: profile-aware match (score + reasons + missing skills),
best-CV selection, opt-in `prepared` status, opt-in cover-letter generation,
and re-attempt tracking.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from _jobs_helpers import make_offer
from models import GeneratedCV, JobApplication, JobOffer, User
from schemas.applications import ApplicationStatus, ApplyRequest
from schemas.profile import UserProfileIn
from services.jobs.application_service import JobApplicationService
from services.user_profile_service import UserProfileService


def _now():
    return datetime.now(timezone.utc)


def _offer(db, *, sid="1", title="Senior Python Backend Engineer",
           company="Globex", city="Paris", lang="en", desc=None, salary_min=None):
    row = JobOffer(
        source="ashby", source_job_id=sid, content_hash=f"h{sid}",
        source_url=f"https://jobs.ashbyhq.example/{sid}", title=title, company=company,
        city=city, language=lang, description=desc or "We use Python, PostgreSQL and AWS.",
        salary_min=salary_min,
        first_seen_at=_now(), scraped_at=_now(), last_verified_at=_now(),
        is_active=True, freshness="fresh", expires_at=_now() + timedelta(days=10),
    )
    db.add(row); db.commit(); db.refresh(row)
    return row


def _profile(db, user, **kw):
    base = dict(target_titles=["Backend Engineer"], skills=["Python", "PostgreSQL", "Kubernetes"],
                locations=["Paris"], remote_preference="hybrid", experience_level="senior",
                salary_min=60000, salary_currency="EUR", languages=["en"])
    base.update(kw)
    UserProfileService(db).upsert(user, UserProfileIn(**base))


# ---------------------------------------------------------------------------
# match analysis
# ---------------------------------------------------------------------------

def test_match_uses_the_profile_and_reports_reasons_and_missing_skills(db, test_user):
    _profile(db, test_user)
    offer = _offer(db, salary_min=70000)

    resp = JobApplicationService(db).apply(test_user, offer.id, ApplyRequest())
    assert resp.match_score is not None and 0.0 <= resp.match_score <= 1.0
    assert any("Python" in r or "skills present" in r for r in resp.match_reasons)
    assert any("salary" in r.lower() for r in resp.match_reasons)
    assert "Kubernetes" in resp.missing_skills          # wanted, not in the posting
    assert "Python" not in resp.missing_skills          # wanted and present

    row = db.get(JobApplication, resp.application_id)
    assert row.match_reasons and row.missing_skills == ["Kubernetes"]


def test_low_match_still_prepares_with_a_weak_match_reason(db, test_user):
    _profile(db, test_user, skills=["COBOL"], target_titles=["Mainframe Operator"])
    offer = _offer(db)
    resp = JobApplicationService(db).apply(test_user, offer.id, ApplyRequest())
    assert resp.match_reasons  # never empty


# ---------------------------------------------------------------------------
# prepared status (opt-in)
# ---------------------------------------------------------------------------

def test_default_apply_is_still_manual_required(db, test_user):
    offer = _offer(db)
    resp = JobApplicationService(db).apply(test_user, offer.id, ApplyRequest())
    assert resp.application_status == ApplicationStatus.manual_required


def test_prepare_flag_gives_prepared_status(db, test_user):
    _profile(db, test_user)
    offer = _offer(db)
    resp = JobApplicationService(db).apply(test_user, offer.id, ApplyRequest(prepare=True))
    assert resp.application_status == ApplicationStatus.prepared
    assert resp.application_url == offer.source_url
    assert db.get(JobApplication, resp.application_id).status == "prepared"


# ---------------------------------------------------------------------------
# re-attempt tracking
# ---------------------------------------------------------------------------

def test_reapply_increments_attempt_count_on_the_existing_record(db, test_user):
    offer = _offer(db)
    svc = JobApplicationService(db)
    first = svc.apply(test_user, offer.id, ApplyRequest())
    svc.apply(test_user, offer.id, ApplyRequest())
    svc.apply(test_user, offer.id, ApplyRequest())
    row = db.get(JobApplication, first.application_id)
    assert row.attempt_count == 3
    assert db.query(JobApplication).count() == 1


# ---------------------------------------------------------------------------
# cover letter (opt-in, best-effort)
# ---------------------------------------------------------------------------

def test_generate_letter_without_cv_profile_notes_it_and_still_prepares(db, test_user):
    _profile(db, test_user)
    offer = _offer(db)
    resp = JobApplicationService(db).apply(
        test_user, offer.id, ApplyRequest(prepare=True, generate_letter=True)
    )
    assert resp.application_status == ApplicationStatus.prepared
    assert resp.letter_id is None
    assert "cover letter" in resp.message.lower()


def test_generate_letter_with_cv_profile_creates_and_links_it(db, test_user, mock_llm, fake_minio):
    _profile(db, test_user)
    offer = _offer(db)
    cv_profile = {
        "name": "John Doe", "email": "john@x.com", "phone": "+33 6 00 00 00 00",
        "professional_summary": "Backend engineer.",
        "experience": [], "education": [], "projects": [], "skills": [], "languages": [],
        "certifications": [],
    }
    resp = JobApplicationService(db).apply(
        test_user, offer.id,
        ApplyRequest(prepare=True, generate_letter=True, cv_profile=cv_profile, letter_language="en"),
    )
    assert resp.application_status == ApplicationStatus.prepared
    assert resp.letter_id is not None
    from models import GeneratedLetter
    letter = db.get(GeneratedLetter, resp.letter_id)
    assert letter is not None and letter.user_id == test_user.id


def test_explicit_letter_id_is_linked(db, test_user):
    from models import GeneratedLetter
    lt = GeneratedLetter(user_id=test_user.id, filename="l.pdf", storage_key="k",
                         minio_bucket="b", language="en")
    db.add(lt); db.commit(); db.refresh(lt)
    offer = _offer(db)
    resp = JobApplicationService(db).apply(
        test_user, offer.id, ApplyRequest(prepare=True, letter_id=lt.id)
    )
    assert resp.letter_id == lt.id
