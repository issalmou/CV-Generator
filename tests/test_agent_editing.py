"""
LOT 9/10 — the conversational EDIT AGENT.

- a message naming CV_xxxxxx / LETTER_xxxxxx routes to the edit agent
  (POST /api/conversations/{id}/messages — no new endpoint);
- the LLM only proposes a structured AgentAction; the backend validates +
  applies it to structured_source, re-renders, saves a NEW VERSION of the
  SAME reference (old version kept);
- new factual claims not in the user's message are REFUSED (needs_confirmation);
- structured_source is the source of truth — the PDF is never edited directly.
"""

from __future__ import annotations

import json

import pytest

from services.documents import document_service
from services.conversations.agent_service import EditAgent, _apply_cv, _unsupported_facts, find_reference
from schemas.agent_schemas import AgentAction


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_cv(client, cv_profile_dict):
    r = client.post("/api/generate-cv", json=cv_profile_dict)
    assert r.status_code == 200
    return r.json()["reference"]


def _route(mock_llm, action: dict):
    from tests.conftest import _default_llm_router

    def router(prompt, *, request_type, use_cache=True, **kw):
        if request_type == "agent_edit":
            base = {"op": "none", "value": None, "category": None, "skill": None,
                    "from_name": None, "to_name": None, "role_index": None,
                    "bullet_index": None, "order": [], "recipient": None, "reply": ""}
            base.update(action)
            return json.dumps(base)
        return _default_llm_router(prompt, request_type=request_type, use_cache=use_cache)

    mock_llm.set(router)


# ---------------------------------------------------------------------------
# unit
# ---------------------------------------------------------------------------

def test_find_reference():
    assert find_reference("please edit CV_A8F42K thanks") == "CV_A8F42K"
    assert find_reference("LETTER_91BC72: change the tone") == "LETTER_91BC72"
    assert find_reference("how do I improve my CV?") is None


def test_unsupported_facts_flags_invented_tech_and_numbers():
    ctx = "i worked with python and sql at acme"
    assert _unsupported_facts("Managed Kubernetes clusters", ctx) == {"Kubernetes"}
    assert "40%" in _unsupported_facts("Cut latency by 40%", ctx)
    assert _unsupported_facts("Rephrased using Python and SQL", ctx) == set()


def test_apply_cv_add_skill_is_deterministic():
    s = {"skills": [{"category": "Languages", "skills": ["Python"]}]}
    out, msg = _apply_cv(s, AgentAction(op="add_skill", skill="Go", category="Languages"))
    assert out["skills"][0]["skills"] == ["Python", "Go"]
    assert "Go" in msg


def test_apply_cv_reorder_requires_full_permutation():
    s = {"experience": [{"position": "A"}, {"position": "B"}]}
    from services.conversations.agent_service import _AgentReject
    with pytest.raises(_AgentReject):
        _apply_cv(s, AgentAction(op="reorder_experience", order=[0, 0]))


# ---------------------------------------------------------------------------
# integration — via the conversation endpoint
# ---------------------------------------------------------------------------

def test_edit_by_reference_creates_new_version_same_reference(client, cv_profile_dict, mock_llm):
    ref = _make_cv(client, cv_profile_dict)
    _route(mock_llm, {"op": "set_summary", "value": "Senior backend engineer focused on data platforms.",
                      "reply": "Updated your summary."})

    conv = client.post("/api/conversations", json={}).json()
    r = client.post(f"/api/conversations/{conv['id']}/messages",
                    json={"content": f"{ref} rewrite my professional summary to sound more senior"})
    assert r.status_code == 200
    body = r.json()
    assert body["document"]["reference"] == ref
    assert body["document"]["version"] == 2
    assert body["assistant_message"]["content"] == "Updated your summary."

    versions = client.get(f"/api/cvs/{ref}/versions").json()
    assert [v["version"] for v in versions] == [1, 2]     # v1 kept


def test_agent_refuses_to_invent_a_skill(client, cv_profile_dict, mock_llm):
    ref = _make_cv(client, cv_profile_dict)
    # the LLM proposes adding a skill the user never mentioned
    _route(mock_llm, {"op": "add_skill", "skill": "Kubernetes", "category": "Cloud",
                      "reply": "Added Kubernetes."})

    conv = client.post("/api/conversations", json={}).json()
    r = client.post(f"/api/conversations/{conv['id']}/messages",
                    json={"content": f"{ref} make my skills better"})
    body = r.json()
    assert body["document"] is None                       # nothing applied
    reply = body["assistant_message"]["content"].lower()
    assert "isn't in your profile" in reply
    assert "doesn't mean you don't know it" in reply      # Phase 5 — missing ≠ can't do
    assert "which experience" in reply                    # asks for CONTEXT, not just yes/no
    # Phase 5 — the agent asks for context, as a contextual recommendation
    ra = body["recommended_actions"]
    assert ra and ra[0]["type"] == "provide_skill_context" and ra[0]["skill"] == "Kubernetes"
    assert ra[0]["question"] and "kubernetes" in ra[0]["question"].lower()

    # still only 1 version
    assert len(client.get(f"/api/cvs/{ref}/versions").json()) == 1


def test_agent_adds_skill_when_user_names_it(client, cv_profile_dict, mock_llm):
    ref = _make_cv(client, cv_profile_dict)
    _route(mock_llm, {"op": "add_skill", "skill": "Docker", "category": "Tools",
                      "reply": "Added Docker to Tools."})

    conv = client.post("/api/conversations", json={}).json()
    r = client.post(f"/api/conversations/{conv['id']}/messages",
                    json={"content": f"{ref} add Docker to my skills, I use it every day"})
    body = r.json()
    assert body["document"]["version"] == 2

    latest = client.get(f"/api/cvs/{ref}/versions/2/download")
    assert latest.status_code == 200


def test_message_without_reference_is_normal_chat(client, mock_llm):
    conv = client.post("/api/conversations", json={}).json()
    r = client.post(f"/api/conversations/{conv['id']}/messages",
                    json={"content": "How can I improve my CV in general?"})
    body = r.json()
    assert body["document"] is None
    assert body["assistant_message"]["content"]  # a normal reply
    assert "agent_edit" not in mock_llm.calls


def test_edited_message_links_to_the_document(client, db, cv_profile_dict, mock_llm):
    from models import Message

    ref = _make_cv(client, cv_profile_dict)
    _route(mock_llm, {"op": "set_career_objective", "value": "Lead data platform teams.",
                      "reply": "Done."})
    conv = client.post("/api/conversations", json={}).json()
    client.post(f"/api/conversations/{conv['id']}/messages",
                json={"content": f"{ref} set my objective to leading data platform teams"})

    linked = db.query(Message).filter(Message.document_reference == ref).all()
    assert len(linked) == 1 and linked[0].role == "assistant"
