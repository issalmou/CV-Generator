"""Phase 6 finalisation:

- deterministic FR/EN "oui/non" · "yes/no" confirmation, explicit and never
  mixed, appended in code (never left to the LLM to phrase);
- conversational job listing paginated 5 / 5 / 5, backed by
  ``Conversation.job_browse_state`` — never a new search, never a re-rank,
  never a duplicate, stable order;
- a displayed ordinal ("offre 1") resolves to the REAL job offer UUID (not
  the literal "1") before it is ever frozen into ``pending_action``.
"""

from __future__ import annotations

import json
import uuid

import bcrypt
import pytest

import main
from _jobs_helpers import FakeJobProvider, make_offer as _offer
from database import get_db
from dependencies.auth import get_current_user
from models import Conversation, JobApplication, User


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
    from fastapi.testclient import TestClient
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
    from tests.conftest import _default_llm_router

    def r(prompt, *, request_type, use_cache=True, **kw):
        if request_type == "conversation_agent":
            return directive_json
        return _default_llm_router(prompt, request_type=request_type, use_cache=use_cache)
    mock_llm.set(r)


def _send(client, conv_id, text):
    return client.post(f"/api/conversations/{conv_id}/messages", json={"content": text})


# ---------------------------------------------------------------------------
# Confirmation — explicit oui/non (FR) and yes/no (EN), deterministic
# ---------------------------------------------------------------------------

def test_apply_proposal_is_french_with_explicit_oui_non(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [_offer(sid="a", title="Ingenieur Backend")])])
    client = _client(db, u)

    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "backend"}))
    _send(client, c.id, "trouve-moi un poste de developpeur backend a Paris")

    _route(mock_llm, _directive(intent="apply_jobs", apply={"scope": "all", "job_ids": [], "filter": None}))
    body = _send(client, c.id, "Peux-tu postuler a cette offre pour moi, s'il te plait ?").json()
    reply = body["assistant_message"]["content"].lower()

    assert "oui" in reply and "non" in reply
    assert "répondez" in reply.lower() or "repondez" in reply.lower() or "continuer" in reply.lower()
    # never a mixed-language instruction
    assert "yes" not in reply and " no " not in f" {reply} "


def test_apply_proposal_is_english_with_explicit_yes_no(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [_offer(sid="a", title="Backend Engineer")])])
    client = _client(db, u)

    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "backend"}))
    _send(client, c.id, "find me a backend engineer role in Paris")

    _route(mock_llm, _directive(intent="apply_jobs", apply={"scope": "all", "job_ids": [], "filter": None}))
    body = _send(client, c.id, "Can you apply to this job for me please?").json()
    reply = body["assistant_message"]["content"].lower()

    assert "yes" in reply and "no" in reply
    assert "continue" in reply
    # never a mixed-language instruction
    assert "oui" not in reply and "non" not in reply


@pytest.mark.parametrize("message,should_confirm", [
    ("oui", True),
    ("Oui !", True),
    ("confirme", True),
    ("oui mais attends", False),
    ("oui, attends", False),
    ("oui mais", False),
    ("non", False),
    ("non merci", False),
])
def test_french_confirmation_matrix(db, mock_llm, swap_providers, message, should_confirm):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [_offer(sid="a", title="A")])])
    client = _client(db, u)
    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "x"}))
    _send(client, c.id, "cherche un role")

    _route(mock_llm, _directive(intent="apply_jobs", apply={"scope": "all", "job_ids": [], "filter": None}))
    _send(client, c.id, "postule a cette offre")

    _route(mock_llm, _directive(reply="Sure.", intent="chat"))
    body = _send(client, c.id, message).json()

    if should_confirm:
        assert len(body["applications"]) == 1
        assert db.query(JobApplication).filter_by(user_id=u.id).count() == 1
    else:
        assert body["applications"] == []
        assert db.query(JobApplication).filter_by(user_id=u.id).count() == 0


@pytest.mark.parametrize("message,should_confirm", [
    ("yes", True),
    ("Yes!", True),
    ("confirm", True),
    ("yes but wait", False),
    ("yes, before that", False),
    ("no", False),
    ("no thanks", False),
])
def test_english_confirmation_matrix(db, mock_llm, swap_providers, message, should_confirm):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [_offer(sid="a", title="A")])])
    client = _client(db, u)
    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "x"}))
    _send(client, c.id, "search for a role")

    _route(mock_llm, _directive(intent="apply_jobs", apply={"scope": "all", "job_ids": [], "filter": None}))
    _send(client, c.id, "apply to this job")

    _route(mock_llm, _directive(reply="Sure.", intent="chat"))
    body = _send(client, c.id, message).json()

    if should_confirm:
        assert len(body["applications"]) == 1
        assert db.query(JobApplication).filter_by(user_id=u.id).count() == 1
    else:
        assert body["applications"] == []
        assert db.query(JobApplication).filter_by(user_id=u.id).count() == 0


