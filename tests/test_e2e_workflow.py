"""
Phase 3 — END-TO-END candidate workflow.

One real journey through the HTTP API, real auth (signup + Bearer token),
real services, real DB, in-memory MinIO, deterministic LLM (mock_llm):

    LOGIN → PROFILE → UPLOAD CV → EXTRACTION → STRUCTURED PROFILE →
    JOB SEARCH → JOB SELECTION → MATCHING → TARGETED CV → COVER LETTER →
    ANTI-HALLUCINATION CHECK → APPLICATION → ASSOCIATION → RETRIEVED → VERIFIED

Plus, in the same file: owner-scoping E2E (User A vs User B), versioning in
the workflow (CV_x v1→v2→v3), the conversational edit agent (+ anti-hallucination
refusal), an LLM provider-failure → fallback scenario, and the Mistral re-ping
result.
"""

from __future__ import annotations

import io
import json

import pytest

from _jobs_helpers import FakeJobProvider, make_offer as _offer
from tests.conftest import _default_llm_router


def _directive_router(directive: dict):
    """A mock_llm router that returns `directive` for the agent turn and the
    normal defaults for everything else."""
    base = {"reply": "", "intent": "chat", "search_patch": None, "apply": None, "question": None}
    base.update(directive)
    payload = json.dumps(base)

    def r(prompt, *, request_type, use_cache=True, **kw):
        if request_type == "conversation_agent":
            return payload
        return _default_llm_router(prompt, request_type=request_type, use_cache=use_cache)
    return r


def _seed_selected_offer(db, user_id, *, title="Backend Engineer", description="Python + PostgreSQL.",
                         city="Casablanca", country="Morocco", sid="e2e1"):
    """Directly seed a JobOffer the agent 'retained' — for E2E branches that
    aren't exercising the search step itself."""
    from datetime import datetime, timezone
    from models import JobOffer, SavedJob
    now = datetime.now(timezone.utc)
    o = JobOffer(source="arbeitnow", source_job_id=sid, content_hash=f"h{sid}{user_id[:6]}",
                 source_url=f"https://arbeitnow.example/{sid}", title=title, description=description,
                 city=city, country=country, first_seen_at=now, scraped_at=now,
                 last_verified_at=now, is_active=True, freshness="fresh")
    db.add(o); db.commit(); db.refresh(o)
    db.add(SavedJob(user_id=user_id, job_offer_id=o.id, origin="agent"))
    db.commit()
    return o.id


CV_TEXT = (
    "MEHDI BENNANI\n"
    "mehdi.bennani@example.com | +212 600 445566 | linkedin.com/in/mbennani\n"
    "Address: Casablanca, Morocco\n\n"
    "PROFESSIONAL SUMMARY\n"
    "Backend engineer with 3 years building Python data services.\n\n"
    "WORK EXPERIENCE\n"
    "Backend Engineer, Atlas Data - Casablanca\n"
    "Jan 2022 - Present\n"
    "- Built the ingestion service handling 2M events/day\n"
    "- Cut the nightly batch from 40 min to 12 min\n\n"
    "EDUCATION\n"
    "Ingenieur d'Etat en Informatique, ENSIAS, 2021\n\n"
    "SKILLS\n"
    "Languages: Python, SQL\n"
    "Databases: PostgreSQL, Redis\n\n"
    "LANGUAGES\n"
    "French (Native), English (Fluent)\n"
)


@pytest.fixture
def e2e_client(db):
    """A TestClient with ONLY get_db overridden — real signup / JWT / auth."""
    from fastapi.testclient import TestClient

    import main
    from database import get_db

    main.app.dependency_overrides[get_db] = lambda: db
    c = TestClient(main.app)
    yield c
    main.app.dependency_overrides.clear()


