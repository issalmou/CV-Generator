"""Phase 6 — the job-aware conversation agent.

`POST /api/conversations/{id}/messages` (no CV_/LETTER_ ref):
- chat            → just a reply
- search_jobs     → runs JobSearchService INTERNALLY, persists the selection
- apply_jobs      → PROPOSES only; a deterministic "yes" on the next turn applies
- request_information → a real question, never a mutation

The LLM can never trigger an application.
"""

from __future__ import annotations

import json

import bcrypt
import pytest
from fastapi.testclient import TestClient

import main
from _jobs_helpers import FakeJobProvider, make_offer as _offer
from database import get_db
from dependencies.auth import get_current_user
from models import Conversation, JobApplication, SavedJob, User


def _user(db, email="u@x.com"):
    u = User(email=email, password_hash=bcrypt.hashpw(b"Passw0rd!", bcrypt.gensalt()).decode())
    db.add(u); db.commit(); db.refresh(u)
    return u


def _conv(db, user):
    c = Conversation(user_id=user.id)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _client(db, user):
    main.app.dependency_overrides[get_db] = lambda: db
    main.app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _clean():
    yield
    main.app.dependency_overrides.clear()


def _directive(**kw):
    base = {"reply": "", "intent": "chat", "search_patch": None, "apply": None, "question": None}
    base.update(kw)
    return json.dumps(base)


def _route(mock_llm, directive_json):
    """Make the conversation_agent turn return `directive_json`, everything else default."""
    from tests.conftest import _default_llm_router

    def r(prompt, *, request_type, use_cache=True, **kw):
        if request_type == "conversation_agent":
            return directive_json
        return _default_llm_router(prompt, request_type=request_type, use_cache=use_cache)
    mock_llm.set(r)


def _send(client, conv_id, text):
    return client.post(f"/api/conversations/{conv_id}/messages", json={"content": text})


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------

def test_plain_chat_turn_just_replies(db, mock_llm):
    u = _user(db); c = _conv(db, u)
    _route(mock_llm, _directive(reply="Your summary looks solid.", intent="chat"))
    body = _send(_client(db, u), c.id, "is my summary good?").json()
    assert body["assistant_message"]["content"] == "Your summary looks solid."
    assert body["jobs_found"] == [] and body["applications"] == []
    assert body["pending_confirmation"] is None
    assert db.query(SavedJob).count() == 0


# ---------------------------------------------------------------------------
# search — runs JobSearchService internally
# ---------------------------------------------------------------------------

def test_search_runs_the_internal_service_and_persists_the_selection(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [
        _offer(sid="s1", title="Backend Python Engineer", company="Globex"),
        _offer(sid="s2", title="Senior Backend Engineer", company="Initech"),
    ])])
    _route(mock_llm, _directive(
        reply="I found some roles.", intent="search_jobs",
        search_patch={"query": "backend python", "remote_type": "remote", "country": "France"},
    ))

    body = _send(_client(db, u), c.id,
                 "Trouve-moi des postes Backend Python à distance en France").json()
    assert body["assistant_message"]["content"]
    assert len(body["jobs_found"]) == 2
    titles = {j["title"] for j in body["jobs_found"]}
    assert "Backend Python Engineer" in titles
    # persisted as an agent selection, owner-scoped, tied to the conversation
    rows = db.query(SavedJob).filter_by(user_id=u.id).all()
    assert len(rows) == 2
    assert all(r.origin == "agent" and r.conversation_id == c.id for r in rows)


def test_search_results_show_up_on_the_dashboard(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [_offer(sid="d1", title="Data Engineer")])])
    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "data"}))
    _send(_client(db, u), c.id, "cherche data engineer").json()

    dash = _client(db, u).get("/api/dashboard/jobs").json()
    assert dash["total"] == 1
    assert dash["jobs"][0]["title"] == "Data Engineer"
    assert dash["jobs"][0]["application"] is None      # Apply button available


# ---------------------------------------------------------------------------
# apply — proposal only, never a single-turn action
# ---------------------------------------------------------------------------

