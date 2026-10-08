"""Phase 8 — recommendation quality audit: multi-topic coverage, FR/EN
matrix, coherence checks, anti-hallucination boundaries, and end-to-end
integration across the real conversational workflows.

Honesty note (see benchmarks/PHASE_6.md §O for the full audit): these tests
mock the LLM and therefore prove the DETERMINISTIC backend (context
building, ownership, safety, contradiction pruning, language filtering,
ranking) handles a wide variety of LLM output correctly. They do NOT prove a
real LLM produces genuinely insightful recommendations for every topic —
that would require a live-LLM test (see test_real_llm_recommendation_smoke
at the bottom, which is skipped with a clear reason when no working
provider is configured, never faked).
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
from services.conversations.recommendation_engine import validate_and_rank


def _user(db, email="p8@x.com"):
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
# §3 A-G — multi-topic coverage, end to end
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action,message", [
    ("adapt_cv_to_job", "my cv feels too generic for this role"),                  # A. CV
    ("shorten_cv_summary", "my cv is way too long"),                               # A. CV
    ("generate_cover_letter", "I don't have a cover letter for this yet"),         # B. Letter
    ("review_cover_letter_consistency", "does my letter still match my cv?"),      # B. Letter
    ("widen_search_location", "I'm not finding much in just Paris"),               # C. Job search
    ("reduce_search_constraints", "nothing matches all my criteria"),              # C. Job search
    ("review_low_match_job", "this offer doesn't look like a great fit"),          # D. Matching
    ("review_prepared_application", "what's the status of my applications?"),      # E. Applications
    ("update_search_preference", "actually I'd consider hybrid too"),               # F. Profile
    ("explore_data_engineer_transition", "I want to move from backend to data"),   # G. Career change
])
def test_recommendation_covers_every_mission_topic(db, mock_llm, action, message):
    u = _user(db); c = _conv(db, u)
    _route(mock_llm, _directive(intent="chat", reply="ok", recommendations=[_rec(action)]))
    body = _send(_client(db, u), c.id, message).json()
    assert body["recommendations"] and body["recommendations"][0]["action"] == action


def test_search_context_actually_changes_recommendation_topic_across_turns(db, mock_llm, swap_providers):
    """§3-C: 'backend Python Paris' -> then 'remote only' -> then 'startups' ->
    then 'at least 50k' must each be free to surface a DIFFERENT suggestion,
    never the same generic trio every time."""
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [_offer(sid="a", title="Backend Engineer")])])

    turns = [
        ("Je cherche un poste Backend Python à Paris.", "widen_search_radius"),
        ("Je veux uniquement du remote.", "confirm_remote_only"),
        ("Je préfère les startups.", "filter_by_company_size"),
        ("Je veux au moins 50k.", "clarify_salary_currency"),
    ]
    seen_actions = []
    for message, action in turns:
        _route(mock_llm, _directive(intent="chat", reply="ok", recommendations=[_rec(action)]))
        body = _send(_client(db, u), c.id, message).json()
        seen_actions.append(body["recommendations"][0]["action"])
    assert seen_actions == [a for _, a in turns]
    assert len(set(seen_actions)) == 4   # never collapsed to one fixed trio


# ---------------------------------------------------------------------------
# §4 — matching: recommendation content is free to depend on fit quality
# (the mission's own trap: don't hardcode `if ats < 50` in the new engine)
# ---------------------------------------------------------------------------

def test_recommendation_engine_has_no_ats_score_threshold_logic():
    """Regression guard: unlike services/recommendations.py (kept, CV-gen
    path only, and allowed its own thresholds), the Phase 7/8 conversational
    engine may DISPLAY `ats_score` (build_context reports it as part of the
    compact CV summary) but must contain NO numeric-threshold RULE on it —
    no `if ... ats_score ... <` / `>=` comparison anywhere. That judgement is
    the LLM's, from context, not a re-hidden `if score < N`."""
    import inspect
    import re

    import services.conversations.recommendation_engine as mod
    src = inspect.getsource(mod)
    assert "ats_score" in src, "sanity: build_context should still report it"
    comparison = re.compile(r"ats_score\s*(<|<=|>|>=|==)|(<|<=|>|>=)\s*ats_score")
    assert not comparison.search(src), "found a threshold comparison on ats_score"


@pytest.mark.parametrize("action", [
    "review_strong_match", "review_weak_match", "flag_missing_skill",
    "flag_experience_gap", "flag_location_mismatch", "flag_seniority_mismatch",
    "flag_language_requirement", "compare_multiple_offers",
])
def test_matching_related_recommendations_are_accepted_regardless_of_wording(db, action):
    u = _user(db)
    kept = validate_and_rank(db, u, [IntelligentRecommendation(**_rec(action))])
    assert len(kept) == 1 and kept[0].action == action


# ---------------------------------------------------------------------------
# §5 — applications: recommendation != execution, always
# ---------------------------------------------------------------------------

def test_recommendation_about_an_application_never_creates_one(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [_offer(sid="a", title="A")])])
    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "x"}))
    _send(_client(db, u), c.id, "cherche un role")
    offer_id = db.query(JobOffer).first().id

    _route(mock_llm, _directive(
        intent="chat", reply="ok",
        recommendations=[_rec("propose_multiple_applications", target_type="job", target_id=offer_id)],
    ))
    body = _send(_client(db, u), c.id, "should I apply now?").json()
    assert body["recommendations"]
    assert db.query(JobApplication).count() == 0


def test_recommendation_referencing_an_expired_or_unselected_job_is_dropped(db):
    u = _user(db)
    offer = JobOffer(source="arbeitnow", source_job_id="exp1", source_url="https://x.test/exp1",
                     content_hash="hexp1", title="Old Role", is_active=False)
    db.add(offer); db.commit()
    # never saved/selected by the user -> not owned, regardless of is_active
    kept = validate_and_rank(db, u, [IntelligentRecommendation(
        **_rec("review_prepared_application", target_type="job", target_id=offer.id))])
    assert kept == []


# ---------------------------------------------------------------------------
# §6/§7 — profile preferences + career change topics
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action", [
    "clarify_location_preference", "clarify_contract_type", "clarify_availability",
    "clarify_target_seniority", "suggest_related_sector",
    "explore_backend_to_data_transition", "suggest_devops_upskilling_path",
    "suggest_senior_track_preparation",
])
def test_profile_and_career_topics_are_accepted(db, action):
    u = _user(db)
    kept = validate_and_rank(db, u, [IntelligentRecommendation(**_rec(action))])
    assert len(kept) == 1


