"""Phases 49 + 66 — the UNIFIED LLM layer.

There is exactly **one** provider implementation
(``OpenAICompatibleProvider``); every backend (nvidia / openai / gemini /
mistral / groq / openai_compatible / custom) is that class with a different
config. No per-provider subclass. Business code is unaffected — it still calls
``call_gemini``.
"""

from __future__ import annotations

import pytest

from config import _LLM_BLOCKS, LLM_PROVIDER_NAMES, Settings
from services.llm import (
    OpenAICompatibleProvider, build_active_provider, build_provider, provider_config,
)
from services.llm.base import BaseLLMProvider, LLMError, _sanitize


def _s(**env):
    return Settings(_env_file=None, DATABASE_URL="sqlite://", JWT_SECRET_KEY="x", **env)


_LLM_ENV = tuple(
    a for block in _LLM_BLOCKS.values() for a in block[:3]
) + ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL", "LLM_MODELS_FALLBACK")


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for name in _LLM_ENV:
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# anti-redundancy: ONE class
# ---------------------------------------------------------------------------

def test_there_is_exactly_one_provider_implementation():
    import services.llm.providers as p
    impls = [v for v in vars(p).values()
             if isinstance(v, type) and issubclass(v, BaseLLMProvider)
             and v is not BaseLLMProvider]
    assert impls == [OpenAICompatibleProvider], impls


def test_every_backend_is_the_same_class():
    for name in LLM_PROVIDER_NAMES:
        prov = build_provider(name)
        assert type(prov) is OpenAICompatibleProvider
        assert prov.name == name


# ---------------------------------------------------------------------------
# config resolution
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name, host", [
    ("nvidia", "integrate.api.nvidia.com"),
    ("openai", "api.openai.com"),
    ("gemini", "generativelanguage.googleapis.com"),
    ("mistral", "api.mistral.ai"),
    ("groq", "api.groq.com"),
])
def test_default_base_urls(name, host):
    assert host in build_provider(name).base_url


def test_provider_block_wins_over_generic():
    s = _s(LLM_PROVIDER="mistral", MISTRAL_API_KEY="mk", MISTRAL_MODEL="mistral-x",
           LLM_API_KEY="generic", LLM_MODEL="generic-model")
    assert s.llm_provider == "mistral"
    assert s.llm_api_key == "mk"
    assert s.llm_models[0] == "mistral-x"


def test_generic_fallback_used_when_no_block():
    s = _s(LLM_PROVIDER="groq", LLM_API_KEY="g", LLM_BASE_URL="https://proxy/v1")
    assert s.llm_api_key == "g"
    assert s.llm_base_url == "https://proxy/v1"     # generic wins when set...
    s2 = _s(LLM_PROVIDER="groq", GROQ_API_KEY="g")
    assert "api.groq.com" in s2.llm_base_url        # ...else the provider default


def test_unknown_provider_falls_back_to_nvidia():
    s = _s(LLM_PROVIDER="skynet")
    assert s.llm_provider == "nvidia"
    assert provider_config("skynet")["name"] == "nvidia"


def test_public_config_never_contains_a_key():
    s = _s(LLM_PROVIDER="groq", GROQ_API_KEY="gsk_super_secret_value_1234567890")
    cfg = s.llm_public_config()
    assert "gsk_super_secret" not in repr(cfg)
    assert "api_key" not in cfg
    assert set(cfg["available_providers"]) == set(LLM_PROVIDER_NAMES)


# ---------------------------------------------------------------------------
# complete() behaviour
# ---------------------------------------------------------------------------

class _Chain(OpenAICompatibleProvider):
    def __init__(self, models, ok_model):
        super().__init__(name="fake", base_url="https://fake/v1", api_key="k",
                         models=models, temperature=0.3, max_tokens=10, timeout=5.0)
        self._ok = ok_model

    def _complete_one(self, model, system, prompt, *, response_format=None, temperature=None):
        if model == self._ok:
            return f"ok from {model}", {"prompt_tokens": 11, "completion_tokens": 7}
        raise RuntimeError(f"{model} down")


def test_complete_refuses_without_a_key():
    p = build_provider("openai")   # no key in the isolated env
    assert not p.configured
    with pytest.raises(LLMError, match="no API key"):
        p.complete("sys", "hi")


def test_complete_walks_the_fallback_chain():
    res = _Chain(["m1", "m2", "m3"], "m3").complete("s", "p")
    assert res.text == "ok from m3" and res.model == "m3"
    assert res.attempts == 3 and res.fallback_used is True
    assert res.prompt_tokens == 11 and res.completion_tokens == 7


def test_complete_raises_llmerror_when_all_fail():
    with pytest.raises(LLMError, match="no fake model available"):
        _Chain(["m1", "m2"], None).complete("s", "p")


# ---------------------------------------------------------------------------
# sanitisation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("secret", [
    "sk-abcdefghijklmnop", "nvapi-abcdefghijklmnop", "AIzaAbCdEfGhIjKlMnOp",
    "gsk_abcdefghijklmnop1234", "0123456789abcdef0123456789abcdef",
])
def test_sanitize_strips_secrets_and_urls(secret):
    out = _sanitize(f"auth failed for {secret} at https://api.example.com/v1/x")
    assert secret not in out and "https://" not in out


def test_sanitize_redacts_labelled_values():
    assert "topsecret" not in _sanitize("Authorization: Bearer topsecret-token-here")