# ---------------------------------------------------------------------------
# Pagination — 5 / 5 / 5, stable order, no duplicates, no new search
# ---------------------------------------------------------------------------

def _offers(n, prefix="p"):
    # distinct company per offer — same-company-same-city offers with
    # similar titles ("Role 1" vs "Role 10") get fuzzy-merged by
    # services/jobs/dedup.py, which would make the offer count unpredictable.
    return [_offer(sid=f"{prefix}{i}", title=f"Role {i} Engineer", company=f"Company{i}")
            for i in range(n)]


def test_pagination_fifteen_offers_in_three_batches_of_five(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", _offers(15))])
    client = _client(db, u)

    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "role"}))
    first = _send(client, c.id, "montre-moi tous les jobs que tu as trouves").json()
    assert len(first["jobs_found"]) == 5

    db.refresh(c)
    ordered = c.job_browse_state["ordered_job_ids"]
    assert len(ordered) == 15
    assert len(set(ordered)) == 15                              # no duplicate ids
    assert [j["job_offer_id"] for j in first["jobs_found"]] == ordered[0:5]

    _route(mock_llm, _directive(intent="list_jobs"))
    second = _send(client, c.id, "montre-moi les suivantes").json()
    assert [j["job_offer_id"] for j in second["jobs_found"]] == ordered[5:10]

    third = _send(client, c.id, "encore").json()
    assert [j["job_offer_id"] for j in third["jobs_found"]] == ordered[10:15]

    # every batch's ids are disjoint (no repeat across batches)
    seen = set()
    for batch in (first, second, third):
        ids = {j["job_offer_id"] for j in batch["jobs_found"]}
        assert not (ids & seen)
        seen |= ids
    assert len(seen) == 15

    # nothing left — no fourth batch, and no error / no new search triggered
    fourth = _send(client, c.id, "montre-moi encore plus").json()
    assert fourth["jobs_found"] == []
    db.refresh(c)
    assert c.job_browse_state["ordered_job_ids"] == ordered      # never recomputed


def test_pagination_with_fewer_than_five_offers(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", _offers(3, prefix="q"))])
    client = _client(db, u)

    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "role"}))
    first = _send(client, c.id, "cherche un role").json()
    assert len(first["jobs_found"]) == 3

    db.refresh(c)
    assert c.job_browse_state["offset"] == 3

    _route(mock_llm, _directive(intent="list_jobs"))
    second = _send(client, c.id, "montre-moi les suivantes").json()
    assert second["jobs_found"] == []


def test_list_jobs_before_any_search_is_a_helpful_reply_not_an_error(db, mock_llm):
    u = _user(db); c = _conv(db, u)
    client = _client(db, u)
    _route(mock_llm, _directive(intent="list_jobs"))
    body = _send(client, c.id, "montre-moi tous les jobs que tu as trouves").json()
    assert body["jobs_found"] == []
    assert body["assistant_message"]["content"]


# ---------------------------------------------------------------------------
# Display ordinal -> real UUID (never the literal "1")
# ---------------------------------------------------------------------------

def test_apply_by_displayed_ordinal_resolves_to_the_real_uuid(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", _offers(6, prefix="r"))])
    client = _client(db, u)

    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "role"}))
    _send(client, c.id, "cherche un role")
    db.refresh(c)
    ordered = c.job_browse_state["ordered_job_ids"]

    _route(mock_llm, _directive(intent="apply_jobs",
                                apply={"scope": "ids", "job_ids": ["1"], "filter": None}))
    prop = _send(client, c.id, "postule a l'offre 1").json()
    target_ids = prop["pending_confirmation"]["job_ids"]

    assert target_ids == [ordered[0]]
    assert target_ids[0] != "1"
    uuid.UUID(target_ids[0])                     # a real UUID, not the ordinal

    db.refresh(c)
    assert c.pending_action["job_ids"] == [ordered[0]]

    _route(mock_llm, _directive(reply="unused", intent="chat"))
    _send(client, c.id, "oui")
    applied = [a.job_offer_id for a in db.query(JobApplication).filter_by(user_id=u.id)]
    assert applied == [ordered[0]]

    # a later search must never change what that already-confirmed id means
    swap_providers([FakeJobProvider("arbeitnow", _offers(6, prefix="s"))])
    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "other role"}))
    _send(client, c.id, "cherche un autre role")
    db.refresh(c)
    assert applied[0] not in (c.job_browse_state["ordered_job_ids"])