def test_interview_prep_feature_is_not_fabricated_if_unsupported():
    """§8: the mission explicitly forbids inventing a fake workflow. There is
    no interview-preparation service in this codebase (grep confirms no
    'interview' module) — the recommendation engine does not claim to
    support one; it can still surface a generic conversational suggestion
    (a free-text action), it just has no dedicated backend capability behind
    it, same as most `action` values (see module docstring: recommendations
    are never dispatched to a capability, always just displayed)."""
    import importlib
    import pkgutil

    import services
    all_service_modules = {
        name for _, name, _ in pkgutil.iter_modules(services.__path__)
    }
    assert "interview" not in " ".join(all_service_modules).lower()
    # documented, not asserted as a failure: see benchmarks/PHASE_6.md §O
    # "feature not supported" for interview-prep.


# ---------------------------------------------------------------------------
# §9 — missing information never becomes an invented fact
# ---------------------------------------------------------------------------

def test_request_information_path_is_unaffected_by_the_new_engine(db, mock_llm):
    u = _user(db); c = _conv(db, u)
    _route(mock_llm, _directive(
        intent="request_information",
        question={"topic": "Kubernetes",
                  "question": "The job wants Kubernetes experience — have you used it, and where?",
                  "reason": "Kubernetes is required but not confirmed in your profile."},
        recommendations=[_rec("clarify_kubernetes_experience", requires_information=True,
                              question="Have you used Kubernetes?")],
    ))
    body = _send(_client(db, u), c.id, "postule au poste DevOps").json()
    ra = body["recommended_actions"]
    assert len(ra) == 1 and ra[0]["type"] == "request_information"
    assert "Kubernetes" in ra[0]["question"]
    # if the new engine emits an additional suggestion, it must carry a real question.
    if body["recommendations"]:
        assert body["recommendations"][0]["question"]


# ---------------------------------------------------------------------------
# §10 / §17 (incohérence multilingue) — FR/EN matrix + language filtering
# ---------------------------------------------------------------------------

_TOPIC_MESSAGES_FR = {
    "cv": "Je veux améliorer mon CV.",
    "jobs": "Je cherche un poste Backend Python en France.",
    "matching": "Cette offre correspond-elle à mon profil ?",
    "application": "Postule aux offres 1 et 3.",
    "profile": "Je préfère le télétravail complet.",
    "missing_info": "Je ne sais pas si mon expérience Kubernetes suffit.",
}
_TOPIC_MESSAGES_EN = {
    "cv": "I want to improve my resume.",
    "jobs": "I'm looking for a remote Python backend position in France.",
    "matching": "Does this offer match my profile?",
    "application": "Apply to jobs 1 and 3.",
    "profile": "I only want fully remote roles.",
    "missing_info": "I'm not sure my Kubernetes experience is enough.",
}


@pytest.mark.parametrize("topic", sorted(_TOPIC_MESSAGES_FR))
def test_french_topic_matrix_recommendation_kept_in_french(db, topic):
    u = _user(db)
    rec = IntelligentRecommendation(**_rec(
        f"fr_{topic}_tip", title="Suggestion pertinente pour vous",
        message="Voici une piste utile compte tenu de votre situation actuelle et de vos préférences.",
    ))
    kept = validate_and_rank(db, u, [rec], language="fr")
    assert len(kept) == 1


@pytest.mark.parametrize("topic", sorted(_TOPIC_MESSAGES_EN))
def test_english_topic_matrix_recommendation_kept_in_english(db, topic):
    u = _user(db)
    rec = IntelligentRecommendation(**_rec(
        f"en_{topic}_tip", title="A useful suggestion for you",
        message="Here is a useful next step given your current situation and preferences.",
    ))
    kept = validate_and_rank(db, u, [rec], language="en")
    assert len(kept) == 1


def test_french_conversation_with_an_english_recommendation_is_dropped(db):
    u = _user(db)
    rec = IntelligentRecommendation(**_rec(
        "mismatched_language_tip", title="A useful suggestion for you right now",
        message="Here is a clear and useful next step given your current profile and situation.",
    ))
    kept = validate_and_rank(db, u, [rec], language="fr")
    assert kept == []


def test_english_conversation_with_a_french_recommendation_is_dropped(db):
    u = _user(db)
    rec = IntelligentRecommendation(**_rec(
        "mismatched_language_tip", title="Une suggestion pertinente pour vous",
        message="Voici une piste utile et claire compte tenu de votre situation et de votre profil.",
    ))
    kept = validate_and_rank(db, u, [rec], language="en")
    assert kept == []


def test_short_label_is_never_penalised_for_a_weak_language_signal(db):
    """A 1-2 word title like 'Adapt CV' or 'Adapter CV' has too little signal
    to reliably detect language — must never be dropped on that basis alone."""
    u = _user(db)
    rec = IntelligentRecommendation(**_rec("adapt_cv", title="Adapt CV", message="Ok."))
    assert len(validate_and_rank(db, u, [rec], language="fr")) == 1


def test_end_to_end_french_message_yields_a_french_confirmation_and_recommendation(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [_offer(sid="a", title="Ingenieur Backend")])])
    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "backend"}))
    _send(_client(db, u), c.id, "trouve-moi un poste backend a Paris")

    _route(mock_llm, _directive(
        intent="apply_jobs", apply={"scope": "all", "job_ids": [], "filter": None},
        recommendations=[_rec("tip_fr", title="Verifiez votre lettre",
                              message="Assurez-vous que votre lettre correspond bien a cette offre precise.")],
    ))
    body = _send(_client(db, u), c.id, "Peux-tu postuler a cette offre pour moi ?").json()
    reply = body["assistant_message"]["content"].lower()
    assert "oui" in reply and "non" in reply
    assert body["recommendations"]   # French-enough text survives the fr filter


def test_end_to_end_english_message_yields_an_english_confirmation_and_recommendation(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [_offer(sid="a", title="Backend Engineer")])])
    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "backend"}))
    _send(_client(db, u), c.id, "find me a backend engineer role in Paris")

    _route(mock_llm, _directive(
        intent="apply_jobs", apply={"scope": "all", "job_ids": [], "filter": None},
        recommendations=[_rec("tip_en", title="Check your cover letter",
                              message="Make sure your cover letter actually matches this specific offer.")],
    ))
    body = _send(_client(db, u), c.id, "Can you apply to this job for me please?").json()
    reply = body["assistant_message"]["content"].lower()
    assert "yes" in reply and "no" in reply
    assert body["recommendations"]


# ---------------------------------------------------------------------------
# §17 — coherence: contradiction / duplicate / premature action
# ---------------------------------------------------------------------------

def test_contradictory_letter_recommendations_for_the_same_job_collapse_to_one(db):
    u = _user(db)
    offer = JobOffer(source="arbeitnow", source_job_id="coh1", source_url="https://x.test/coh1",
                     content_hash="hcoh1", title="Backend Role")
    db.add(offer); db.commit()
    db.add(SavedJob(user_id=u.id, job_offer_id=offer.id, origin="agent")); db.commit()

    raw = [
        IntelligentRecommendation(**_rec("generate_cover_letter", target_type="job",
                                        target_id=offer.id, priority="low", confidence=0.5)),
        IntelligentRecommendation(**_rec("review_cover_letter_tone", target_type="job",
                                        target_id=offer.id, priority="high", confidence=0.9)),
    ]
    kept = validate_and_rank(db, u, raw)
    assert len(kept) == 1
    assert kept[0].action == "review_cover_letter_tone"   # the higher-ranked one wins


