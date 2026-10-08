"""Phase 7 — the intelligent, contextual, any-topic recommendation engine.

Two layers, tested mostly independently of any real LLM:

- ``services/conversations/recommendation_engine.py`` (ContextBuilder +
  validate_and_rank) — pure Python, no LLM, no HTTP, no execution.
- the conversational path end-to-end with the LLM mocked to return
  different ``IntelligentRecommendation`` payloads, proving the backend
  accepts and correctly handles arbitrary, non-enum ``action`` values while
  still enforcing ownership/safety/state-consistency.
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
from models import (
    Conversation, GeneratedCV, GeneratedLetter, JobApplication, JobOffer,
    SavedJob, User,
)
from schemas.recommendation import IntelligentRecommendation
from services.conversations.recommendation_engine import (
    build_context, render_context_block, validate_and_rank,
)


def _user(db, email="reco@x.com"):
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


def _rec(action, **kw):
    base = dict(
        action=action, title="t", message="m", reason="r", priority="medium",
        confidence=0.8, requires_confirmation=False, requires_information=False,
        question=None, target_type=None, target_id=None, parameters={},
    )
    base.update(kw)
    return base


def _directive(**kw):
    base = {"reply": "", "intent": "chat", "search_patch": None, "apply": None,
            "question": None, "recommendations": []}
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
# A. Contextuality — ContextBuilder is compact and reflects real state
# ---------------------------------------------------------------------------

def test_context_builder_is_quiet_with_no_documents_yet(db):
    u = _user(db)
    ctx = build_context(db, u)
    assert "no CV" in ctx.cv_summary
    assert "no cover letter" in ctx.letter_summary
    assert "no applications" in ctx.application_summary


def test_context_builder_reflects_existing_documents_and_applications(db):
    u = _user(db)
    cv = GeneratedCV(user_id=u.id, filename="cv.pdf", storage_key="cv/x.pdf",
                     minio_bucket="cv-files", language="en", ats_score=77.0)
    letter = GeneratedLetter(user_id=u.id, filename="l.pdf", storage_key="letter/x.pdf",
                             minio_bucket="cv-files", language="fr")
    db.add_all([cv, letter]); db.commit()
    offer = JobOffer(source="arbeitnow", source_job_id="x1", source_url="https://x.test/1",
                     content_hash="h1", title="Backend Engineer")
    db.add(offer); db.commit()
    app = JobApplication(user_id=u.id, job_offer_id=offer.id, status="prepared")
    db.add(app); db.commit()

    ctx = build_context(db, u)
    assert "77" in ctx.cv_summary
    assert "fr" in ctx.letter_summary
    assert "prepared" in ctx.application_summary
    block = render_context_block(ctx)
    assert "CV STATUS" in block and "COVER LETTER STATUS" in block and "APPLICATIONS STATUS" in block


def test_prompt_embeds_the_compact_context_block_not_raw_rows(db):
    """The prompt must carry the SUMMARY, never a full CV/letter/application
    dump (the mission's explicit anti "send the whole DB" requirement)."""
    from services.conversations.job_agent_service import JobConversationAgent

    u = _user(db); c = _conv(db, u)
    cv = GeneratedCV(user_id=u.id, filename="cv.pdf", storage_key="cv/x.pdf",
                     minio_bucket="cv-files", language="en", ats_score=42.0,
                     structured_source="SECRET_FULL_CV_BODY_SHOULD_NOT_LEAK")
    db.add(cv); db.commit()

    prompt = JobConversationAgent(db)._build_prompt(u, c, [{"role": "user", "content": "hi"}])
    assert "CV STATUS" in prompt
    assert "42" in prompt                          # the summary made it in
    assert "SECRET_FULL_CV_BODY_SHOULD_NOT_LEAK" not in prompt   # the raw document did not


# ---------------------------------------------------------------------------
# B. Diversity of topics — non-enum actions from every domain in the mission
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action", [
    "adapt_cv_to_job", "improve_cv_summary", "compare_cv_versions", "clarify_ambiguous_period",
    "generate_cover_letter", "rewrite_cover_letter_tone", "check_cv_letter_consistency",
    "widen_search_location", "reduce_search_constraints", "search_similar_roles", "see_more_offers",
    "analyze_job_fit", "identify_job_strengths", "identify_job_gaps",
    "clarify_user_preference", "update_search_preference", "ask_contract_type",
    "review_prepared_application", "propose_multiple_applications", "verify_cv_used",
    "clarify_request", "offer_choice_between_options", "resume_pending_task",
])
def test_engine_accepts_a_wide_variety_of_non_enum_actions(db, action):
    u = _user(db)
    raw = [IntelligentRecommendation(**_rec(action))]
    kept = validate_and_rank(db, u, raw)
    assert len(kept) == 1
    assert kept[0].action == action


