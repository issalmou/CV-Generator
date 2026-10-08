"""Phase 39 — LLM failure modes: no key, bad model, timeout, API error.

`call_gemini` must fail cleanly (a plain `RuntimeError`), and the pipeline
guards must map that to a `429` rather than a `500`.
"""

from __future__ import annotations

import pytest

from config import settings
from services import gemini_client
from services.generation_service import llm_error_response


@pytest.fixture(autouse=True)
def _reset_client():
    gemini_client.reset_client()
    yield
    gemini_client.reset_client()


def test_no_key_raises_clean_runtimeerror(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "")
    with pytest.raises(RuntimeError, match="No API key"):
        gemini_client.call_gemini("hi", use_cache=False)


class _FakeProvider:
    name = "fake"
    _models = ["fake/one", "fake/two"]
    configured = True

    def __init__(self, exc=None):
        self._exc = exc

    @property
    def models(self):
        return list(self._models)

    def complete(self, system, prompt, **kwargs):
        from services.llm.base import LLMError
        raise LLMError(f"no fake model available (last error: {self._exc})")


def test_all_models_failing_raises_runtimeerror(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "present")
    monkeypatch.setattr(gemini_client, "_check_rate_limit", lambda: None)
    monkeypatch.setattr(gemini_client, "_active_provider",
                        lambda: _FakeProvider(TimeoutError("upstream timed out")))
    with pytest.raises(RuntimeError, match="No LLM model available"):
        gemini_client.call_gemini("prompt", use_cache=False)


def test_rate_limit_is_a_runtimeerror(monkeypatch):
    monkeypatch.setattr(settings, "LLM_MAX_CALLS_PER_MINUTE", 1)
    monkeypatch.setattr(settings, "LLM_API_KEY", "present")
    monkeypatch.setattr(gemini_client, "_CALL_TIMESTAMPS", [])

    class _NoNet(_FakeProvider):
        def complete(self, *_, **__):
            raise AssertionError("should not reach the provider")

    monkeypatch.setattr(gemini_client, "_active_provider", lambda: _NoNet())
    gemini_client._check_rate_limit()  # uses the 1 allowed slot
    with pytest.raises(RuntimeError, match="Rate limit"):
        gemini_client.call_gemini("x", use_cache=False)


def test_pipeline_guard_maps_llm_failure_to_429():
    resp = llm_error_response(RuntimeError("No NVIDIA model available"), trace_id="t")
    assert resp is not None and resp.status_code == 429


def test_pipeline_guard_passes_through_unknown_errors():
    assert llm_error_response(ValueError("weird"), trace_id="t") is None


def test_get_config_reports_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "")
    assert gemini_client.get_config()["configured"] is False
    assert gemini_client.is_configured() is False
