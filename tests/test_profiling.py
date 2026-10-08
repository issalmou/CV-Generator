"""v2.9 Phase 1 — latency + LLM-call profiling (`services/profiling.py`).

Verifies the instrumentation records what it should (durations, token
counts, model names, flags), propagates into worker threads, no-ops cleanly
when disabled, and never carries prompt / response / document content.
"""

from __future__ import annotations

import concurrent.futures

import bcrypt
import pytest
from fastapi.testclient import TestClient

import main
from config import settings
from database import get_db
from dependencies.auth import get_current_user
from models import User
from services import profiling


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(settings, "PROFILING_ENABLED", True)
    profiling.reset()
    yield
    profiling.reset()


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------

def test_span_and_llm_call_accumulate_on_the_current_profile():
    with profiling.profile_request("cv_generation", request_id="abc123") as rp:
        with profiling.span("profile_analysis"):
            pass
        profiling.record_llm_call(
            request_type="profile_analysis", provider="groq", model="llama-3.3-70b",
            duration_ms=1234.5, prompt_tokens=900, completion_tokens=1600,
            max_tokens=2000, attempts=1, fallback_used=False,
        )
        assert rp is not None

    prof = profiling.recent(10)["profiles"][0]
    assert prof["kind"] == "cv_generation"
    assert prof["request_id"] == "abc123"
    assert [s["name"] for s in prof["spans"]] == ["profile_analysis"]
    assert prof["llm"]["count"] == 1
    assert prof["llm"]["prompt_tokens"] == 900
    assert prof["llm"]["completion_tokens"] == 1600
    call = prof["llm"]["by_call"][0]
    assert call["provider"] == "groq" and call["model"] == "llama-3.3-70b"
    assert call["in_tokens"] == 900 and call["out_tokens"] == 1600


def test_disabled_profiling_is_a_noop(monkeypatch):
    monkeypatch.setattr(settings, "PROFILING_ENABLED", False)
    with profiling.profile_request("cv_generation") as rp:
        assert rp is None
        with profiling.span("x"):
            pass
        profiling.record_llm_call(
            request_type="x", provider="p", model="m", duration_ms=1.0,
        )
    assert profiling.recent(10)["profiles"] == []


def test_record_outside_a_request_is_silently_dropped():
    # no active profile_request -> nothing recorded, no error
    profiling.record_llm_call(request_type="x", provider="p", model="m", duration_ms=1.0)
    assert profiling.recent(10)["count"] == 0


def test_fallback_and_error_flags_are_captured():
    with profiling.profile_request("letter"):
        profiling.record_llm_call(
            request_type="cover_letter", provider="nvidia", model="model-b",
            duration_ms=50000.0, attempts=2, fallback_used=True,
        )
        profiling.record_llm_call(
            request_type="cover_letter", provider="nvidia", model=None,
            duration_ms=45000.0, ok=False, error_kind="LLMError",
        )
    prof = profiling.recent(1)["profiles"][0]
    assert prof["llm"]["fallbacks"] == 1
    assert prof["llm"]["errors"] == 1


# ---------------------------------------------------------------------------
# worker-thread propagation (the extraction pool + the CV executor hop)
# ---------------------------------------------------------------------------

def test_bind_propagates_the_profile_into_a_worker_thread():
    def _work():
        with profiling.span("worker_step"):
            pass
        profiling.record_llm_call(
            request_type="resume_parse_experience", provider="mistral",
            model="mistral-small", duration_ms=800.0, prompt_tokens=120, completion_tokens=90,
        )
        return "done"

    with profiling.profile_request("extraction"):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(profiling.bind(_work)) for _ in range(4)]
            results = [f.result() for f in futures]

    assert results == ["done"] * 4
    prof = profiling.recent(1)["profiles"][0]
    # all 4 workers recorded into the SAME profile
    assert prof["llm"]["count"] == 4
    assert len([s for s in prof["spans"] if s["name"] == "worker_step"]) == 4


def test_bind_without_an_active_profile_returns_the_function_unchanged():
    def _f(x):
        return x * 2
    assert profiling.bind(_f) is _f
    assert profiling.bind(_f)(21) == 42


# ---------------------------------------------------------------------------
# call_gemini records real token usage from the Completion
# ---------------------------------------------------------------------------