def test_action_field_has_no_enum_or_literal_constraint():
    """Regression guard: the old 12-value closed vocabulary must not creep
    back in — action stays a free string."""
    field = IntelligentRecommendation.model_fields["action"]
    assert field.annotation is str


# ---------------------------------------------------------------------------
# C. Non-static behaviour
# ---------------------------------------------------------------------------

def test_job_agent_no_longer_imports_the_old_fixed_rule_engine():
    import inspect

    import services.conversations.job_agent_service as mod
    src = inspect.getsource(mod)
    assert "services.recommendations" not in src
    assert "RecommendationContext(" not in src


def test_different_directives_yield_different_recommendation_sets(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [_offer(sid="a", title="Backend Engineer")])])

    _route(mock_llm, _directive(
        intent="chat", reply="ok",
        recommendations=[_rec("clarify_user_preference", title="Clarify")],
    ))
    r1 = _send(_client(db, u), c.id, "hello").json()

    _route(mock_llm, _directive(
        intent="chat", reply="ok",
        recommendations=[_rec("improve_cv_summary", title="Improve summary")],
    ))
    r2 = _send(_client(db, u), c.id, "my cv is too long").json()

    actions1 = {r["action"] for r in r1["recommendations"]}
    actions2 = {r["action"] for r in r2["recommendations"]}
    assert actions1 != actions2
    assert "clarify_user_preference" in actions1
    assert "improve_cv_summary" in actions2


def test_no_hardcoded_generic_quartet_regardless_of_context(db, mock_llm, swap_providers):
    """The old failure mode: review_match / generate_cv / generate_letter /
    download_cv shown no matter what. Prove a plain chat turn with no LLM
    recommendations surfaces none — never a fallback fixed list."""
    u = _user(db); c = _conv(db, u)
    _route(mock_llm, _directive(intent="chat", reply="Sure.", recommendations=[]))
    body = _send(_client(db, u), c.id, "thanks!").json()
    assert body["recommendations"] == []


# ---------------------------------------------------------------------------
# D. Security — ownership, cross-user targets, unknown resources
# ---------------------------------------------------------------------------

def test_recommendation_targeting_another_users_job_is_dropped(db):
    a = _user(db, "a@x.com"); b = _user(db, "b@x.com")
    offer = JobOffer(source="arbeitnow", source_job_id="x2", source_url="https://x.test/2",
                     content_hash="h2", title="Data Engineer")
    db.add(offer); db.commit()
    db.add(SavedJob(user_id=b.id, job_offer_id=offer.id, origin="agent")); db.commit()

    raw = [IntelligentRecommendation(**_rec("adapt_cv_to_job", target_type="job", target_id=offer.id))]
    assert validate_and_rank(db, a, raw) == []   # a does not have this job selected


def test_recommendation_targeting_an_unowned_cv_is_dropped(db):
    a = _user(db, "a2@x.com"); b = _user(db, "b2@x.com")
    cv = GeneratedCV(user_id=b.id, filename="cv.pdf", storage_key="cv/y.pdf",
                     minio_bucket="cv-files", language="en")
    db.add(cv); db.commit()
    raw = [IntelligentRecommendation(**_rec("review_cv", target_type="cv", target_id=cv.id))]
    assert validate_and_rank(db, a, raw) == []