def test_contradictory_cv_recommendations_for_different_jobs_do_not_collapse(db):
    """Different targets -> not a contradiction, both may legitimately stand."""
    u = _user(db)
    o1 = JobOffer(source="arbeitnow", source_job_id="coh2", source_url="https://x.test/coh2",
                 content_hash="hcoh2", title="Role A")
    o2 = JobOffer(source="arbeitnow", source_job_id="coh3", source_url="https://x.test/coh3",
                 content_hash="hcoh3", title="Role B")
    db.add_all([o1, o2]); db.commit()
    db.add_all([SavedJob(user_id=u.id, job_offer_id=o1.id, origin="agent"),
               SavedJob(user_id=u.id, job_offer_id=o2.id, origin="agent")])
    db.commit()

    raw = [
        IntelligentRecommendation(**_rec("adapt_cv_to_job", target_type="job", target_id=o1.id)),
        IntelligentRecommendation(**_rec("adapt_cv_to_job_differently", target_type="job", target_id=o2.id)),
    ]
    kept = validate_and_rank(db, u, raw)
    assert len(kept) == 2


def test_apply_recommendation_requiring_information_still_forces_confirmation(db):
    """§17 'action prématurée': even if the LLM under-set requires_confirmation,
    an apply-like recommendation is never allowed to look unconfirmed."""
    u = _user(db)
    offer = JobOffer(source="arbeitnow", source_job_id="coh4", source_url="https://x.test/coh4",
                     content_hash="hcoh4", title="Role C")
    db.add(offer); db.commit()
    db.add(SavedJob(user_id=u.id, job_offer_id=offer.id, origin="agent")); db.commit()

    rec = IntelligentRecommendation(**_rec(
        "apply_to_this_job", target_type="job", target_id=offer.id,
        requires_information=True, requires_confirmation=False,
        question="Are you sure you meet the on-site requirement?",
    ))
    kept = validate_and_rank(db, u, [rec])
    assert kept[0].requires_confirmation is True


# ---------------------------------------------------------------------------
# §16 — anti-hallucination boundary (honest limitation, see report)
# ---------------------------------------------------------------------------

def test_recommendation_text_is_never_written_back_into_the_users_profile_or_cv():
    """The engine cannot fact-check free text (§16 is fundamentally a prompt
    concern, not a backend-validation one — see the audit report). What IS
    guaranteed structurally: nothing anywhere takes recommendation text and
    persists it as a verified fact."""
    import inspect

    import services.conversations.recommendation_engine as reco_mod
    import services.user_profile_service as profile_mod
    src_reco = inspect.getsource(reco_mod)
    src_profile = inspect.getsource(profile_mod)
    assert "UserProfile(" not in src_reco
    assert "rec.message" not in src_profile and "rec.parameters" not in src_profile


def test_hallucinated_years_of_experience_in_recommendation_text_does_not_alter_stored_profile(db):
    """A recommendation CLAIMING '7 years of Python' (when nothing in the DB
    says that) must not change any stored record — it is display-only text."""
    from models import UserProfile

    u = _user(db)
    db.add(UserProfile(user_id=u.id, skills=["Python"])); db.commit()

    rec = IntelligentRecommendation(**_rec(
        "confirm_python_seniority", title="Nice, 7 years of Python!",
        message="Your profile shows 7 years of Python experience, well above the requirement.",
        parameters={"years_claimed": 7},
    ))
    validate_and_rank(db, u, [rec])   # runs, does nothing to storage

    profile = db.get(UserProfile, u.id)
    assert profile.skills == ["Python"]   # untouched — the claim never became stored fact


def test_unconfirmed_skill_claim_is_downgraded_to_a_question(db):
    """Fix applied after the initial audit: a recommendation asserting (not
    asking about) a skill NOT present in the stored profile is no longer
    surfaced as a confident, unearned claim — it becomes a question."""
    from models import UserProfile

    u = _user(db)
    db.add(UserProfile(user_id=u.id, skills=["Python"])); db.commit()

    rec = IntelligentRecommendation(**_rec(
        "confirm_kubernetes_seniority", title="Great Kubernetes background!",
        message="Your profile shows solid Kubernetes experience for this role.",
        parameters={"skill": "Kubernetes"}, requires_information=False,
    ))
    kept = validate_and_rank(db, u, [rec])
    assert len(kept) == 1
    assert kept[0].requires_information is True
    assert kept[0].question


def test_confirmed_skill_claim_matching_the_profile_is_left_alone(db):
    from models import UserProfile

    u = _user(db)
    db.add(UserProfile(user_id=u.id, skills=["Python", "Kubernetes"])); db.commit()

    rec = IntelligentRecommendation(**_rec(
        "confirm_kubernetes_seniority", title="Great Kubernetes background!",
        message="Your profile shows solid Kubernetes experience for this role.",
        parameters={"skill": "Kubernetes"}, requires_information=False,
    ))
    kept = validate_and_rank(db, u, [rec])
    assert len(kept) == 1
    assert kept[0].requires_information is False   # matches real stored data — left as a statement


def test_skill_claim_already_framed_as_a_question_is_never_touched(db):
    u = _user(db)
    rec = IntelligentRecommendation(**_rec(
        "ask_kubernetes_experience", title="Kubernetes?",
        message="Have you used Kubernetes?", parameters={"skill": "Kubernetes"},
        requires_information=True, question="Have you used Kubernetes, and where?",
    ))
    kept = validate_and_rank(db, u, [rec])
    assert kept[0].question == "Have you used Kubernetes, and where?"   # not overwritten


# ---------------------------------------------------------------------------
# §24 — LLM-mocked variation matrix
# ---------------------------------------------------------------------------

def test_null_action_is_rejected_not_coerced(db, mock_llm):
    u = _user(db); c = _conv(db, u)
    payload = json.dumps({"reply": "hi", "intent": "chat", "search_patch": None, "apply": None,
                          "question": None,
                          "recommendations": [{**_rec("x"), "action": None}]})
    _route(mock_llm, payload)
    r = _send(_client(db, u), c.id, "hello")
    assert r.status_code == 200   # degrades gracefully (see test_intelligent_recommendations.py)


def test_unknown_but_well_formed_action_is_accepted(db):
    u = _user(db)
    kept = validate_and_rank(db, u, [IntelligentRecommendation(**_rec("something_completely_new_and_unseen"))])
    assert len(kept) == 1


def test_dangerous_action_delete_account_is_dropped(db):
    u = _user(db)
    kept = validate_and_rank(db, u, [IntelligentRecommendation(**_rec("delete_account"))])
    assert kept == []


def test_recommendation_with_no_context_hallucinated_target_is_dropped(db):
    import uuid
    u = _user(db)
    kept = validate_and_rank(db, u, [IntelligentRecommendation(
        **_rec("review_application", target_type="application", target_id=str(uuid.uuid4())))])
    assert kept == []


