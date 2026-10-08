"""Phases 29/30 + 49/50 — configurable multi-provider LLM backend, no key leak.

Config comes from ``config.Settings`` (``LLM_PROVIDER`` + generic ``LLM_*`` +
per-provider blocks). Business code still calls ``call_gemini``.
"""

from __future__ import annotations

import pytest

from config import DEFAULT_LLM_MODELS, Settings, settings
from services import gemini_client

_LLM_ENV = ("NVIDIA_API_KEY", "NVIDIA_MODEL", "NVIDIA_BASE_URL",
            "LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL",
            "LLM_MODELS_FALLBACK", "LLM_BASE_URL",
            "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL",
            "GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_BASE_URL",
            "OPENAI_COMPATIBLE_API_KEY", "OPENAI_COMPATIBLE_MODEL",
            "OPENAI_COMPATIBLE_BASE_URL",
            "CUSTOM_LLM_API_KEY", "CUSTOM_LLM_MODEL", "CUSTOM_LLM_BASE_URL")


@pytest.fixture
def fresh(monkeypatch):
    """A Settings built from an explicit env, isolated from the process env
    (conftest sets NVIDIA_API_KEY etc. globally)."""
    for name in _LLM_ENV:
        monkeypatch.delenv(name, raising=False)

    def _make(**env):
        base = dict(DATABASE_URL="sqlite://", JWT_SECRET_KEY="x")
        base.update(env)
        return Settings(_env_file=None, **base)
    return _make


# ---------------------------------------------------------------------------
# model chain
# ---------------------------------------------------------------------------

def test_default_chain_when_nothing_configured(fresh):
    s = fresh(NVIDIA_API_KEY="k")
    assert s.llm_models == list(DEFAULT_LLM_MODELS)


def test_llm_model_takes_precedence(fresh):
    s = fresh(LLM_MODEL="acme/one", LLM_MODELS_FALLBACK="acme/two, acme/three")
    assert s.llm_models == ["acme/one", "acme/two", "acme/three"]


def test_nvidia_model_alias(fresh):
    s = fresh(NVIDIA_MODEL="nvidia/custom")
    assert s.llm_models[0] == "nvidia/custom"


def test_provider_block_model_wins_over_generic(fresh):
    # v2.6: the active provider's own var is primary; LLM_MODEL is the shared
    # fallback. (v2.5 had the opposite ordering.)
    s = fresh(LLM_PROVIDER="nvidia", LLM_MODEL="a/b", NVIDIA_MODEL="c/d")
    assert s.llm_models[0] == "c/d"
    s2 = fresh(LLM_PROVIDER="nvidia", LLM_MODEL="a/b")
    assert s2.llm_models[0] == "a/b"


def test_chain_is_deduped(fresh):
    s = fresh(LLM_MODEL="x/y", LLM_MODELS_FALLBACK="x/y, z/w, z/w")
    assert s.llm_models == ["x/y", "z/w"]


# ---------------------------------------------------------------------------
# key
# ---------------------------------------------------------------------------

def test_api_key_resolution(fresh):
    # nvidia provider: NVIDIA_API_KEY primary, LLM_API_KEY the shared fallback
    assert fresh(NVIDIA_API_KEY="nv").llm_api_key == "nv"
    assert fresh(LLM_API_KEY="llm", NVIDIA_API_KEY="nv").llm_api_key == "nv"
    assert fresh(LLM_API_KEY="llm").llm_api_key == "llm"
    assert fresh(LLM_PROVIDER="openai", OPENAI_API_KEY="oa", NVIDIA_API_KEY="nv").llm_api_key == "oa"


def test_not_configured_without_a_key(fresh):
    assert fresh().llm_configured is False
    assert fresh(NVIDIA_API_KEY="k").llm_configured is True


# ---------------------------------------------------------------------------
# no key leakage
# ---------------------------------------------------------------------------

def test_public_config_never_contains_the_key(fresh):
    s = fresh(LLM_API_KEY="super-secret-key-value", NVIDIA_API_KEY="also-secret")
    cfg = s.llm_public_config()
    blob = repr(cfg)
    assert "super-secret-key-value" not in blob
    assert "also-secret" not in blob
    assert "api_key" not in cfg and "key" not in cfg


def test_gemini_client_get_config_has_no_key(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "leak-me-please")
    cfg = gemini_client.get_config()
    assert "leak-me-please" not in repr(cfg)
    assert cfg["configured"] is True


def test_health_endpoint_does_not_leak_key(client, monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "hidden-key-xyz")
    r = client.get("/api/health")
    assert r.status_code == 200
    assert "hidden-key-xyz" not in r.text
    assert r.json()["gemini_configured"] is True


# ---------------------------------------------------------------------------
# client rebuild + limits are read live
# ---------------------------------------------------------------------------

def test_reset_client_rebuilds_from_settings(monkeypatch):
    gemini_client.reset_client()
    c1 = gemini_client._get_client()
    c2 = gemini_client._get_client()
    assert c1 is c2                      # cached
    gemini_client.reset_client()
    c3 = gemini_client._get_client()
    assert c3 is not c1                  # rebuilt
    gemini_client.reset_client()


def test_rate_limit_uses_configured_rpm(monkeypatch):
    monkeypatch.setattr(settings, "LLM_MAX_CALLS_PER_MINUTE", 2)
    monkeypatch.setattr(gemini_client, "_CALL_TIMESTAMPS", [])
    gemini_client._check_rate_limit()
    gemini_client._check_rate_limit()
    with pytest.raises(RuntimeError, match="2 calls/min"):
        gemini_client._check_rate_limit()


def test_get_model_reflects_a_settings_override(monkeypatch):
    monkeypatch.setattr(settings, "LLM_MODEL", "override/model")
    assert gemini_client._get_model()[0] == "override/model"
    monkeypatch.setattr(settings, "LLM_MODEL", "")
