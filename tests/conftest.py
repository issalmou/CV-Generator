"""
Shared pytest fixtures for the CV Assistant test suite.

The golden rule of this suite: **no test ever hits a real LLM**. Every
test that exercises a code path touching ``services.gemini_client`` gets
``call_gemini`` patched — in every module that imported the name — via the
``mock_llm`` fixture.

Infrastructure (added with auth / MinIO / cache):
- the database runs on a throwaway SQLite file (env var set below, before
  any project import), recreated per test via the ``db`` fixture;
- MinIO is replaced by an in-memory dict (``fake_minio``, autouse);
- the application cache is cleared before every test (``clear_cache``, autouse);
- ``auth_client`` / ``anon_client`` are the two TestClients — authenticated
  (a ``test_user`` is injected) and anonymous.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Make the project root importable when pytest is run from anywhere.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Environment — MUST be set before `config` / `database` are imported.
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL", f"sqlite:///{ROOT / 'test_cv.db'}")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("PASSWORD_RESET_DEV_MODE", "true")
os.environ.setdefault("PASSWORD_RESET_MAX_ATTEMPTS", "5")
os.environ.setdefault("REACTIVATION_DEV_MODE", "true")
os.environ.setdefault("REACTIVATION_MAX_ATTEMPTS", "5")
os.environ.setdefault("NVIDIA_API_KEY", "test-nvidia-key")
# LOT 3 — the existing suite exercises the single-provider (legacy) path;
# call_gemini routing is covered explicitly by tests/test_llm_routing.py,
# which flips this on. mock_llm patches call_gemini itself, so most tests
# never reach either path.
os.environ.setdefault("LLM_ROUTING_ENABLED", "false")
# No e-mail backend -> EmailService dev-mode. No Redis / MinIO -> memory + fake.
for _unset in ("RESEND_API_KEY", "SMTP_HOST", "REDIS_URL", "MINIO_ENDPOINT"):
    os.environ.pop(_unset, None)


# ---------------------------------------------------------------------------
# LLM mocking
# ---------------------------------------------------------------------------

_LLM_CONSUMER_MODULES = (
    "services.gemini_client",
    "services.cv.experience_parser",
    "services.cv.education_parser",
    "services.cv.project_parser",
    "services.cv.skills_parser",
    "services.cv.resume_structurer",
    "services.cv.profile_analyzer",
    "services.cv.ats_optimizer",
    "services.cv.cv_generator",
    "services.jobs.company_parser",
    "services.cv.letter_generator",
    "services.jobs.preference_service",
    "services.conversations.agent_service",
    "services.conversations.job_agent_service",
)


def _default_llm_router(prompt: str, *, request_type: str = "generic", use_cache: bool = True,
                        json_schema=None, temperature=None) -> str:
    if request_type == "resume_parse_experience":
        return json.dumps({"experience": [{
            "company": "Acme Corp", "position": "Software Engineer",
            "period": "Jan 2021 - Present",
            "location": "Remote", "description": None,
            "achievements": ["Shipped the billing service"],
            "technologies": ["Python", "PostgreSQL"],
        }]})
    if request_type == "resume_parse_education":
        return json.dumps({"education": [{
            "institution": "INSEA", "degree": "Master", "field": "Data Science",
            "start_date": "2022", "end_date": "2024", "gpa": None, "location": "Rabat",
        }]})
    if request_type == "resume_parse_projects":
        return json.dumps({"projects": [{
            "title": "Portfolio site", "description": "Personal website",
            "technologies": ["Next.js"], "github": None, "demo": None,
        }]})
    if request_type == "resume_parse_skills":
        return json.dumps({"skills": ["Python", "SQL", "PostgreSQL", "MongoDB"]})
    if request_type == "resume_parse_fallback":
        return json.dumps({
            "education": [], "experience": [], "projects": [],
            "certifications": [], "skills": [],
        })
    if request_type == "profile_analysis":
        return json.dumps({
            "professional_summary": "Backend engineer with a data focus.",
            "career_objective": "Build reliable data platforms.",
            "key_skills": ["Python", "PostgreSQL", "Airflow"],
            "strengths": ["Data modelling", "API design"],
            "expertise_areas": ["Backend", "Data engineering"],
            "years_of_experience": 3, "seniority_level": "Mid",
        })
    if request_type == "ats_keyword_extraction":
        return json.dumps({
            "technical_skills": ["Python", "Kubernetes"], "frameworks": ["FastAPI"],
            "tools": ["Git"], "business_skills": [], "certifications": [],
            "degrees": [], "soft_skills": ["Communication"],
        })
    if request_type == "ats_content_optimization":
        return json.dumps({
            "optimized_summary": "ATS-optimised summary.",
            "career_objective": "Objective.",
            "optimized_achievements": [], "additional_skills": [],
        })
    if request_type == "skill_categorisation":
        return json.dumps({"Programming Languages": ["Python"], "Databases": ["PostgreSQL"]})
    if request_type == "letter_job_company":
        return json.dumps({
            "company_name": "Globex", "position": "Backend Engineer",
            "location": "Rabat, Morocco", "recipient": "Hiring Team",
            "company_address": "12 Innovation Street\nRabat",
        })
    if request_type == "job_preference_extraction":
        return json.dumps({
            "query": "data scientist",
            "job_type": "internship",
            "location": "Paris",
            "remote_type": "hybrid",
            "skills": ["Python", "SQL"],
            "experience_level": "student",
            "language": "fr",
            "clarifying_question": "",
        })
    if request_type == "conversation_agent":
        # Phase 6 — the agent turn now expects a ConversationDirective. Default:
        # a plain chat reply (individual tests override with search/apply/etc.).
        return json.dumps({
            "reply": "Sure, here is some help with that.",
            "intent": "chat",
            "search_patch": None, "apply": None, "question": None,
        })
    if request_type == "agent_edit":
        # default: a no-op action (individual tests override with a real edit)
        return json.dumps({
            "op": "none", "value": None, "category": None, "skill": None,
            "from_name": None, "to_name": None, "role_index": None,
            "bullet_index": None, "order": [], "recipient": None,
            "reply": "I didn't change anything — tell me what to edit.",
        })
    if request_type == "cover_letter":
        return (
            "Dear Hiring Team,\n\n"
            "I am writing to apply for the Backend Engineer role at Globex. "
            "Over the past three years I have built and shipped data platforms "
            "in Python.\n\n"
            "At Acme Corp I owned the billing service end to end, cutting "
            "processing time by 40%.\n\n"
            "Globex's focus on reliable infrastructure is exactly where I want "
            "to contribute next.\n\n"
            "Thank you for your consideration.\n\nSincerely,\nJohn Doe"
        )
    return "{}"


@pytest.fixture
def mock_llm(monkeypatch):
    class _Controller:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.prompts: list[tuple[str, str]] = []
            self._router = _default_llm_router

        def set(self, fn) -> None:
            self._router = fn

        def __call__(self, prompt: str, *, request_type: str = "generic", use_cache: bool = True,
                     json_schema=None, temperature=None) -> str:
            self.calls.append(request_type)
            self.prompts.append((request_type, prompt))
            try:
                return self._router(prompt, request_type=request_type, use_cache=use_cache,
                                    json_schema=json_schema, temperature=temperature)
            except TypeError:
                # a test installed a narrower router (prompt, *, request_type, use_cache)
                return self._router(prompt, request_type=request_type, use_cache=use_cache)

    controller = _Controller()
    for mod_name in _LLM_CONSUMER_MODULES:
        __import__(mod_name)
        monkeypatch.setattr(sys.modules[mod_name], "call_gemini", controller, raising=True)
    return controller


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    """A fresh schema + session per test."""
    from database import Base, SessionLocal, engine
    import models  # noqa: F401 - register tables

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


# ---------------------------------------------------------------------------
# Fake MinIO + cache reset (autouse)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fake_minio(monkeypatch):
    """Replace the MinIO singleton's I/O with an in-memory store."""
    from services.minio_service import minio_service

    store: dict[str, bytes] = {}

    monkeypatch.setattr(minio_service, "is_configured", lambda: True)
    monkeypatch.setattr(minio_service, "ensure_bucket", lambda: None)
    monkeypatch.setattr(
        minio_service, "upload",
        lambda key, data, content_type="application/pdf": store.__setitem__(key, bytes(data)) or key,
    )
    monkeypatch.setattr(minio_service, "download", lambda key: store[key])
    monkeypatch.setattr(minio_service, "delete", lambda key: store.pop(key, None) and None)
    monkeypatch.setattr(
        minio_service, "presigned_get_url",
        lambda key, expires=None: f"http://minio.test/{minio_service.bucket}/{key}",
    )
    minio_service._fake_store = store  # test-visible handle
    return store