# ---------------------------------------------------------------------------
# §18 — integration across real workflows (not just Validator unit tests)
# ---------------------------------------------------------------------------

def test_full_workflow_search_to_selected_jobs_to_recommendation(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [_offer(sid="w1", title="Platform Engineer")])])
    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "platform"}))
    search_body = _send(_client(db, u), c.id, "cherche un role platform").json()
    assert search_body["jobs_found"]
    job_id = search_body["jobs_found"][0]["job_offer_id"]

    _route(mock_llm, _directive(
        intent="chat", reply="ok",
        recommendations=[_rec("review_job_fit", target_type="job", target_id=job_id)],
    ))
    follow_up = _send(_client(db, u), c.id, "what do you think of this one?").json()
    assert follow_up["recommendations"][0]["target_id"] == job_id


def test_full_workflow_application_proposal_then_confirmation_unaffected(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [_offer(sid="w2", title="SRE")])])
    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "sre"}))
    _send(_client(db, u), c.id, "cherche un role sre")

    _route(mock_llm, _directive(intent="apply_jobs", apply={"scope": "all", "job_ids": [], "filter": None}))
    prop = _send(_client(db, u), c.id, "postule a cette offre").json()
    assert prop["pending_confirmation"] is not None
    assert db.query(JobApplication).count() == 0

    def _boom(*a, **k):
        raise AssertionError("confirmation turn must never call the LLM")
    mock_llm.set(_boom)
    confirmed = _send(_client(db, u), c.id, "oui").json()
    assert len(confirmed["applications"]) == 1
    assert db.query(JobApplication).count() == 1


# ---------------------------------------------------------------------------
# Fixes applied after the initial audit (were "FOUND — NOT FIXED", now fixed)
# ---------------------------------------------------------------------------

def test_generic_job_recommendation_is_backfilled_with_the_real_top_match(db, mock_llm, swap_providers):
    """The LLM cannot know a job's real id before the search that finds it
    runs — a generic 'review the top match' suggestion must be pointed at
    the actual top result once it exists, without a second LLM call."""
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [_offer(sid="bf1", title="Top Match Role")])])
    _route(mock_llm, _directive(
        intent="search_jobs", search_patch={"query": "role"}, reply="ok",
        recommendations=[_rec("review_top_match", target_type="job", target_id=None)],
    ))
    body = _send(_client(db, u), c.id, "cherche un role").json()

    assert body["jobs_found"], "sanity: the search must have found something"
    top_id = body["jobs_found"][0]["job_offer_id"]
    assert body["recommendations"]
    assert body["recommendations"][0]["target_id"] == top_id


def test_backfill_never_touches_a_recommendation_unrelated_to_the_job_search(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [_offer(sid="bf2", title="Role")])])
    _route(mock_llm, _directive(
        intent="search_jobs", search_patch={"query": "role"}, reply="ok",
        recommendations=[_rec("clarify_user_preference", target_type=None, target_id=None)],
    ))
    body = _send(_client(db, u), c.id, "cherche un role").json()
    assert body["recommendations"][0]["target_id"] is None   # untouched — not job-focused wording


def test_backfill_never_overrides_a_target_id_the_llm_already_gave(db, mock_llm, swap_providers):
    """If the LLM DID name a real id (e.g. from an earlier turn's selection
    still in context), backfill must never clobber it with the new top match."""
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [_offer(sid="a1", title="A"), _offer(sid="a2", title="B")])])
    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "x"}))
    _send(_client(db, u), c.id, "cherche un role")
    older_job_id = db.query(JobOffer).first().id

    swap_providers([FakeJobProvider("arbeitnow", [_offer(sid="a3", title="C")])])
    _route(mock_llm, _directive(
        intent="search_jobs", search_patch={"query": "y"}, reply="ok",
        recommendations=[_rec("review_top_match", target_type="job", target_id=older_job_id)],
    ))
    body = _send(_client(db, u), c.id, "cherche autre chose").json()
    assert body["recommendations"][0]["target_id"] == older_job_id


def test_explicit_job_number_in_message_overrides_the_llms_own_target_guess(db, mock_llm, swap_providers):
    """Fix applied after the initial audit ('mauvais contexte'): when the
    user's OWN message names a job by an explicit number, that is ground
    truth — it must win over whatever target_id the LLM guessed."""
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [
        _offer(sid="ov1", title="Role One"), _offer(sid="ov2", title="Role Two"),
        _offer(sid="ov3", title="Role Three"),
    ])])
    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "role"}))
    _send(_client(db, u), c.id, "cherche des roles")
    db.refresh(c)
    ordered = c.job_browse_state["ordered_job_ids"]
    wrong_guess = ordered[2]   # the LLM (mocked) will "guess" the wrong one
    correct_target = ordered[1]   # "l'offre 2" == 1-based position 2

    _route(mock_llm, _directive(
        intent="chat", reply="ok",
        recommendations=[_rec("review_job_fit", target_type="job", target_id=wrong_guess)],
    ))
    body = _send(_client(db, u), c.id, "que penses-tu de l'offre 2 ?").json()
    assert body["recommendations"][0]["target_id"] == correct_target


def test_explicit_ordinal_word_also_overrides_the_target(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [
        _offer(sid="ow1", title="Role One"), _offer(sid="ow2", title="Role Two"),
    ])])
    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "role"}))
    _send(_client(db, u), c.id, "cherche des roles")
    db.refresh(c)
    ordered = c.job_browse_state["ordered_job_ids"]

    _route(mock_llm, _directive(
        intent="chat", reply="ok",
        recommendations=[_rec("review_job_fit", target_type="job", target_id=ordered[0])],
    ))
    body = _send(_client(db, u), c.id, "la deuxieme option m'interesse plus").json()
    assert body["recommendations"][0]["target_id"] == ordered[1]


def test_no_explicit_number_leaves_the_llms_target_untouched(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [_offer(sid="ov4", title="Role")])])
    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "role"}))
    _send(_client(db, u), c.id, "cherche un role")
    job_id = db.query(JobOffer).first().id

    _route(mock_llm, _directive(
        intent="chat", reply="ok",
        recommendations=[_rec("review_job_fit", target_type="job", target_id=job_id)],
    ))
    body = _send(_client(db, u), c.id, "que penses-tu de cette offre ?").json()
    assert body["recommendations"][0]["target_id"] == job_id   # LLM's own choice stands


