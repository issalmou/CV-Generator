"""
Phase 4 — the backend is organised by domain:

    api/                     HTTP layer (one module per domain)
    services/cv/             résumé extraction + CV/letter generation + skill analysis
    services/jobs/           job search + matching + applications (agent-driven)
    services/conversations/  conversational memory + edit agent + job agent
    services/documents/      generated-document versioning
    services/llm/            the single OpenAI-compatible provider + routing + circuit
    services/providers/      job-board adapters

This test guards that:
* every domain package imports cleanly (no circular imports);
* ``import main`` still wires every router;
* the flat ``services/*.py`` that stayed flat are genuinely cross-cutting
  (infra / orchestration), not stray domain code.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

DOMAIN_MODULES = [
    "api.auth", "api.cv", "api.jobs", "api.conversations", "api.admin",
    "api.profile", "api.extraction",
    "services.cv.resume_parser_pipeline", "services.cv.cv_generator",
    "services.cv.pdf_generator", "services.cv.ats_optimizer",
    "services.cv.profile_analyzer", "services.cv.skill_analysis",
    "services.cv.letter_generator",
    "services.jobs.search_service", "services.jobs.preference_service",
    "services.jobs.application_service", "services.jobs.match_service",
    "services.conversations.conversation_service", "services.conversations.agent_service",
    "services.conversations.job_agent_service",
    "services.documents.document_service",
    "services.llm.routing", "services.llm.circuit", "services.llm.providers",
    "services.generation_service", "services.gemini_client",
]


@pytest.mark.parametrize("mod", DOMAIN_MODULES)
def test_domain_module_imports(mod):
    importlib.import_module(mod)


def test_import_main_wires_every_router():
    import main
    paths = {getattr(r, "path", "") for r in main.app.routes}
    for expected in ("/api/auth/signin", "/api/generate-cv", "/api/jobs/{job_id}/apply",
                     "/api/conversations/{conversation_id}/messages", "/api/admin/llm",
                     "/api/profile", "/api/dashboard", "/api/dashboard/jobs", "/api/health"):
        assert expected in paths, expected
    # Phase 6 — the manual job-search API is gone; the agent is the only way in.
    for removed in ("/api/jobs/search", "/api/jobs/search/{search_id}",
                    "/api/jobs/context", "/api/jobs/sources", "/api/stats"):
        assert removed not in paths, f"{removed} should have been removed"


def test_every_domain_package_is_a_package():
    for pkg in ("api", "services.cv", "services.jobs", "services.conversations",
                "services.documents", "services.llm", "services.providers"):
        m = importlib.import_module(pkg)
        assert hasattr(m, "__path__"), pkg


def test_no_domain_leftovers_in_flat_services():
    """The modules still directly under services/ must be cross-cutting only."""
    import services
    flat = {name for _, name, ispkg in pkgutil.iter_modules(services.__path__) if not ispkg}
    allowed = {
        "gemini_client", "generation_service",            # LLM entrypoint + CV/letter orchestrator
        "cache_service", "minio_service", "profiling",     # infra
        "id_utils",                                       # business-id (UUID) validation helper
        "parser_common",                                  # shared LLM-JSON helper (cv + jobs)
        "recommendations",                                # contextual recommendations (cv + agent)
        "auth_service", "admin_service", "stats_service", "email_service",
        "usage_event_service", "runtime_config_service",
        "user_profile_service", "user_dashboard_service",
    }
    assert flat <= allowed, f"unexpected flat service module(s): {flat - allowed}"