def test_recommendation_with_a_malformed_target_id_is_dropped(db):
    u = _user(db)
    raw = [IntelligentRecommendation(**_rec("adapt_cv_to_job", target_type="job", target_id="not-a-uuid"))]
    assert validate_and_rank(db, u, raw) == []


def test_recommendation_with_a_wellformed_but_nonexistent_target_id_is_dropped(db):
    u = _user(db)
    raw = [IntelligentRecommendation(**_rec("adapt_cv_to_job", target_type="job", target_id=str(uuid.uuid4())))]
    assert validate_and_rank(db, u, raw) == []


def test_recommendation_owning_its_own_job_target_is_kept(db):
    u = _user(db)
    offer = JobOffer(source="arbeitnow", source_job_id="x3", source_url="https://x.test/3",
                     content_hash="h3", title="ML Engineer")
    db.add(offer); db.commit()
    db.add(SavedJob(user_id=u.id, job_offer_id=offer.id, origin="agent")); db.commit()

    raw = [IntelligentRecommendation(**_rec("adapt_cv_to_job", target_type="job", target_id=offer.id))]
    kept = validate_and_rank(db, u, raw)
    assert len(kept) == 1 and kept[0].target_id == offer.id


def test_stray_target_id_on_a_non_ownable_type_is_cleared_not_rejected(db):
    """A target_id the LLM attached to e.g. search_preferences (which has no
    owned resource) is noise, not a reason to drop an otherwise-useful tip."""
    u = _user(db)
    raw = [IntelligentRecommendation(**_rec(
        "update_search_preference", target_type="search_preferences", target_id=str(uuid.uuid4()),
    ))]
    kept = validate_and_rank(db, u, raw)
    assert len(kept) == 1 and kept[0].target_id is None


@pytest.mark.parametrize("action", [
    "delete_all_documents", "drop_table_users", "execute_sql_query", "rm_-rf_everything",
    "run_shell_command", "sudo_reset_password", "grant_admin_access",
])
def test_forbidden_actions_are_dropped_never_surfaced(db, action):
    u = _user(db)
    raw = [IntelligentRecommendation(**_rec(action))]
    assert validate_and_rank(db, u, raw) == []


def test_recommendations_endpoint_never_leaks_across_users(db, mock_llm, swap_providers):
    a = _user(db, "usera@x.com"); b = _user(db, "userb@x.com")
    ca = _conv(db, a)
    offer = JobOffer(source="arbeitnow", source_job_id="x4", source_url="https://x.test/4",
                     content_hash="h4", title="Platform Engineer")
    db.add(offer); db.commit()
    db.add(SavedJob(user_id=a.id, job_offer_id=offer.id, origin="agent")); db.commit()

    _route(mock_llm, _directive(
        intent="chat", reply="ok",
        recommendations=[_rec("adapt_cv_to_job", target_type="job", target_id=offer.id)],
    ))
    body = _send(_client(db, a), ca.id, "help").json()
    assert len(body["recommendations"]) == 1     # a owns the job -> kept

    # b tries to read a's conversation outright -> 403, never a's recommendations
    r = _client(db, b).get(f"/api/conversations/{ca.id}")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# E. recommendation != execution
# ---------------------------------------------------------------------------

def test_recommendation_never_triggers_an_application(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [_offer(sid="a", title="A")])])
    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "x"}))
    _send(_client(db, u), c.id, "cherche un role")

    offer_id = db.query(JobOffer).first().id
    _route(mock_llm, _directive(
        intent="chat", reply="ok",
        recommendations=[_rec("apply_to_selected_jobs", target_type="job", target_id=offer_id,
                              requires_confirmation=True)],
    ))
    body = _send(_client(db, u), c.id, "what should I do next?").json()
    assert any(r["action"] == "apply_to_selected_jobs" for r in body["recommendations"])
    assert db.query(JobApplication).count() == 0   # suggested, never executed
    assert body["pending_confirmation"] is None    # a recommendation is not a proposal