def test_call_gemini_records_tokens_and_model_from_completion(monkeypatch):
    from services import gemini_client
    from services.llm.base import Completion

    class _FakeProvider:
        name = "groq"
        configured = True
        _models = ["llama-3.3-70b-versatile"]

        def complete(self, system, prompt, **kwargs):
            return Completion(
                text="the answer", model="llama-3.3-70b-versatile",
                prompt_tokens=812, completion_tokens=1450, attempts=1, fallback_used=False,
            )

    monkeypatch.setattr(gemini_client, "_active_provider", lambda: _FakeProvider())
    monkeypatch.setattr(gemini_client, "_check_rate_limit", lambda: None)

    with profiling.profile_request("cv_generation"):
        out = gemini_client.call_gemini("hello", request_type="profile_analysis", use_cache=False)

    assert out == "the answer"
    call = profiling.recent(1)["profiles"][0]["llm"]["by_call"][0]
    assert call["request_type"] == "profile_analysis"
    assert call["provider"] == "groq"
    assert call["model"] == "llama-3.3-70b-versatile"
    assert call["in_tokens"] == 812 and call["out_tokens"] == 1450
    assert call["cache_hit"] is False and call["ok"] is True


def test_call_gemini_prompt_cache_hit_is_recorded_without_tokens(monkeypatch):
    from services import gemini_client
    from services.llm.base import Completion

    class _FakeProvider:
        name = "groq"
        configured = True
        _models = ["m"]
        def complete(self, system, prompt, **kwargs):
            return Completion(text="cached-me", model="m", prompt_tokens=10, completion_tokens=5)

    monkeypatch.setattr(gemini_client, "_active_provider", lambda: _FakeProvider())
    monkeypatch.setattr(gemini_client, "_check_rate_limit", lambda: None)

    with profiling.profile_request("chat"):
        gemini_client.call_gemini("same prompt", request_type="conversation_agent", use_cache=True)
        gemini_client.call_gemini("same prompt", request_type="conversation_agent", use_cache=True)

    calls = profiling.recent(1)["profiles"][0]["llm"]["by_call"]
    assert len(calls) == 2
    assert calls[0]["cache_hit"] is False
    assert calls[1]["cache_hit"] is True and calls[1]["provider"] == "<prompt-cache>"


# ---------------------------------------------------------------------------
# never logs content
# ---------------------------------------------------------------------------

def test_profiling_summary_contains_no_prompt_or_response_text():
    with profiling.profile_request("chat"):
        profiling.record_llm_call(
            request_type="conversation_agent", provider="groq", model="m",
            duration_ms=1.0, prompt_tokens=5, completion_tokens=5,
        )
    dump = repr(profiling.recent(5))
    # the profile carries only counters / names / flags — no free text fields
    assert "prompt" not in dump.lower().replace("prompt_tokens", "").replace("prompt-cache", "")
    for banned in ("content", "message", "job_description", "cv_profile"):
        assert banned not in dump


# ---------------------------------------------------------------------------
# aggregates
# ---------------------------------------------------------------------------

def test_recent_aggregates_p50_p95_and_rates():
    for i in range(5):
        with profiling.profile_request("cv_generation"):
            profiling.record_llm_call(
                request_type="ats_content_optimization", provider="nvidia",
                model="nemotron", duration_ms=1000.0 * (i + 1),
                fallback_used=(i % 2 == 0),
            )
    agg = profiling.recent(10)["aggregates"]
    assert agg["by_kind"]["cv_generation"]["n"] == 5
    rt = agg["by_request_type"]["ats_content_optimization"]
    assert rt["n"] == 5
    assert rt["p50_ms"] > 0 and rt["p95_ms"] >= rt["p50_ms"]
    assert 0.0 < rt["fallback_rate"] <= 1.0


# ---------------------------------------------------------------------------
# admin endpoint
# ---------------------------------------------------------------------------

def _client(db, user):
    main.app.dependency_overrides[get_db] = lambda: db
    main.app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(main.app)


def test_profiling_endpoint_is_superadmin_only(db):
    normal = User(email="n@x.com", password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt()).decode())
    db.add(normal); db.commit(); db.refresh(normal)
    try:
        assert _client(db, normal).get("/api/admin/profiling/recent").status_code == 403
    finally:
        main.app.dependency_overrides.clear()


def test_profiling_endpoint_returns_recent_profiles(db):
    admin = User(email="a@x.com", is_superadmin=True,
                 password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt()).decode())
    db.add(admin); db.commit(); db.refresh(admin)

    with profiling.profile_request("cv_generation", request_id="deadbeef"):
        with profiling.span("pdf_rendering"):
            pass

    try:
        body = _client(db, admin).get("/api/admin/profiling/recent").json()
        assert body["enabled"] is True
        assert any(p["request_id"] == "deadbeef" for p in body["profiles"])
        assert "aggregates" in body
    finally:
        main.app.dependency_overrides.clear()