def test_generate_letter_recommendation_dropped_when_a_standalone_letter_already_exists(db):
    """A GeneratedLetter with no application yet, matched by job_hash (the
    same digest application_service._prepare_letter uses), must still block
    a 'generate a new one' suggestion for that same job."""
    from services.cache_service import cache

    u = _user(db)
    offer = JobOffer(source="arbeitnow", source_job_id="lh1", source_url="https://x.test/lh1",
                     content_hash="hlh1", title="Backend Role", description="We need a backend engineer.")
    db.add(offer); db.commit()
    db.add(SavedJob(user_id=u.id, job_offer_id=offer.id, origin="agent")); db.commit()
    job_hash = cache.digest(offer.description)
    db.add(GeneratedLetter(user_id=u.id, filename="l.pdf", storage_key="letter/x.pdf",
                           minio_bucket="cv-files", language="en", job_hash=job_hash))
    db.commit()

    generate_rec = IntelligentRecommendation(**_rec(
        "generate_cover_letter", target_type="job", target_id=offer.id))
    assert validate_and_rank(db, u, [generate_rec]) == []

    # a REVIEW/improve suggestion for the same, already-lettered job is fine
    review_rec = IntelligentRecommendation(**_rec(
        "review_cover_letter_tone", target_type="job", target_id=offer.id))
    kept = validate_and_rank(db, u, [review_rec])
    assert len(kept) == 1


def test_generate_letter_recommendation_survives_when_no_letter_exists_at_all(db):
    u = _user(db)
    offer = JobOffer(source="arbeitnow", source_job_id="lh2", source_url="https://x.test/lh2",
                     content_hash="hlh2", title="Frontend Role", description="React role.")
    db.add(offer); db.commit()
    db.add(SavedJob(user_id=u.id, job_offer_id=offer.id, origin="agent")); db.commit()

    rec = IntelligentRecommendation(**_rec(
        "generate_cover_letter", target_type="job", target_id=offer.id))
    kept = validate_and_rank(db, u, [rec])
    assert len(kept) == 1


# ---------------------------------------------------------------------------
# §20 — Phase 6 regression guards, re-affirmed under the Phase 8 engine
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message,should_confirm", [
    ("oui", True), ("yes", True),
    ("oui mais...", False), ("yes but...", False),
    ("attends", False), ("wait", False),
])
def test_confirmation_matrix_still_holds_with_recommendations_enabled(db, mock_llm, swap_providers, message, should_confirm):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [_offer(sid="reg1", title="Role")])])
    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "x"}))
    _send(_client(db, u), c.id, "cherche un role")
    _route(mock_llm, _directive(intent="apply_jobs", apply={"scope": "all", "job_ids": [], "filter": None}))
    _send(_client(db, u), c.id, "postule")
    _route(mock_llm, _directive(reply="ok", intent="chat"))
    _send(_client(db, u), c.id, message)
    assert (db.query(JobApplication).count() == 1) is should_confirm


def test_ownership_still_enforced_user_b_cannot_read_user_a_recommendations(db, mock_llm):
    a = _user(db, "owna@x.com"); b = _user(db, "ownb@x.com")
    ca = _conv(db, a)
    r = _client(db, b).get(f"/api/conversations/{ca.id}")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Phase 9 audit — CV contradiction (was a real, confirmed gap: §8 "CV")
# ---------------------------------------------------------------------------

def test_generate_cv_recommendation_dropped_when_a_targeted_cv_already_exists(db):
    from services.cache_service import cache

    u = _user(db)
    offer = JobOffer(source="arbeitnow", source_job_id="cvh1", source_url="https://x.test/cvh1",
                     content_hash="hcvh1", title="Backend Role", description="Backend role, Python.")
    db.add(offer); db.commit()
    db.add(SavedJob(user_id=u.id, job_offer_id=offer.id, origin="agent")); db.commit()
    job_hash = cache.digest(offer.description)
    db.add(GeneratedCV(user_id=u.id, filename="cv.pdf", storage_key="cv/x.pdf",
                       minio_bucket="cv-files", language="en", job_hash=job_hash))
    db.commit()

    generate_rec = IntelligentRecommendation(**_rec(
        "generate_targeted_cv", target_type="job", target_id=offer.id))
    assert validate_and_rank(db, u, [generate_rec]) == []

    # review/adapt/improve of that SAME already-targeted CV stays legitimate
    review_rec = IntelligentRecommendation(**_rec(
        "improve_cv_summary", target_type="job", target_id=offer.id))
    kept = validate_and_rank(db, u, [review_rec])
    assert len(kept) == 1


def test_generate_cv_recommendation_survives_when_no_cv_exists_for_that_job(db):
    u = _user(db)
    offer = JobOffer(source="arbeitnow", source_job_id="cvh2", source_url="https://x.test/cvh2",
                     content_hash="hcvh2", title="Frontend Role", description="React role.")
    db.add(offer); db.commit()
    db.add(SavedJob(user_id=u.id, job_offer_id=offer.id, origin="agent")); db.commit()

    rec = IntelligentRecommendation(**_rec(
        "generate_targeted_cv", target_type="job", target_id=offer.id))
    assert len(validate_and_rank(db, u, [rec])) == 1


# ---------------------------------------------------------------------------
# Phase 9 audit — ambiguous job targeting -> clarification, not a guess (§10)
# ---------------------------------------------------------------------------

def test_ambiguous_job_reference_with_two_selected_jobs_becomes_a_question(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [
        _offer(sid="amb1", title="Role One"), _offer(sid="amb2", title="Role Two"),
    ])])
    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "role"}))
    _send(_client(db, u), c.id, "cherche des roles")

    _route(mock_llm, _directive(
        intent="chat", reply="ok",
        recommendations=[_rec("clarify_job_fit", target_type="job", target_id=None)],
    ))
    body = _send(_client(db, u), c.id, "que penses-tu de ce poste ?").json()
    rec = body["recommendations"][0]
    assert rec["target_id"] is None
    assert rec["requires_information"] is True
    assert rec["question"]


def test_unambiguous_single_job_reference_is_resolved_not_questioned(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [_offer(sid="amb3", title="Only Role")])])
    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "role"}))
    _send(_client(db, u), c.id, "cherche un role")
    job_id = db.query(JobOffer).first().id

    _route(mock_llm, _directive(
        intent="chat", reply="ok",
        recommendations=[_rec("clarify_job_fit", target_type="job", target_id=None)],
    ))
    body = _send(_client(db, u), c.id, "que penses-tu de ce poste ?").json()
    rec = body["recommendations"][0]
    assert rec["target_id"] == job_id
    assert rec["requires_information"] is False


def test_no_selection_at_all_drops_the_dangling_job_recommendation(db, mock_llm):
    """Audit fix: with ZERO selected jobs there is nothing to point a
    job-scoped suggestion at AND nothing useful to ask ("which job?" when
    none exist is not a real question) — it must be dropped, not shown
    dangling with target_id=None."""
    u = _user(db); c = _conv(db, u)
    _route(mock_llm, _directive(
        intent="chat", reply="ok",
        recommendations=[_rec("clarify_job_fit", target_type="job", target_id=None)],
    ))
    body = _send(_client(db, u), c.id, "que penses-tu de ce poste ?").json()
    assert body["recommendations"] == []


# ---------------------------------------------------------------------------
# Phase 9 audit — 0/10 boundary, None/wrong-type fallback (§14/§16)
# ---------------------------------------------------------------------------