def test_apply_like_recommendations_are_forced_to_require_confirmation(db):
    u = _user(db)
    offer = JobOffer(source="arbeitnow", source_job_id="x5", source_url="https://x.test/5",
                     content_hash="h5", title="SRE")
    db.add(offer); db.commit()
    db.add(SavedJob(user_id=u.id, job_offer_id=offer.id, origin="agent")); db.commit()

    raw = [IntelligentRecommendation(**_rec(
        "apply_to_this_job", target_type="job", target_id=offer.id, requires_confirmation=False,
    ))]
    kept = validate_and_rank(db, u, raw)
    assert kept[0].requires_confirmation is True


def test_there_is_no_dispatcher_that_executes_a_recommendation_action():
    """Regression guard: nothing anywhere maps action -> a callable. A
    recommendation is inert data, always — see the module docstring."""
    import inspect

    import services.conversations.recommendation_engine as mod
    src = inspect.getsource(mod)
    assert "getattr(" not in src
    assert "globals()[" not in src
    # note: the module's own forbidden-action regex literally contains the
    # substring "exec(" as pattern text (matching e.g. "execute_sql") — check
    # for an actual call (whitespace/start-of-line before it) rather than a
    # bare substring, so this guard doesn't trip on itself.
    assert "eval(" not in src
    assert not any(line.strip().startswith(("exec(", "eval(")) for line in src.splitlines())


def test_no_module_in_the_conversation_path_maps_action_to_a_callable():
    """Audit hardening (repo-wide, not just recommendation_engine.py): the
    engine module bans a bare `getattr(` outright because it has no
    legitimate use for one — but job_agent_service.py legitimately calls
    `getattr(conversation, "pending_action", None)` etc. for OTHER
    attributes, so a blanket ban there would be a false positive. What must
    actually never exist, anywhere in the conversation path, is a getattr/
    index/registry lookup keyed by a recommendation's own `action` string —
    i.e. the LLM's text ever selecting WHICH attribute/callable gets
    touched."""
    import inspect
    import re

    import api.conversations as conv_api
    import services.conversations.conversation_service as conv_svc
    import services.conversations.job_agent_service as job_svc
    import services.conversations.recommendation_engine as reco_engine

    dangerous = re.compile(
        r"getattr\([^,]+,\s*(\w+\.)?action\b"      # getattr(x, rec.action) / getattr(x, action)
        r"|dispatch(er)?\[[^\]]*action"             # dispatch[...action...]
        r"|handlers?\[[^\]]*action"                 # handler[...action...]
        r"|actions\[[^\]]*\]\("                     # actions[...](...)
        r"|globals\(\)\[|locals\(\)\[",
        re.I,
    )
    for mod in (conv_api, conv_svc, job_svc, reco_engine):
        src = inspect.getsource(mod)
        assert not dangerous.search(src), f"dangerous action-dispatch pattern found in {mod.__name__}"


# ---------------------------------------------------------------------------
# F. confirmation (Phase 6) — still untouched by this engine
# ---------------------------------------------------------------------------

def test_apply_proposal_confirmation_flow_is_unaffected_by_recommendations(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [_offer(sid="a", title="A")])])
    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "x"}))
    _send(_client(db, u), c.id, "cherche un role")

    _route(mock_llm, _directive(
        intent="apply_jobs", apply={"scope": "all", "job_ids": [], "filter": None},
        recommendations=[_rec("unrelated_tip")],
    ))
    prop = _send(_client(db, u), c.id, "postule a cette offre").json()
    assert prop["pending_confirmation"] is not None
    assert db.query(JobApplication).count() == 0

    def _boom(*a, **k):
        raise AssertionError("the LLM must not be called on a confirmation turn")
    mock_llm.set(_boom)
    confirmed = _send(_client(db, u), c.id, "oui").json()
    assert len(confirmed["applications"]) == 1
    assert confirmed["recommendations"] == []   # no LLM call -> no new recommendations, not stale ones


# ---------------------------------------------------------------------------
# G. multilingual
# ---------------------------------------------------------------------------