def _signup(client, email="mehdi@example.com", password="Str0ngPass123"):
    r = client.post("/api/auth/signup", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _me_id(client, headers: dict) -> str:
    return client.get("/api/auth/me", headers=headers).json()["id"]


def _to_cv_profile(extracted: dict) -> dict:
    """The transform a frontend review form does: ExtractedCVProfile (flat
    skills) -> CVProfile (categorised skills, typed gpa)."""
    p = {k: v for k, v in extracted.items()
         if k in ("name", "email", "phone", "linkedin", "github", "portfolio",
                  "address", "nationality", "professional_summary",
                  "experience", "projects", "languages", "certifications")}
    p["skills"] = ([{"category": "Skills", "skills": extracted["skills"]}]
                   if extracted.get("skills") else [])
    p["education"] = []
    for e in extracted.get("education", []):
        e = dict(e)
        g = e.get("gpa")
        e["gpa"] = g if isinstance(g, (int, float)) else None
        p["education"].append(e)
    p.setdefault("phone", "+000")
    return p


# ===========================================================================
# THE FULL WORKFLOW
# ===========================================================================

def test_full_candidate_workflow(e2e_client, mock_llm, db):
    c = e2e_client
    steps: list[tuple[str, bool]] = []

    def step(name, ok):
        steps.append((name, bool(ok)))
        assert ok, f"E2E broke at step: {name}"

    # 1 — signup / login
    token = _signup(c)
    h = _auth(token)
    me = c.get("/api/auth/me", headers=h)
    step("1_login", me.status_code == 200 and me.json()["email"] == "mehdi@example.com")
    user_id = me.json()["id"]

    # 2 — profile (empty at first)
    prof = c.get("/api/profile", headers=h)
    step("2_get_profile", prof.status_code == 200)
    # seed the job-search profile so matching has something to work with
    put = c.put("/api/profile", headers=h, json={
        "target_titles": ["Backend Engineer"],
        "skills": ["Python", "PostgreSQL", "Redis"],
        "locations": ["Casablanca"], "languages": ["en"],
    })
    step("2b_put_profile", put.status_code == 200)

    # 3 + 4 — upload CV → extraction
    files = {"file": ("mehdi_cv.txt", io.BytesIO(CV_TEXT.encode("utf-8")), "text/plain")}
    ex = c.post("/api/extract-cv", headers=h, files=files, data={"language": "en"})
    step("3_4_upload_and_extract", ex.status_code == 200)
    extracted = ex.json()["cv_profile"]
    cv_profile = _to_cv_profile(extracted)

    # 5 — verify the structured profile (real data, locally extracted, nothing fabricated)
    step("5_structured_profile",
         extracted["name"] in ("MEHDI BENNANI", "Mehdi Bennani")
         and extracted["email"] == "mehdi.bennani@example.com"
         and "Python" in extracted["skills"]
         and extracted["address"] and "Casablanca" in extracted["address"])

    # 6 — job search DRIVEN BY THE AGENT (no manual search API). The agent runs
    # JobSearchService internally and adds the results to the user's dashboard.
    from services.providers.registry import registry
    _orig = dict(registry._providers)
    registry._providers = {"arbeitnow": FakeJobProvider("arbeitnow", [_offer(
        title="Senior Backend Engineer",
        description="We need Python, FastAPI, PostgreSQL, Redis, Docker, Kubernetes. "
                    "Bonus: Kafka. English required.",
        city="Casablanca", country="Morocco",
    )])}
    try:
        conv_id = c.post("/api/conversations", headers=h, json={}).json()["id"]
        mock_llm.set(_directive_router({
            "reply": "I found a matching role and added it to your dashboard.",
            "intent": "search_jobs",
            "search_patch": {"query": "backend engineer", "city": "Casablanca",
                             "remote_type": "remote", "experience_level": "mid"},
        }))
        msg = c.post(f"/api/conversations/{conv_id}/messages", headers=h, json={
            "content": "Je cherche des postes Backend Python à distance avec au moins 3 ans d'expérience."})
        mock_llm.set(_default_llm_router)          # back to defaults for later turns
        step("6_agent_search", msg.status_code == 200 and len(msg.json()["jobs_found"]) >= 1)

        # 6b — the retained job is on the dashboard
        dash = c.get("/api/dashboard/jobs", headers=h)
        step("6b_dashboard_jobs",
             dash.status_code == 200 and dash.json()["total"] >= 1
             and dash.json()["jobs"][0]["application"] is None)
        offer_id = dash.json()["jobs"][0]["job_offer_id"]

        # 7 — job detail (scoped to the user's selection)
        detail = c.get(f"/api/jobs/{offer_id}", headers=h)
        step("7_job_detail",
             detail.status_code == 200 and detail.json()["job"]["id"] == offer_id)
        job_description = detail.json()["job"]["description"]

        # 8 — matching candidate ↔ offer
        from schemas.jobs import JobSearchContext
        from services.jobs.match_service import evaluate
        from models import JobOffer
        match = evaluate(db.get(JobOffer, offer_id),
                         JobSearchContext(query="backend engineer",
                                          skills=["Python", "PostgreSQL", "Redis"]))
        step("8_matching", 0.0 <= match.score <= 1.0 and "Python" in " ".join(match.reasons))

        # 9 — targeted CV
        gcv = c.post("/api/generate-cv", headers=h, json={
            "cv_profile": cv_profile, "job_description": job_description, "language": "en",
        })
        step("9_targeted_cv", gcv.status_code == 200)
        cv_ref = gcv.json()["reference"]
        cv_id = gcv.json()["generated_cv_id"]
        step("9b_cv_reference", cv_ref.startswith("CV_") and gcv.json()["version"] == 1)

        # 10 — cover letter
        glt = c.post("/api/generate-letter", headers=h, json={
            "cv_profile": cv_profile, "job_description": job_description, "language": "en",
        })
        step("10_cover_letter", glt.status_code == 200)
        letter_ref = glt.json()["reference"]
        letter_id = glt.json()["generated_letter_id"]
        step("10b_letter_reference", letter_ref.startswith("LETTER_"))

        # 11 — anti-hallucination: the JD asks for Docker/Kubernetes/Kafka which
        # the candidate does NOT have — none may appear in the generated CV body.
        from models import GeneratedCV
        src = json.loads(db.query(GeneratedCV).filter_by(id=cv_id).one().structured_source)
        body = src["summary"]["professional_summary"] + " " + " ".join(
            b for e in src["experience"] for b in e["bullets"])
        low = body.lower()
        injected = [t for t in ("kubernetes", "docker", "kafka", "terraform") if t in low]
        step("11_anti_hallucination", injected == [])
        # they may appear as SEPARATE suggestions
        step("11b_additional_skills_separate", isinstance(gcv.json()["additional_skills"], list))

        # 11c — Phase 4 skill analysis (Case B: a JD was supplied)
        sa = gcv.json()["skill_analysis"]
        matched = {m["skill"].lower() for m in sa["matched"]} if sa else set()
        step("11c_skill_analysis_matched", sa is not None and "python" in matched)
        step("11d_missing_not_can_not_do",
             any(s.lower() == "kubernetes" for s in sa["missing"]))
        step("11e_recommended_actions_are_suggestions",
             any(a["type"] == "confirm_skill" and a["skill"].lower() == "kubernetes"
                 for a in gcv.json()["recommended_actions"]))

        # 12 + 13 + 14 — prepare + submit the application (CV + letter + offer + user)
        app = c.post(f"/api/jobs/{offer_id}/apply", headers=h, json={
            "cv_id": cv_id, "letter_id": letter_id, "prepare": True,
            "context": "I built a similar ingestion service at Atlas Data.",
        })
        step("12_14_application_posted", app.status_code == 200)
        ab = app.json()
        application_id = ab["application_id"]
        step("13_association",
             ab["cv_id"] == cv_id and ab["letter_id"] == letter_id and ab["job_id"] == offer_id)
        step("13b_status_prepared", ab["application_status"] == "prepared")
        step("13c_never_submitted", ab["application_status"] != "submitted")

        # 15 — the application really exists
        lst = c.get("/api/jobs/applications", headers=h)
        step("15_application_retrieved",
             lst.status_code == 200
             and any(a["id"] == application_id for a in lst.json()))

        # 16 — verify its status + ownership + associations
        one = c.get(f"/api/jobs/applications/{application_id}", headers=h)
        ob = one.json()
        step("16_application_verified",
             one.status_code == 200
             and ob["status"] == "prepared"
             and ob["cv_id"] == cv_id
             and ob["letter_id"] == letter_id
             and ob["job_offer_id"] == offer_id
             and ob["match_score"] is not None)

        # ownership at the DB level
        from models import JobApplication
        row = db.query(JobApplication).filter_by(id=application_id).one()
        step("16b_owner_scoped", row.user_id == user_id)

    finally:
        registry._providers = _orig

    print("\nE2E workflow steps:")
    for name, ok in steps:
        print(f"  {'OK ' if ok else 'FAIL'} {name}")
    assert all(ok for _, ok in steps)


# ===========================================================================
# OWNER-SCOPING — User A must never reach User B's data
# ===========================================================================

def test_e2e_owner_scoping_user_a_vs_user_b(e2e_client, mock_llm, db):
    c = e2e_client
    ta = _signup(c, "alice@example.com")
    tb = _signup(c, "bob@example.com")
    ha, hb = _auth(ta), _auth(tb)

    profile = {
        "name": "Alice", "email": "alice@example.com", "phone": "+1 555 0000",
        "experience": [], "education": [], "projects": [], "skills": [],
        "languages": [], "certifications": [],
    }
    a_cv = c.post("/api/generate-cv", headers=ha,
                  json={"cv_profile": profile, "language": "en"}).json()
    a_letter = c.post("/api/generate-letter", headers=ha,
                      json={"cv_profile": profile, "job_description": "JD", "language": "en"}).json()
    a_ref, a_cv_id = a_cv["reference"], a_cv["generated_cv_id"]
    a_letter_id = a_letter["generated_letter_id"]

    # B tries to read / download / version / delete A's CV
    assert c.get(f"/api/cvs/{a_cv_id}", headers=hb).status_code == 403
    assert c.get(f"/api/cvs/{a_ref}", headers=hb).status_code == 403
    assert c.get(f"/api/cvs/{a_cv_id}/download", headers=hb).status_code == 403
    assert c.get(f"/api/cvs/{a_ref}/versions", headers=hb).status_code == 403
    assert c.get(f"/api/cvs/{a_ref}/versions/1/download", headers=hb).status_code == 403
    assert c.delete(f"/api/cvs/{a_cv_id}", headers=hb).status_code == 403
    # B tries A's letter
    assert c.get(f"/api/letters/{a_letter_id}/download", headers=hb).status_code == 403
    assert c.get(f"/api/letters/{a_letter['reference']}/versions", headers=hb).status_code == 403
    # B cannot apply with A's CV / letter
    oid_b = _seed_selected_offer(db, _me_id(c, hb), sid="scope-b")
    r = c.post(f"/api/jobs/{oid_b}/apply", headers=hb,
               json={"cv_id": a_cv_id, "letter_id": a_letter_id})
    assert r.status_code == 403

    # B cannot see A's application
    oid_a = _seed_selected_offer(db, _me_id(c, ha), sid="scope-a")
    app_id = c.post(f"/api/jobs/{oid_a}/apply", headers=ha,
                    json={"cv_id": a_cv_id}).json()["application_id"]
    assert c.get(f"/api/jobs/applications/{app_id}", headers=hb).status_code == 403
    assert all(a["id"] != app_id for a in c.get("/api/jobs/applications", headers=hb).json())

    # unknown reference / version
    assert c.get("/api/cvs/CV_ZZZZZZ/versions", headers=ha).status_code == 404
    assert c.get(f"/api/cvs/{a_ref}/versions/99/download", headers=ha).status_code == 404

    # B edits A's document by reference through the agent -> refused (403 surfaced in reply)
    conv = c.post("/api/conversations", headers=hb, json={}).json()
    edit = c.post(f"/api/conversations/{conv['id']}/messages", headers=hb,
                  json={"content": f"{a_ref} change my summary"})
    assert edit.status_code == 200
    assert edit.json()["document"] is None   # nothing created for B


# ===========================================================================
# VERSIONING IN THE WORKFLOW — CV_x v1 → v2 → v3
# ===========================================================================

def test_e2e_versioning_in_workflow(e2e_client, mock_llm, db):
    c = e2e_client
    h = _auth(_signup(c, "vers@example.com"))
    profile = {
        "name": "Vera", "email": "vera@example.com", "phone": "+1 555 1",
        "experience": [{"company": "Acme", "position": "Engineer", "period": "2021-2023",
                        "achievements": ["Shipped billing"], "technologies": ["Python"]}],
        "education": [], "projects": [],
        "skills": [{"category": "Languages", "skills": ["Python", "SQL"]}],
        "languages": [], "certifications": [],
    }
    v1 = c.post("/api/generate-cv", headers=h, json={"cv_profile": profile, "language": "en"}).json()
    ref = v1["reference"]
    v2 = c.post("/api/generate-cv", headers=h,
                json={"cv_profile": profile, "job_description": "Python role", "language": "en",
                      "reference": ref}).json()
    v3 = c.post("/api/generate-cv", headers=h,
                json={"cv_profile": profile, "language": "fr", "reference": ref}).json()

    assert v2["reference"] == ref and v3["reference"] == ref
    assert [v1["version"], v2["version"], v3["version"]] == [1, 2, 3]

    versions = c.get(f"/api/cvs/{ref}/versions", headers=h).json()
    assert [v["version"] for v in versions] == [1, 2, 3]
    assert versions[-1]["is_latest"] and not versions[0]["is_latest"]
    for n in (1, 2, 3):                                   # every old version still downloadable
        assert c.get(f"/api/cvs/{ref}/versions/{n}/download", headers=h).status_code == 200

    # structured_source correct per version (v3 is French)
    from models import GeneratedCV
    rows = {r.version: r for r in db.query(GeneratedCV).filter_by(reference=ref).all()}
    assert json.loads(rows[3].structured_source)["language"] == "fr"
    assert json.loads(rows[1].structured_source)["language"] == "en"

    # associate a SPECIFIC version to an application (v3's row id)
    oid = _seed_selected_offer(db, _me_id(c, h), sid="vers1")
    r = c.post(f"/api/jobs/{oid}/apply", headers=h, json={"cv_id": rows[3].id})
    assert r.status_code == 200 and r.json()["cv_id"] == rows[3].id


# ===========================================================================
# CONVERSATIONAL EDIT AGENT IN THE WORKFLOW (+ anti-hallucination)
# ===========================================================================

def test_e2e_agent_edit_and_anti_hallucination(e2e_client, mock_llm, db):
    c = e2e_client
    h = _auth(_signup(c, "agent@example.com"))
    profile = {
        "name": "Ada", "email": "ada@example.com", "phone": "+1 555 2",
        "experience": [{"company": "Acme", "position": "Engineer", "period": "2021-2023",
                        "achievements": ["Built the API"], "technologies": ["Python"]}],
        "education": [], "projects": [],
        "skills": [{"category": "Languages", "skills": ["Python"]}],
        "languages": [], "certifications": [],
    }
    ref = c.post("/api/generate-cv", headers=h,
                 json={"cv_profile": profile, "language": "en"}).json()["reference"]
    conv = c.post("/api/conversations", headers=h, json={}).json()["id"]

    from tests.conftest import _default_llm_router

    # --- anti-hallucination: "add Kubernetes" but it's nowhere -> refused, NO new version
    def refuse_router(prompt, *, request_type, use_cache=True, **kw):
        if request_type == "agent_edit":
            base = {"op": "add_skill", "value": None, "category": "Cloud", "skill": "Kubernetes",
                    "from_name": None, "to_name": None, "role_index": None, "bullet_index": None,
                    "order": [], "recipient": None, "reply": "Added Kubernetes."}
            return json.dumps(base)
        return _default_llm_router(prompt, request_type=request_type, use_cache=use_cache)

    mock_llm.set(refuse_router)
    r = c.post(f"/api/conversations/{conv}/messages", headers=h,
               json={"content": f"{ref} add Kubernetes to my skills"})
    assert r.status_code == 200
    body = r.json()
    assert body["document"] is None
    reply = body["assistant_message"]["content"].lower()
    assert "isn't in your profile" in reply and "doesn't mean you don't know it" in reply
    # Phase 5 — the agent asks for CONTEXT (where/when), it does not act
    ra = body["recommended_actions"]
    assert ra and ra[0]["type"] == "provide_skill_context" and ra[0]["skill"] == "Kubernetes"
    assert "experience" in (ra[0]["question"] or "").lower()
    assert len(c.get(f"/api/cvs/{ref}/versions", headers=h).json()) == 1   # no v2

    # --- legitimate edit explicitly provided by the user -> applied, new version
    def apply_router(prompt, *, request_type, use_cache=True, **kw):
        if request_type == "agent_edit":
            base = {"op": "add_skill", "value": None, "category": "Languages", "skill": "Go",
                    "from_name": None, "to_name": None, "role_index": None, "bullet_index": None,
                    "order": [], "recipient": None, "reply": "Added Go to Languages."}
            return json.dumps(base)
        return _default_llm_router(prompt, request_type=request_type, use_cache=use_cache)

    mock_llm.set(apply_router)
    r2 = c.post(f"/api/conversations/{conv}/messages", headers=h,
                json={"content": f"{ref} add Go, I use it daily at work"})
    assert r2.json()["document"]["reference"] == ref
    assert r2.json()["document"]["version"] == 2

    versions = c.get(f"/api/cvs/{ref}/versions", headers=h).json()
    assert [v["version"] for v in versions] == [1, 2]
    assert c.get(f"/api/cvs/{ref}/versions/2/download", headers=h).status_code == 200
    # the new version's structured source contains Go, not Kubernetes
    from models import GeneratedCV
    latest = db.query(GeneratedCV).filter_by(reference=ref, version=2).one()
    skills = [s for cat in json.loads(latest.structured_source)["skills"] for s in cat["skills"]]
    assert "Go" in skills and "Kubernetes" not in skills


# ===========================================================================
# AGENT-DRIVEN JOBS — search → dashboard → propose → confirm → apply (Phase 6)
# ===========================================================================

def test_e2e_agent_jobs_search_propose_confirm_apply(e2e_client, mock_llm, db):
    c = e2e_client
    h = _auth(_signup(c, "agentjobs@example.com"))
    uid = _me_id(c, h)

    from services.providers.registry import registry
    _orig = dict(registry._providers)
    registry._providers = {"arbeitnow": FakeJobProvider("arbeitnow", [
        _offer(sid="j1", title="Backend Python Engineer", company="Globex",
               description="Python, FastAPI, PostgreSQL. Remote, France."),
        _offer(sid="j2", title="Senior Backend Engineer", company="Initech",
               description="Python, Django. Remote."),
        _offer(sid="j3", title="Data Engineer", company="Umbrella",
               description="Spark, Python."),
    ])}
    try:
        conv = c.post("/api/conversations", headers=h, json={}).json()["id"]

        # 1 — search, agent-driven (JobSearchService runs internally)
        mock_llm.set(_directive_router({
            "intent": "search_jobs", "reply": "Found some roles.",
            "search_patch": {"query": "backend python", "remote_type": "remote", "country": "France"},
        }))
        s = c.post(f"/api/conversations/{conv}/messages", headers=h,
                   json={"content": "Trouve-moi des postes Backend Python à distance en France, 3 ans d'xp"})
        assert s.status_code == 200
        assert len(s.json()["jobs_found"]) == 3
        from models import SavedJob, JobApplication
        assert db.query(SavedJob).filter_by(user_id=uid, origin="agent").count() == 3

        # 2 — dashboard shows them, no application yet
        dash = c.get("/api/dashboard/jobs", headers=h).json()
        assert dash["total"] == 3 and all(j["application"] is None for j in dash["jobs"])

        # 3 — "apply to jobs 1 and 2" → PROPOSAL ONLY, nothing applied
        mock_llm.set(_directive_router({
            "intent": "apply_jobs", "reply": "I'll apply to those two — confirm?",
            "apply": {"scope": "ids", "job_ids": ["1", "2"], "filter": None},
        }))
        p = c.post(f"/api/conversations/{conv}/messages", headers=h,
                   json={"content": "postule aux offres 1 et 2"})
        assert p.json()["pending_confirmation"] is not None
        assert len(p.json()["pending_confirmation"]["job_ids"]) == 2
        assert p.json()["applications"] == []
        assert db.query(JobApplication).count() == 0

        # 4 — deterministic "oui" → applies EXACTLY the frozen set (LLM not called)
        mock_llm.set(lambda *a, **k: (_ for _ in ()).throw(AssertionError("no LLM on confirm")))
        d = c.post(f"/api/conversations/{conv}/messages", headers=h, json={"content": "oui"})
        assert len(d.json()["applications"]) == 2
        assert db.query(JobApplication).filter_by(user_id=uid).count() == 2
        mock_llm.set(_default_llm_router)

        # 5 — dashboard now reflects the applications
        dash2 = c.get("/api/dashboard/jobs", headers=h).json()
        applied = [j for j in dash2["jobs"] if j["application"] is not None]
        assert len(applied) == 2
        assert all(j["application"]["status"] in ("prepared", "manual_required") for j in applied)

        # 6 — user B cannot see or confirm anything of A's
        hb = _auth(_signup(c, "otherjobs@example.com"))
        assert c.get("/api/dashboard/jobs", headers=hb).json()["total"] == 0
        assert c.post(f"/api/conversations/{conv}/messages", headers=hb,
                      json={"content": "oui"}).status_code == 403
    finally:
        registry._providers = _orig


# ===========================================================================
# LLM PROVIDER FAILURE → FALLBACK in a real generation
# ===========================================================================

def test_e2e_llm_provider_failure_then_fallback(e2e_client, monkeypatch, db):
    """Primary provider 429s on every call; routing fails over to the fallback;
    the CV still generates, profiling records the WINNING provider, no double
    retry, no corrupted output."""
    from config import settings
    from services import gemini_client, profiling
    from services.llm import circuit as circ
    from services.llm import providers as prov
    from services.llm.base import Completion, LLMError

    monkeypatch.setattr(settings, "LLM_ROUTING_ENABLED", True)
    monkeypatch.setattr(settings, "GROQ_API_KEY", "k")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "k")
    circ.reset()

    calls = {"groq": 0, "gemini": 0}

    class _P:
        def __init__(self, name, fail): self.name, self.fail, self.configured = name, fail, True

        def complete(self, system, prompt, *, response_format=None, temperature=None):
            calls[self.name] += 1
            if self.fail:
                raise LLMError(f"{self.name} 429 rate limit exceeded")
            # minimal valid JSON for whatever structured call this is
            return Completion(text="{}", model=f"{self.name}-m", prompt_tokens=10, completion_tokens=2)

    built = {"groq": _P("groq", True), "gemini": _P("gemini", False)}
    monkeypatch.setattr(prov, "build_for", lambda p, m, **kw: built[p])
    monkeypatch.setattr(gemini_client, "_check_rate_limit", lambda: None)

    with profiling.profile_request("e2e"):
        out = gemini_client.call_gemini("give me json", request_type="profile_analysis",
                                        use_cache=False, json_schema={"name": "x"}, temperature=0.0)

    assert out == "{}"                         # valid, uncorrupted
    assert calls["groq"] == 1                  # exactly one attempt — no hidden double retry
    assert calls["gemini"] == 1
    call = profiling.recent(1)["profiles"][0]["llm"]["by_call"][-1]
    assert call["provider"] == "gemini" and call["fallback"] is True

    # every provider down -> a clean RuntimeError, not a crash
    built["gemini"].fail = True
    circ.reset()
    with pytest.raises(RuntimeError, match="No LLM model available"):
        gemini_client.call_gemini("x", request_type="profile_analysis", use_cache=False)