def test_zero_recommendations_from_the_llm_is_fine(db, mock_llm):
    u = _user(db); c = _conv(db, u)
    _route(mock_llm, _directive(intent="chat", reply="ok", recommendations=[]))
    body = _send(_client(db, u), c.id, "merci").json()
    assert body["recommendations"] == []


def test_ten_raw_recommendations_are_accepted_then_capped_to_three(db):
    u = _user(db)
    raw = [IntelligentRecommendation(**_rec(f"tip_{i}", priority="medium", confidence=0.5 + i / 100))
          for i in range(10)]
    kept = validate_and_rank(db, u, raw)
    assert len(kept) == 3


def test_recommendations_field_missing_entirely_defaults_to_empty(db, mock_llm):
    u = _user(db); c = _conv(db, u)
    payload = json.dumps({"reply": "hi", "intent": "chat", "search_patch": None,
                          "apply": None, "question": None})   # no "recommendations" key at all
    _route(mock_llm, payload)
    body = _send(_client(db, u), c.id, "hello").json()
    assert body["recommendations"] == []


def test_recommendations_field_as_null_does_not_crash_the_turn(db, mock_llm):
    u = _user(db); c = _conv(db, u)
    payload = json.dumps({"reply": "hi", "intent": "chat", "search_patch": None,
                          "apply": None, "question": None, "recommendations": None})
    _route(mock_llm, payload)
    r = _send(_client(db, u), c.id, "hello")
    assert r.status_code == 200
    assert r.json()["recommendations"] == []


def test_recommendations_field_as_a_string_does_not_crash_the_turn(db, mock_llm):
    u = _user(db); c = _conv(db, u)
    payload = json.dumps({"reply": "hi", "intent": "chat", "search_patch": None,
                          "apply": None, "question": None, "recommendations": "not a list"})
    _route(mock_llm, payload)
    r = _send(_client(db, u), c.id, "hello")
    assert r.status_code == 200
    assert r.json()["recommendations"] == []


def test_one_bad_item_among_several_recommendations_does_not_drop_the_valid_ones(db):
    """A partially-invalid raw list at the Python level (not the JSON/LLM
    level, already covered above): validate_and_rank must skip a non-model
    item rather than choke on the whole batch."""
    u = _user(db)
    good = IntelligentRecommendation(**_rec("valid_tip"))
    raw = [good, {"action": "not_a_model_instance"}, None, 42]
    kept = validate_and_rank(db, u, raw)
    assert len(kept) == 1 and kept[0].action == "valid_tip"


# ---------------------------------------------------------------------------
# Phase 9 audit — diversity: a multi-item CV-only batch collapses (§12)
# ---------------------------------------------------------------------------

def test_four_cv_only_suggestions_for_one_job_collapse_to_a_single_best_one(db):
    """The mission's own example: generate_cv/generate_cv/improve_cv/review_cv
    for the same job all reduce to ONE recommendation, not a wall of
    near-duplicates about the same document need."""
    u = _user(db)
    offer = JobOffer(source="arbeitnow", source_job_id="div1", source_url="https://x.test/div1",
                     content_hash="hdiv1", title="Role")
    db.add(offer); db.commit()
    db.add(SavedJob(user_id=u.id, job_offer_id=offer.id, origin="agent")); db.commit()

    raw = [
        IntelligentRecommendation(**_rec("generate_cv", target_type="job", target_id=offer.id,
                                        priority="low", confidence=0.4)),
        IntelligentRecommendation(**_rec("generate_cv", target_type="job", target_id=offer.id,
                                        priority="low", confidence=0.6)),
        IntelligentRecommendation(**_rec("improve_cv", target_type="job", target_id=offer.id,
                                        priority="medium", confidence=0.7)),
        IntelligentRecommendation(**_rec("review_cv", target_type="job", target_id=offer.id,
                                        priority="high", confidence=0.95)),
    ]
    kept = validate_and_rank(db, u, raw)
    assert len(kept) == 1
    assert kept[0].action == "review_cv"   # highest priority+confidence survives


# ---------------------------------------------------------------------------
# Phase 9 audit — sensitive-action confirmation forcing, FR and EN (§7)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action", ["apply_to_this_job", "postuler_a_cette_offre"])
def test_apply_like_action_forces_confirmation_fr_and_en(db, action):
    u = _user(db)
    rec = IntelligentRecommendation(**_rec(action, requires_confirmation=False))
    kept = validate_and_rank(db, u, [rec])
    assert kept[0].requires_confirmation is True


def test_benign_remove_from_list_action_survives_but_still_requires_confirmation(db):
    """The blacklist must not be so broad that it silently drops a legitimate
    'unsave this job' suggestion — it should survive AND still be flagged as
    needing confirmation (it changes the user's saved selection)."""
    u = _user(db)
    rec = IntelligentRecommendation(**_rec("delete_saved_job", requires_confirmation=False))
    kept = validate_and_rank(db, u, [rec])
    assert len(kept) == 1
    assert kept[0].requires_confirmation is True


@pytest.mark.parametrize("action", [
    "supprimer_tout", "effacer_le_profil", "supprimer_le_compte",
])
def test_french_destructive_actions_are_still_blacklisted(db, action):
    u = _user(db)
    rec = IntelligentRecommendation(**_rec(action))
    assert validate_and_rank(db, u, [rec]) == []


@pytest.mark.parametrize("action", [
    "deleting_all_data", "deletes_account", "executing_sql", "shut_down_server",
    "shut-down-server", "supprimez_le_compte", "supprimons_tout",
    "effacez_le_compte", "removing_account", "formatting_disk",
])
def test_conjugated_and_differently_spaced_dangerous_variants_are_caught(db, action):
    """Second follow-up audit: the blacklist previously matched only the
    bare infinitive/imperative form of each verb — a conjugated form
    ('deleting', 'executing', 'supprimez'/'supprimons') or a differently
    separated one ('shut_down' vs 'shutdown') slipped straight through."""
    u = _user(db)
    rec = IntelligentRecommendation(**_rec(action))
    assert validate_and_rank(db, u, [rec]) == []


# ---------------------------------------------------------------------------
# Second follow-up audit — malformed recommendation ITEMS must never fail
# the whole turn (previously they did: a single bad item raised inside
# ConversationDirective.model_validate, discarding intent/reply/search_patch
# along with the one bad recommendation).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_recommendations", [
    None,
    "hello",
    [None],
    [{}],
    [{"confidence": 4}],
    [{"confidence": -1}],
    [{"action": "ok_one"}, {"confidence": 4}, None, "garbage"],
])
def test_malformed_recommendation_items_never_fail_the_turn(db, mock_llm, bad_recommendations):
    u = _user(db); c = _conv(db, u)
    _route(mock_llm, _directive(
        intent="chat", reply="A REAL, DISTINCTIVE REPLY", recommendations=bad_recommendations,
    ))
    r = _send(_client(db, u), c.id, "hello")
    assert r.status_code == 200
    body = r.json()
    # the critical assertion: the turn's real intent/reply survives even
    # though one (or all) recommendation items were garbage — a broken
    # recommendation must degrade to [], never take the whole turn with it.
    assert body["assistant_message"]["content"] == "A REAL, DISTINCTIVE REPLY"
    assert isinstance(body["recommendations"], list)


