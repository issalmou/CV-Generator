"""
LOT 3 — centralised multi-provider routing.

ONE abstraction (OpenAICompatibleProvider), ONE routing table
(config.DEFAULT_LLM_ROUTING + LLM_ROUTING_OVERRIDES), resolved per
request_type inside call_gemini. The 11 consumer modules are untouched.
"""

from __future__ import annotations

import pytest

from services.llm import routing
from services.llm.base import Completion, LLMError


@pytest.fixture(autouse=True)
def _routing_on(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "LLM_ROUTING_ENABLED", True)
    # keys present so groq/gemini steps are "usable"
    monkeypatch.setattr(settings, "GROQ_API_KEY", "k-groq")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "k-gem")
    monkeypatch.setattr(settings, "LLM_DISABLED_PROVIDERS", "mistral")
    monkeypatch.setattr(settings, "LLM_ROUTING_OVERRIDES", "")
    routing.reset()
    yield
    routing.reset()


# ---------------------------------------------------------------------------
# resolve()
# ---------------------------------------------------------------------------

def test_resolve_uses_the_configured_chain():
    r = routing.resolve("resume_parse_experience")
    assert [str(s) for s in r.steps][:2] == ["groq/openai/gpt-oss-120b", "gemini/gemini-3.5-flash-lite"]
    assert r.temperature == 0.0


def test_resolve_drops_steps_without_a_key(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    routing.reset()
    r = routing.resolve("cover_letter")
    assert all(s.provider != "gemini" for s in r.steps)
    assert r.steps[0].provider == "groq"


def test_resolve_all_unusable_falls_back_to_global(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "GROQ_API_KEY", "")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "nvidia")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "k-nv")
    routing.reset()
    r = routing.resolve("cover_letter")
    assert len(r.steps) == 1 and r.steps[0].provider == "nvidia"


def test_disabled_provider_never_in_a_chain(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "MISTRAL_API_KEY", "k-m")
    monkeypatch.setattr(settings, "LLM_ROUTING_OVERRIDES",
                        '{"cover_letter": {"chain": [["mistral","mistral-small-latest"],'
                        '["groq","openai/gpt-oss-120b"]]}}')
    routing.reset()
    r = routing.resolve("cover_letter")
    assert [s.provider for s in r.steps] == ["groq"]


def test_override_merges_over_default(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "LLM_ROUTING_OVERRIDES",
                        '{"cover_letter": {"chain": [["gemini","gemini-flash-latest"],'
                        '["groq","openai/gpt-oss-120b"]], "temperature": 0.5}}')
    routing.reset()
    r = routing.resolve("cover_letter")
    assert r.steps[0].provider == "gemini"
    assert r.temperature == 0.5


def test_routing_disabled_uses_single_provider(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "LLM_ROUTING_ENABLED", False)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    routing.reset()
    r = routing.resolve("resume_parse_experience")
    assert len(r.steps) == 1 and r.steps[0].provider == "groq"


def test_as_public_dict_has_no_keys():
    pub = routing.as_public_dict()
    assert "cover_letter" in pub and isinstance(pub["cover_letter"], list)
    assert all("/" in entry for entry in pub["cover_letter"])
    assert "k-groq" not in str(pub)


# ---------------------------------------------------------------------------
# call_gemini walks the chain
# ---------------------------------------------------------------------------

class _FakeProv:
    def __init__(self, name, *, fail=False):
        self.name = name
        self.configured = True
        self._fail = fail
        self.calls = 0

    def complete(self, system, prompt, *, response_format=None, temperature=None):
        self.calls += 1
        if self._fail:
            raise LLMError(f"{self.name} is down")
        return Completion(text=f"ok:{self.name}", model=f"{self.name}-model",
                          prompt_tokens=5, completion_tokens=3)


def test_call_gemini_falls_over_to_the_next_provider(monkeypatch):
    from services import gemini_client, profiling
    from services.llm import providers as prov

    groq = _FakeProv("groq", fail=True)
    gem = _FakeProv("gemini", fail=False)
    built: dict = {"groq": groq, "gemini": gem}
    monkeypatch.setattr(prov, "build_for", lambda p, m, **kw: built[p])
    monkeypatch.setattr(gemini_client, "_check_rate_limit", lambda: None)

    with profiling.profile_request("cv_generation"):
        out = gemini_client.call_gemini("hi", request_type="cover_letter", use_cache=False)

    assert out == "ok:gemini"
    assert groq.calls == 1 and gem.calls == 1
    call = profiling.recent(1)["profiles"][0]["llm"]["by_call"][-1]
    assert call["provider"] == "gemini"
    assert call["fallback"] is True and call["attempts"] == 2


def test_call_gemini_all_steps_fail_raises_runtimeerror(monkeypatch):
    from services import gemini_client
    from services.llm import providers as prov

    monkeypatch.setattr(prov, "build_for", lambda p, m, **kw: _FakeProv(p, fail=True))
    monkeypatch.setattr(gemini_client, "_check_rate_limit", lambda: None)
    with pytest.raises(RuntimeError, match="No LLM model available"):
        gemini_client.call_gemini("hi", request_type="cover_letter", use_cache=False)


# ---------------------------------------------------------------------------
# build_for instance cache
# ---------------------------------------------------------------------------

def test_build_for_caches_one_client_per_provider_model(monkeypatch):
    from config import settings
    from services.llm import providers as prov

    monkeypatch.setattr(settings, "GROQ_API_KEY", "k")
    prov.reset_instances()
    a = prov.build_for("groq", "openai/gpt-oss-120b")
    b = prov.build_for("groq", "openai/gpt-oss-120b")
    c = prov.build_for("groq", "openai/gpt-oss-20b")
    assert a is b and a is not c