# ===========================================================================
# NO PARTIAL CREATION — a failing cover-letter must not leave an orphan row
# ===========================================================================

def test_e2e_application_letter_failure_is_atomic(e2e_client, mock_llm, db, monkeypatch):
    from models import GeneratedLetter

    c = e2e_client
    h = _auth(_signup(c, "atomic@example.com"))
    profile = {"name": "Neo", "email": "neo@x.com", "phone": "+1", "experience": [],
               "education": [], "projects": [], "skills": [], "languages": [], "certifications": []}

    # make cover-letter rendering blow up (imported lazily inside _prepare_letter)
    import services.generation_service as gs

    def boom(*a, **k):
        raise RuntimeError("LLM totally down")
    monkeypatch.setattr(gs, "run_letter_pipeline", boom)

    oid = _seed_selected_offer(db, _me_id(c, h), sid="atom")
    before = db.query(GeneratedLetter).count()
    r = c.post(f"/api/jobs/{oid}/apply", headers=h,
               json={"cv_profile": profile, "prepare": True, "generate_letter": True,
                     "letter_language": "en"})
    # the application is still recorded — just without a letter (graceful)
    assert r.status_code == 200
    body = r.json()
    assert body["application_id"] is not None
    assert body["letter_id"] is None
    assert "could not be generated" in body["message"]
    # NO orphan letter row was committed
    assert db.query(GeneratedLetter).count() == before


# ===========================================================================
# MISTRAL — re-ping result (Phase 3)
# ===========================================================================

def test_mistral_is_still_disabled_in_routing():
    """Re-pinged 2026-09-08 with the updated key: mistral-small-latest /
    mistral-medium-latest / mistral-medium-3.5 all return 429 "Rate limit
    exceeded". Per the closure rule it stays in LLM_DISABLED_PROVIDERS and is
    never an automatic fallback."""
    from config import DEFAULT_LLM_ROUTING, settings
    from services.llm.routing import resolve

    assert "mistral" in settings.llm_disabled_providers_list
    for rt in DEFAULT_LLM_ROUTING:
        assert all(s.provider != "mistral" for s in resolve(rt).steps), rt