def _seed_selection(db, user, conv, *, provider_offers):
    """Run a search turn to populate the selection, return the offer rows."""
    from services.jobs.search_service import JobSearchService
    from schemas.jobs import JobSearchRequest, JobSearchContext
    # persist offers via the real service (same path the agent uses)
    resp = JobSearchService(db).search(JobSearchRequest(context=JobSearchContext(query="x")))
    ids = [r.id for r in resp.results]
    for oid in ids:
        db.add(SavedJob(user_id=user.id, job_offer_id=oid, origin="agent", conversation_id=conv.id))
    db.commit()
    return ids


def test_apply_intent_only_proposes_nothing_is_applied(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [
        _offer(sid="a", title="Backend Engineer"), _offer(sid="b", title="Data Engineer")])])
    ids = _seed_selection(db, u, c, provider_offers=None)

    _route(mock_llm, _directive(reply="I'll apply to all of them.", intent="apply_jobs",
                                apply={"scope": "all", "job_ids": [], "filter": None}))
    body = _send(_client(db, u), c.id, "postule à toutes les offres sélectionnées").json()

    assert body["pending_confirmation"] is not None
    assert set(body["pending_confirmation"]["job_ids"]) == set(ids)
    assert body["applications"] == []
    assert db.query(JobApplication).count() == 0            # NOTHING applied
    assert any(a["type"] == "apply_to_selected_jobs" for a in body["recommended_actions"])
    # frozen on the conversation
    db.refresh(c)
    assert c.pending_action["kind"] == "apply"


def test_confirmation_turn_applies_exactly_the_frozen_set(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [
        _offer(sid="a", title="Backend Engineer"), _offer(sid="b", title="Data Engineer")])])
    ids = _seed_selection(db, u, c, provider_offers=None)
    client = _client(db, u)

    _route(mock_llm, _directive(intent="apply_jobs", apply={"scope": "all", "job_ids": [], "filter": None}))
    _send(client, c.id, "postule à toutes").json()

    # a deterministic "oui" — the LLM is NOT consulted for this branch
    def _boom_llm(*a, **k):
        raise AssertionError("the LLM must not be called on a confirmation turn")
    mock_llm.set(_boom_llm)
    body = _send(client, c.id, "oui").json()

    assert len(body["applications"]) == 2
    assert {a["job_offer_id"] for a in body["applications"]} == set(ids)
    assert db.query(JobApplication).filter_by(user_id=u.id).count() == 2
    db.refresh(c)
    assert c.pending_action is None                          # cleared


def test_apply_to_specific_positions_after_confirm(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [
        _offer(sid="a", title="A"), _offer(sid="b", title="B"), _offer(sid="c", title="C")])])
    ids = _seed_selection(db, u, c, provider_offers=None)   # newest-first order preserved
    client = _client(db, u)

    _route(mock_llm, _directive(intent="apply_jobs",
                                apply={"scope": "ids", "job_ids": ["1", "3"], "filter": None}))
    prop = _send(client, c.id, "postule aux offres 1 et 3").json()
    assert len(prop["pending_confirmation"]["job_ids"]) == 2

    _route(mock_llm, _directive(reply="unused", intent="chat"))
    _send(client, c.id, "oui").json()
    applied = {a.job_offer_id for a in db.query(JobApplication).filter_by(user_id=u.id)}
    assert applied == {ids[0], ids[2]}                       # positions 1 and 3


def test_non_confirmation_clears_the_pending_proposal(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [_offer(sid="a", title="A")])])
    _seed_selection(db, u, c, provider_offers=None)
    client = _client(db, u)

    _route(mock_llm, _directive(intent="apply_jobs", apply={"scope": "all", "job_ids": [], "filter": None}))
    _send(client, c.id, "postule").json()
    db.refresh(c); assert c.pending_action is not None

    _route(mock_llm, _directive(reply="Sure, tell me more.", intent="chat"))
    _send(client, c.id, "actually wait, what's the salary on the first one?").json()
    db.refresh(c)
    assert c.pending_action is None
    assert db.query(JobApplication).count() == 0