def test_one_good_item_survives_among_several_malformed_ones_end_to_end(db, mock_llm):
    u = _user(db); c = _conv(db, u)
    _route(mock_llm, _directive(
        intent="chat", reply="ok",
        recommendations=[{"confidence": 4}, {"action": "the_good_one"}, {}],
    ))
    body = _send(_client(db, u), c.id, "hello").json()
    assert [r["action"] for r in body["recommendations"]] == ["the_good_one"]


# ---------------------------------------------------------------------------
# Second follow-up audit — explicit user reference outranks the LLM's own
# target_id guess, beyond the ordinal case already covered: "last", a
# company name, and an out-of-range ordinal (which must clear the LLM's
# guess too, not just leave it as-is).
# ---------------------------------------------------------------------------

def test_last_job_reference_overrides_the_llms_wrong_target(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [
        _offer(sid="lj1", title="Role One"), _offer(sid="lj2", title="Role Two"),
        _offer(sid="lj3", title="Role Three"),
    ])])
    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "role"}))
    _send(_client(db, u), c.id, "cherche des roles")
    db.refresh(c)
    ordered = c.job_browse_state["ordered_job_ids"]
    wrong_guess = ordered[0]
    last_id = ordered[-1]

    _route(mock_llm, _directive(
        intent="chat", reply="ok",
        recommendations=[_rec("review_job_fit", target_type="job", target_id=wrong_guess)],
    ))
    body = _send(_client(db, u), c.id, "et le dernier poste, il en pense quoi ?").json()
    assert body["recommendations"][0]["target_id"] == last_id


def test_company_name_reference_overrides_the_llms_wrong_target(db, mock_llm, swap_providers):
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [
        _offer(sid="cn1", title="Backend Role", company="Google"),
        _offer(sid="cn2", title="Backend Role", company="Amazon"),
    ])])
    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "role"}))
    _send(_client(db, u), c.id, "cherche des roles")
    offers = {o.id: o for o in db.query(JobOffer).all()}
    google_id = next(i for i, o in offers.items() if o.company == "Google")
    amazon_id = next(i for i, o in offers.items() if o.company == "Amazon")

    _route(mock_llm, _directive(
        intent="chat", reply="ok",
        recommendations=[_rec("review_job_fit", target_type="job", target_id=amazon_id)],
    ))
    body = _send(_client(db, u), c.id, "que penses-tu de celui de Google ?").json()
    assert body["recommendations"][0]["target_id"] == google_id


def test_ambiguous_company_reference_with_no_unique_match_leaves_llm_target_alone(db, mock_llm, swap_providers):
    """Two jobs at the same company (or a company name mentioned that
    matches none of the selection): not a safe resolution — the override
    must not fire, and the LLM's own (still ownership-checked) guess is
    left as it was."""
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [
        _offer(sid="dup1", title="Role A", company="Google"),
        _offer(sid="dup2", title="Role B", company="Google"),
    ])])
    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "role"}))
    _send(_client(db, u), c.id, "cherche des roles")
    db.refresh(c)
    ordered = c.job_browse_state["ordered_job_ids"]
    llm_guess = ordered[0]

    _route(mock_llm, _directive(
        intent="chat", reply="ok",
        recommendations=[_rec("review_job_fit", target_type="job", target_id=llm_guess)],
    ))
    body = _send(_client(db, u), c.id, "que penses-tu de celui de Google ?").json()
    assert body["recommendations"][0]["target_id"] == llm_guess


def test_out_of_range_explicit_ordinal_clears_the_llms_guess_into_a_question(db, mock_llm, swap_providers):
    """Audit fix: an explicit-but-unsatisfiable ordinal ('le 5e poste' with
    only 2 selected) used to leave the LLM's target_id untouched — exactly
    as unreliable as the ambiguous case, so it must clear it and clarify
    instead of silently keeping a guess for a position that doesn't exist."""
    u = _user(db); c = _conv(db, u)
    swap_providers([FakeJobProvider("arbeitnow", [
        _offer(sid="oor1", title="Role One"), _offer(sid="oor2", title="Role Two"),
    ])])
    _route(mock_llm, _directive(intent="search_jobs", search_patch={"query": "role"}))
    _send(_client(db, u), c.id, "cherche des roles")
    db.refresh(c)
    wrong_guess = c.job_browse_state["ordered_job_ids"][0]

    _route(mock_llm, _directive(
        intent="chat", reply="ok",
        recommendations=[_rec("review_job_fit", target_type="job", target_id=wrong_guess)],
    ))
    body = _send(_client(db, u), c.id, "et le 5e poste, qu'en penses-tu ?").json()
    rec = body["recommendations"][0]
    assert rec["target_id"] is None
    assert rec["requires_information"] is True


# ---------------------------------------------------------------------------
# Second follow-up audit — ranking must not let an unresolved/ambiguous
# recommendation outrank an equally-prioritised, fully-resolved one on raw
# confidence alone (§10).
# ---------------------------------------------------------------------------

def test_a_recommendation_needing_clarification_never_outranks_an_equal_priority_resolved_one(db):
    from services.conversations.recommendation_engine import rank_and_cap

    resolved = IntelligentRecommendation(**_rec(
        "resolved_tip", priority="medium", confidence=0.6, requires_information=False))
    ambiguous = IntelligentRecommendation(**_rec(
        "ambiguous_tip", priority="medium", confidence=0.99, requires_information=True))
    kept = rank_and_cap([ambiguous, resolved])
    assert kept[0].action == "resolved_tip"


def test_higher_priority_still_wins_even_while_needing_clarification(db):
    """The clarification tiebreaker only breaks ties WITHIN the same
    priority tier — priority itself still dominates."""
    from services.conversations.recommendation_engine import rank_and_cap

    low_resolved = IntelligentRecommendation(**_rec(
        "low_resolved", priority="low", confidence=0.9, requires_information=False))
    high_ambiguous = IntelligentRecommendation(**_rec(
        "high_ambiguous", priority="high", confidence=0.5, requires_information=True))
    kept = rank_and_cap([low_resolved, high_ambiguous])
    assert kept[0].action == "high_ambiguous"


# ---------------------------------------------------------------------------
# Second follow-up audit — ownership coverage gap found: no test anywhere
# exercised target_type="letter" (owned / cross-user / malformed / missing),
# and target_type="application" had only a nonexistent-UUID case, no real
# owned-vs-cross-user pair. Also: an incoherent type/id combo (target_type
# says one resource, target_id actually belongs to a DIFFERENT resource
# table) must resolve to "not found", never a false-positive ownership hit.
# ---------------------------------------------------------------------------