def test_prompt_targets_the_detected_language_for_recommendations(db):
    from services.conversations.job_agent_service import JobConversationAgent

    u = _user(db); c = _conv(db, u)
    agent = JobConversationAgent(db)
    prompt_fr = agent._build_prompt(
        u, c, [{"role": "user", "content": "Je cherche un poste a Paris, s'il vous plait"}])
    assert "(fr)" in prompt_fr and "(en)" not in prompt_fr

    prompt_en = agent._build_prompt(
        u, c, [{"role": "user", "content": "I am looking for a role in Paris please"}])
    assert "(en)" in prompt_en and "(fr)" not in prompt_en


def test_recommendation_text_round_trips_in_french(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    _route(mock_llm, _directive(
        intent="chat", reply="Bonjour",
        recommendations=[_rec("clarify_user_preference", title="Préciser votre préférence",
                              message="Voulez-vous aussi les postes hybrides ?")],
    ))
    body = _send(_client(db, u), c.id, "Je préfère le télétravail").json()
    assert body["recommendations"][0]["title"] == "Préciser votre préférence"
    assert "hybrides" in body["recommendations"][0]["message"]


# ---------------------------------------------------------------------------
# H. limits — 0 to 3, ranked, deduplicated
# ---------------------------------------------------------------------------

def test_at_most_three_recommendations_survive_ranked_by_priority_then_confidence(db):
    u = _user(db)
    raw = [
        IntelligentRecommendation(**_rec("a1", priority="low", confidence=0.9)),
        IntelligentRecommendation(**_rec("a2", priority="high", confidence=0.5)),
        IntelligentRecommendation(**_rec("a3", priority="medium", confidence=0.99)),
        IntelligentRecommendation(**_rec("a4", priority="high", confidence=0.95)),
        IntelligentRecommendation(**_rec("a5", priority="low", confidence=0.99)),
    ]
    kept = validate_and_rank(db, u, raw)
    assert len(kept) == 3
    assert [r.action for r in kept] == ["a4", "a2", "a3"]


def test_duplicate_action_and_target_is_deduplicated(db):
    u = _user(db)
    raw = [
        IntelligentRecommendation(**_rec("clarify_user_preference", confidence=0.5)),
        IntelligentRecommendation(**_rec("clarify_user_preference", confidence=0.9)),
    ]
    kept = validate_and_rank(db, u, raw)
    assert len(kept) == 1


def test_zero_recommendations_is_a_valid_outcome(db):
    u = _user(db)
    assert validate_and_rank(db, u, []) == []


def test_conversation_directive_caps_raw_recommendations_before_validation():
    from schemas.conversation_directive import ConversationDirective
    d = ConversationDirective.model_validate({
        "reply": "x", "intent": "chat", "search_patch": None, "apply": None, "question": None,
        "recommendations": [_rec(f"a{i}") for i in range(9)],
    })
    assert len(d.recommendations) <= 10


# ---------------------------------------------------------------------------
# I. fallback — invalid / hallucinated recommendation payloads
# ---------------------------------------------------------------------------

def test_recommendation_missing_required_action_is_rejected_not_fabricated():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        IntelligentRecommendation.model_validate({**_rec(""), "action": ""})


def test_turn_with_an_invalid_recommendation_payload_never_crashes_or_500s(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    broken = {**_rec("x"), "action": ""}   # invalid: empty action
    payload = json.dumps({"reply": "hi", "intent": "chat", "search_patch": None,
                          "apply": None, "question": None, "recommendations": [broken]})
    _route(mock_llm, payload)
    r = _send(_client(db, u), c.id, "hello")
    assert r.status_code == 200
    assert r.json()["assistant_message"]["content"]   # degraded gracefully, still a real reply


def test_recommendation_engine_signature_takes_no_db_write_capability_beyond_the_session():
    """Regression guard mirroring test_recommendation_is_never_an_execution
    (services/recommendations.py): validate_and_rank only reads. `language`
    (Phase 8, keyword-only, optional) is the one additive parameter since —
    still no write/session/request capability of any kind."""
    import inspect

    sig = inspect.signature(validate_and_rank)
    assert list(sig.parameters) == ["db", "user", "raw", "language"]
    assert sig.parameters["language"].default is None
    assert sig.parameters["language"].kind is inspect.Parameter.KEYWORD_ONLY