def test_partial_failure_does_not_abort_the_rest(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [_offer(sid="ok", title="Good Job")])])
    ids = _seed_selection(db, u, c, provider_offers=None)
    # freeze a set with one real offer + one bogus id
    c.pending_action = {"kind": "apply", "job_ids": [ids[0], "does-not-exist"],
                        "proposed_at": __import__("datetime").datetime.now(
                            __import__("datetime").timezone.utc).isoformat()}
    db.commit()

    _route(mock_llm, _directive(reply="unused", intent="chat"))
    body = _send(_client(db, u), c.id, "confirme").json()
    outcomes = {o["job_offer_id"]: o["application_status"] for o in body["applications"]}
    assert outcomes[ids[0]] in ("prepared", "manual_required")
    assert outcomes["does-not-exist"] == "failed"
    assert db.query(JobApplication).filter_by(user_id=u.id).count() == 1   # the good one landed


def test_user_b_cannot_confirm_user_a_pending(db, mock_llm, swap_providers):
    a, b = _user(db, "a@x.com"), _user(db, "b@x.com")
    ca = _conv(db, a)
    swap_providers([FakeJobProvider("arbeitnow", [_offer(sid="a", title="A")])])
    _seed_selection(db, a, ca, provider_offers=None)
    ca.pending_action = {"kind": "apply", "job_ids": ["x"],
                         "proposed_at": __import__("datetime").datetime.now(
                             __import__("datetime").timezone.utc).isoformat()}
    db.commit()

    # B tries to post into A's conversation → 403, nothing happens
    r = _client(db, b).post(f"/api/conversations/{ca.id}/messages", json={"content": "oui"})
    assert r.status_code == 403
    assert db.query(JobApplication).count() == 0


def test_apply_with_no_selection_is_a_helpful_reply_not_an_error(db, mock_llm):
    u = _user(db); c = _conv(db, u)
    _route(mock_llm, _directive(intent="apply_jobs", apply={"scope": "all", "job_ids": [], "filter": None}))
    body = _send(_client(db, u), c.id, "postule à tout").json()
    assert body["pending_confirmation"] is None
    assert "search" in body["assistant_message"]["content"].lower()
    assert db.query(JobApplication).count() == 0


# ---------------------------------------------------------------------------
# request_information — a question, never an execution
# ---------------------------------------------------------------------------

def test_request_information_surfaces_a_real_question(db, mock_llm):
    u = _user(db); c = _conv(db, u)
    _route(mock_llm, _directive(
        reply="One thing before I search:", intent="request_information",
        question={"topic": "Kubernetes",
                  "question": "The job wants 5 years of Kubernetes — do you use it, and since when?",
                  "reason": "Kubernetes is required but not in your profile."},
    ))
    body = _send(_client(db, u), c.id, "postule au poste DevOps").json()
    ra = body["recommended_actions"]
    assert len(ra) == 1 and ra[0]["type"] == "request_information"
    assert "5 years of Kubernetes" in ra[0]["question"]
    assert body["applications"] == [] and body["pending_confirmation"] is None
    assert db.query(JobApplication).count() == 0


# ---------------------------------------------------------------------------
# degradation
# ---------------------------------------------------------------------------

def test_unparseable_directive_degrades_to_a_safe_reply(db, mock_llm):
    u = _user(db); c = _conv(db, u)

    def r(prompt, *, request_type, use_cache=True, **kw):
        if request_type == "conversation_agent":
            return "not json at all"
        return "{}"
    mock_llm.set(r)
    body = _send(_client(db, u), c.id, "do something").json()
    assert body["assistant_message"]["content"]              # a reply, not a 500
    assert db.query(JobApplication).count() == 0


def test_llm_down_still_returns_429_and_keeps_the_user_message(db, mock_llm):
    u = _user(db); c = _conv(db, u)

    def _down(prompt, *, request_type="generic", use_cache=True, **kw):
        raise RuntimeError("No LLM model available.")
    mock_llm.set(_down)
    r = _send(_client(db, u), c.id, "hello")
    assert r.status_code == 429
    from models import Message
    rows = db.query(Message).filter_by(conversation_id=c.id).all()
    assert [m.role for m in rows] == ["user"]