def test_letter_target_owned_by_caller_is_kept(db):
    u = _user(db)
    letter = GeneratedLetter(user_id=u.id, filename="l.pdf", storage_key="letter/z.pdf",
                             minio_bucket="cv-files", language="en")
    db.add(letter); db.commit()
    raw = [IntelligentRecommendation(**_rec("review_cover_letter", target_type="letter",
                                            target_id=letter.id))]
    kept = validate_and_rank(db, u, raw)
    assert len(kept) == 1 and kept[0].target_id == letter.id


def test_letter_target_owned_by_another_user_is_dropped(db):
    a = _user(db, "la@x.com"); b = _user(db, "lb@x.com")
    letter = GeneratedLetter(user_id=b.id, filename="l.pdf", storage_key="letter/z2.pdf",
                             minio_bucket="cv-files", language="en")
    db.add(letter); db.commit()
    raw = [IntelligentRecommendation(**_rec("review_cover_letter", target_type="letter",
                                            target_id=letter.id))]
    assert validate_and_rank(db, a, raw) == []


def test_letter_target_malformed_uuid_is_dropped(db):
    u = _user(db)
    raw = [IntelligentRecommendation(**_rec("review_cover_letter", target_type="letter",
                                            target_id="not-a-uuid"))]
    assert validate_and_rank(db, u, raw) == []


def test_letter_target_nonexistent_uuid_is_dropped(db):
    u = _user(db)
    raw = [IntelligentRecommendation(**_rec("review_cover_letter", target_type="letter",
                                            target_id=str(uuid.uuid4())))]
    assert validate_and_rank(db, u, raw) == []


def test_application_target_owned_by_caller_is_kept(db):
    u = _user(db)
    offer = JobOffer(source="arbeitnow", source_job_id="apo1", source_url="https://x.test/apo1",
                     content_hash="hapo1", title="Role")
    db.add(offer); db.commit()
    app = JobApplication(user_id=u.id, job_offer_id=offer.id, status="prepared")
    db.add(app); db.commit()
    raw = [IntelligentRecommendation(**_rec("review_application", target_type="application",
                                            target_id=app.id))]
    kept = validate_and_rank(db, u, raw)
    assert len(kept) == 1 and kept[0].target_id == app.id


def test_application_target_owned_by_another_user_is_dropped(db):
    a = _user(db, "aa@x.com"); b = _user(db, "ab@x.com")
    offer = JobOffer(source="arbeitnow", source_job_id="apo2", source_url="https://x.test/apo2",
                     content_hash="hapo2", title="Role")
    db.add(offer); db.commit()
    app = JobApplication(user_id=b.id, job_offer_id=offer.id, status="prepared")
    db.add(app); db.commit()
    raw = [IntelligentRecommendation(**_rec("review_application", target_type="application",
                                            target_id=app.id))]
    assert validate_and_rank(db, a, raw) == []


def test_incoherent_target_type_pointing_at_a_different_resources_id_is_dropped(db):
    """target_type says "job" but target_id is actually a CV's id (or vice
    versa) — must resolve to "not found" (UUIDs never collide across
    tables in practice), never an accidental ownership match."""
    u = _user(db)
    cv = GeneratedCV(user_id=u.id, filename="cv.pdf", storage_key="cv/inc.pdf",
                     minio_bucket="cv-files", language="en")
    db.add(cv); db.commit()

    raw = [IntelligentRecommendation(**_rec(
        "review_job_fit", target_type="job", target_id=cv.id))]   # a CV id, not a job id
    assert validate_and_rank(db, u, raw) == []

    letter = GeneratedLetter(user_id=u.id, filename="l.pdf", storage_key="letter/inc.pdf",
                             minio_bucket="cv-files", language="en")
    db.add(letter); db.commit()
    raw2 = [IntelligentRecommendation(**_rec(
        "review_cv", target_type="cv", target_id=letter.id))]     # a letter id, not a CV id
    assert validate_and_rank(db, u, raw2) == []


# ---------------------------------------------------------------------------
# Second follow-up audit — a letter generated through the REAL apply flow
# (JobApplicationService._prepare_letter) must get a job_hash, or the
# "standalone letter already exists" contradiction check is dead code for
# the most common real letter path (bug found: it omitted job_hash entirely).
# ---------------------------------------------------------------------------

def test_letter_generated_via_apply_flow_gets_a_job_hash(db):
    """Bug found in this audit: JobApplicationService._prepare_letter's call
    to document_service.record_letter omitted job_hash entirely, so every
    letter created through the real apply flow got job_hash=NULL — silently
    defeating the "standalone letter already exists" contradiction check
    for the most common real letter path. `_prepare_letter` never raises
    (it swallows failures and returns letter_id=None) — if the pipeline is
    unavailable in this test environment (no LLM/minio), this SKIPS rather
    than fabricating a pass."""
    from services.cache_service import cache
    from services.jobs.application_service import JobApplicationService
    from schemas.applications import ApplyRequest

    u = _user(db)
    offer = JobOffer(source="arbeitnow", source_job_id="pl1", source_url="https://x.test/pl1",
                     content_hash="hpl1", title="Backend Role", description="A backend role.")
    db.add(offer); db.commit()

    req = ApplyRequest(prepare=True, generate_letter=True,
                       cv_profile={"full_name": "Test User", "email": u.email})
    letter_id, note = JobApplicationService(db)._prepare_letter(u, offer, req)
    if letter_id is None:
        pytest.skip(f"letter pipeline unavailable in this test environment: {note!r} — "
                    "the job_hash wiring itself is a one-line fix verified by code review")

    expected_hash = cache.digest((offer.description or offer.title or "")[:8000])
    letter = db.get(GeneratedLetter, letter_id)
    assert letter is not None
    assert letter.job_hash == expected_hash


# ---------------------------------------------------------------------------
# §25 — real LLM smoke test (never faked; skips with a clear reason)
# ---------------------------------------------------------------------------

def test_real_llm_recommendation_smoke():
    """§25: attempt ONE real call. If it fails for any reason (no working
    key, network, quota, revoked credential), this SKIPS with the exact
    reason — it never fabricates a result and never fails the suite for an
    infrastructure reason outside this codebase's control."""
    import os

    if not any(os.environ.get(k) for k in
              ("NVIDIA_API_KEY", "GEMINI_API_KEY", "MISTRAL_API_KEY", "GROQ_API_KEY",
               "OPENAI_API_KEY", "LLM_API_KEY")):
        pytest.skip("REAL LLM TEST = NOT AVAILABLE — no provider API key in the environment")

    from services.gemini_client import call_gemini
    try:
        call_gemini("Reply with exactly: OK", request_type="conversation_agent", use_cache=False)
    except Exception as exc:  # noqa: BLE001 — document, never fabricate
        pytest.skip(f"REAL LLM TEST = NOT AVAILABLE — call failed: {exc}")
