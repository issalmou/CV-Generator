"""Phase 33 — superadmin provider statistics (`GET /api/admin/providers`)."""

from __future__ import annotations

import bcrypt
import pytest
from fastapi.testclient import TestClient

import main
from _jobs_helpers import FakeJobProvider, make_offer
from database import get_db
from dependencies.auth import get_current_user
from models import User
from schemas.jobs import JobSearchContext, JobSearchRequest, ProviderState
from services.jobs.search_service import JobSearchService


def _admin(db):
    u = User(email="a@x.com", is_superadmin=True,
             password_hash=bcrypt.hashpw(b"Passw0rd!", bcrypt.gensalt()).decode())
    db.add(u); db.commit(); db.refresh(u)
    return u


def _client(db, user):
    main.app.dependency_overrides[get_db] = lambda: db
    main.app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _clean():
    yield
    main.app.dependency_overrides.clear()


def test_normal_user_forbidden(db, test_user):
    assert _client(db, test_user).get("/api/admin/providers").status_code == 403


def test_lists_every_registered_provider(db):
    r = _client(db, _admin(db)).get("/api/admin/providers")
    assert r.status_code == 200
    rows = r.json()
    names = {row["name"] for row in rows}
    assert {"linkedin", "ashby", "greenhouse", "indeed"} <= names
    for row in rows:
        assert "success_rate" in row and "p95_duration_ms" in row and "health_note" in row


def test_stats_reflect_a_run(db, swap_providers):
    ok = FakeJobProvider("arbeitnow", [make_offer("arbeitnow", "1"), make_offer("arbeitnow", "2")])
    bad = FakeJobProvider("linkedin", state=ProviderState.error)
    swap_providers([ok, bad])

    JobSearchService(db).search(JobSearchRequest(context=JobSearchContext(query="x")))

    rows = {r["name"]: r for r in _client(db, _admin(db)).get("/api/admin/providers").json()}
    assert rows["arbeitnow"]["runs"] >= 1
    assert rows["arbeitnow"]["success"] >= 1
    assert rows["arbeitnow"]["total_offers_collected"] >= 2
    assert rows["arbeitnow"]["last_duration_ms"] is not None
    assert rows["linkedin"]["failure"] >= 1
    assert rows["linkedin"]["success_rate"] == 0.0 or rows["linkedin"]["runs"] == rows["linkedin"]["failure"]


def test_no_url_or_token_in_last_error(db, swap_providers):
    bad = FakeJobProvider("indeed", state=ProviderState.blocked)
    swap_providers([bad])
    JobSearchService(db).search(JobSearchRequest(context=JobSearchContext(query="x")))
    row = next(r for r in _client(db, _admin(db)).get("/api/admin/providers").json()
               if r["name"] == "indeed")
    assert row["last_error"] is None or "http" not in (row["last_error"] or "").lower()