@pytest.fixture(autouse=True)
def clear_cache():
    from services.cache_service import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _reset_profiling():
    """Keep the profiling ring buffer isolated between tests (v2.9 Phase 1)."""
    from services import profiling

    profiling.reset()
    yield
    profiling.reset()


@pytest.fixture(autouse=True)
def _reset_llm_routing():
    """LOT 3 — the resolved-route cache and per-(provider,model) clients are
    process-global; a test that changes LLM settings must not leak into the next."""
    from services.llm import circuit as _c
    from services.llm import providers as _p
    from services.llm import routing as _r

    _r.reset()
    _p.reset_instances()
    _c.reset()
    yield
    _r.reset()
    _p.reset_instances()
    _c.reset()


# ---------------------------------------------------------------------------
# Users + TestClients
# ---------------------------------------------------------------------------

@pytest.fixture
def test_user(db):
    import bcrypt

    from models import User

    user = User(
        email="tester@example.com",
        password_hash=bcrypt.hashpw(b"Testpass123", bcrypt.gensalt()).decode(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _client(db_session, current_user=None):
    from fastapi.testclient import TestClient

    import main
    from database import get_db
    from dependencies.auth import get_current_user

    main.app.dependency_overrides[get_db] = lambda: db_session
    if current_user is not None:
        main.app.dependency_overrides[get_current_user] = lambda: current_user

    client = TestClient(main.app)
    client._cleanup_app = main.app
    return client


@pytest.fixture
def anon_client(db):
    client = _client(db)
    yield client
    client._cleanup_app.dependency_overrides.clear()


@pytest.fixture
def auth_client(db, test_user):
    client = _client(db, current_user=test_user)
    yield client
    client._cleanup_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

@pytest.fixture
def resume_text_en() -> str:
    return (
        "JOHN DOE\n"
        "john.doe@example.com | +212 600 112233 | linkedin.com/in/johndoe | github.com/johndoe\n"
        "Nationality: Moroccan\n"
        "Address: 12 Rue de la Paix, Rabat\n\n"
        "PROFESSIONAL SUMMARY\n"
        "Backend engineer focused on data platforms.\n\n"
        "WORK EXPERIENCE\n"
        "Software Engineer, Acme Corp - Remote\n"
        "Jan 2021 - Present\n"
        "- Shipped the billing service\n\n"
        "EDUCATION\n"
        "Master in Data Science, INSEA, 2024\n\n"
        "PROJECTS\n"
        "Portfolio site - personal website built with Next.js\n\n"
        "SKILLS\n"
        "Languages: Python, SQL\n"
        "Databases: PostgreSQL, MongoDB\n\n"
        "LANGUAGES\n"
        "English (Fluent), French (Native)\n\n"
        "CERTIFICATIONS\n"
        "AWS Certified Solutions Architect, Amazon, 2023\n"
    )


@pytest.fixture
def resume_text_fr() -> str:
    return (
        "JEANNE MARTIN\n"
        "jeanne.martin@example.com | +33 6 12 34 56 78\n\n"
        "PROFIL\n"
        "Developpeuse full-stack.\n\n"
        "EXPERIENCE PROFESSIONNELLE\n"
        "Developpeuse, Beta SARL - Paris\n"
        "2020 - 2023\n"
        "- Refonte du site e-commerce\n\n"
        "FORMATION\n"
        "Master Informatique, Sorbonne, 2019\n\n"
        "COMPETENCES\n"
        "Langages: JavaScript, Python\n\n"
        "LANGUES\n"
        "Francais (Natif), Anglais (Courant)\n"
    )


@pytest.fixture
def cv_profile_dict() -> dict:
    return {
        "language": "en",
        "job_description": "We need a Python + Kubernetes backend engineer.",
        "cv_profile": {
            "name": "John Doe",
            "email": "john.doe@example.com",
            "phone": "+212 600 112233",
            "linkedin": "linkedin.com/in/johndoe",
            "professional_summary": "Backend engineer focused on data platforms.",
            "education": [{
                "institution": "INSEA", "degree": "Master",
                "field": "Data Science", "start_date": "2022", "end_date": "2024",
            }],
            "experience": [{
                "company": "Acme Corp", "position": "Software Engineer",
                "period": "Jan 2021 - Present",
                "achievements": ["Shipped the billing service"],
                "technologies": ["Python", "PostgreSQL"],
            }],
            "projects": [],
            "skills": [{"category": "Languages", "skills": ["Python", "SQL"]}],
            "languages": [{"language": "English", "level": "Fluent"}],
            "certifications": [],
        },
    }


@pytest.fixture
def client(mock_llm, db, test_user):
    """Backward-compatible alias: an authenticated client with the LLM mocked."""
    c = _client(db, current_user=test_user)
    yield c
    c._cleanup_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Job search — fixtures + offline HTTP
# ---------------------------------------------------------------------------

JOB_FIXTURES = ROOT / "tests" / "fixtures" / "jobs"


@pytest.fixture
def job_fixtures():
    return JOB_FIXTURES


@pytest.fixture(autouse=True)
def _no_real_job_http(request, monkeypatch):
    """Safety net: any job provider that reaches the real network in a test
    (without opting into ``fake_http``) fails loudly instead of hitting a site.
    Opt out with ``@pytest.mark.real_http`` (tests that exercise http.py itself)."""
    if request.node.get_closest_marker("real_http"):
        return

    def _blocked(*a, **k):
        raise AssertionError(
            "services.providers.http.fetch called without the `fake_http` fixture"
        )

    import services.providers.http as _http
    monkeypatch.setattr(_http, "fetch", _blocked)
    monkeypatch.setattr(_http, "fetch_json", _blocked)


class _FakeHttp:
    """Routes URL substrings to canned responses. ``AccessDenied`` / ``HttpError``
    instances are raised; ``HttpResult`` / str / (status, body) are returned."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, object]] = []
        self.calls: list[str] = []

    def route(self, needle: str, response) -> "_FakeHttp":
        self.routes.append((needle, response))
        return self

    def _resolve(self, url: str, params):
        from services.providers.http import HttpResult

        full = url
        if params:
            full += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        self.calls.append(full)
        for needle, response in self.routes:
            if needle in full:
                if isinstance(response, Exception):
                    raise response
                if callable(response) and not isinstance(response, HttpResult):
                    response = response(full)
                if isinstance(response, HttpResult):
                    return response
                if isinstance(response, tuple):
                    code, body = response
                    return HttpResult(code, body, url, code < 400, {})
                return HttpResult(200, str(response), url, True, {})
        # default: empty 200 (parsers turn this into "no offers")
        return HttpResult(200, "", url, True, {})

    def fetch(self, url, *, method="GET", params=None, headers=None,
              json_body=None, allowed_hosts=(), accept_language=None,
              timeout=None, max_bytes=None):
        return self._resolve(url, params)

    def fetch_json(self, url, **kwargs):
        import json as _json
        from services.providers.http import HttpError

        result = self._resolve(url, kwargs.get("params"))
        try:
            return _json.loads(result.text) if result.text else {}
        except (ValueError, _json.JSONDecodeError) as exc:
            raise HttpError("malformed JSON") from exc


@pytest.fixture
def fake_http(monkeypatch):
    """Opt-in offline HTTP for job providers. Configure with ``.route(...)``."""
    fake = _FakeHttp()
    import services.providers.http as _http
    monkeypatch.setattr(_http, "fetch", fake.fetch)
    monkeypatch.setattr(_http, "fetch_json", fake.fetch_json)
    return fake


@pytest.fixture
def reset_registry():
    """Rebuild the provider registry after a test that changed provider settings."""
    from services.providers.registry import registry
    yield registry
    from config import settings
    settings.JOBS_ENABLED_PROVIDERS = (
        "linkedin,indeed,arbeitnow,weworkremotely,hackernews,"
        "remotive,jobicy,remoteok,himalayas,adzuna,greenhouse,lever,career_pages,ashby,"
        "workday,oracle_hcm,smartrecruiters,rippling,phenom,talentbrew"
    )
    settings.ADZUNA_APP_ID = ""
    settings.ADZUNA_APP_KEY = ""
    registry.reload()


# ---------------------------------------------------------------------------
# Browser session — never launch a real Chromium in tests
# ---------------------------------------------------------------------------

class _FakeBrowser:
    """Routes URL substrings to canned HTML (or raises). Mirrors ``_FakeHttp``."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, object]] = []
        self.calls: list[str] = []

    def route(self, needle: str, response) -> "_FakeBrowser":
        self.routes.append((needle, response))
        return self

    def is_available(self) -> bool:
        return True

    def get_page(self, url, *, allowed_hosts=(), wait_selector=None, timeout=None):
        self.calls.append(url)
        for needle, response in self.routes:
            if needle in url:
                if isinstance(response, Exception):
                    raise response
                if callable(response):
                    return response(url)
                return str(response)
        return "<html><body>no route</body></html>"

    def shutdown(self) -> None:
        pass


@pytest.fixture
def mock_browser(monkeypatch):
    """Opt-in offline browser for the ``career_pages`` provider."""
    fake = _FakeBrowser()
    import services.providers.browser as _b
    import services.providers.browser_base as _bb
    monkeypatch.setattr(_b, "browser_session", fake)
    monkeypatch.setattr(_bb, "browser_session", fake)
    return fake


@pytest.fixture(autouse=True)
def _no_real_browser(request, monkeypatch):
    """Safety net: a test must not start a real Chromium. Opt into a browser
    with the ``mock_browser`` fixture (which replaces the singleton)."""
    if "mock_browser" in request.fixturenames:
        return

    def _blocked(*a, **k):
        raise AssertionError(
            "BrowserSessionManager.get_page called without the `mock_browser` fixture"
        )

    import services.providers.browser as _b
    monkeypatch.setattr(_b.browser_session, "get_page", _blocked)
    monkeypatch.setattr(_b.browser_session, "_new_driver",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("real WebDriver blocked in tests")))


@pytest.fixture
def swap_providers(monkeypatch):
    """Replace the live provider set with fakes for one test."""
    def _apply(providers):
        from services.providers.registry import registry
        monkeypatch.setattr(registry, "_providers", {p.name: p for p in providers})
        return registry
    return _apply


@pytest.fixture(autouse=True)
def _fast_job_timeouts():
    """Keep provider timeout guards sub-second across the job test suite."""
    from config import settings
    orig = (settings.JOB_PROVIDER_TIMEOUT, settings.JOB_PROVIDER_MAX_PAGES,
            settings.JOB_SEARCH_DEADLINE)
    settings.JOB_PROVIDER_TIMEOUT = 0.2
    settings.JOB_PROVIDER_MAX_PAGES = 1
    settings.JOB_SEARCH_DEADLINE = 1.0
    yield
    (settings.JOB_PROVIDER_TIMEOUT, settings.JOB_PROVIDER_MAX_PAGES,
     settings.JOB_SEARCH_DEADLINE) = orig


@pytest.fixture(autouse=True)
def _no_bundled_ats_boards(monkeypatch):
    """Tests configure ATS boards explicitly — the shipped curated
    `data/ats_boards/*.txt` lists must not leak in. `test_ats_boards_config.py`
    opts back in to exercise the bundled-list loader."""
    from config import settings
    monkeypatch.setattr(settings, "ATS_USE_BUNDLED_BOARDS", False)
