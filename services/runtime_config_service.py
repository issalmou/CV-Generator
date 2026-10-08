"""
Runtime LLM configuration — the bits a superadmin can change without a
redeploy (``PATCH /api/admin/llm``). Persisted in ``runtime_config`` (key
``"llm"``), applied onto the process ``settings`` object, then the LLM client
is rebuilt. **API keys are never accepted, stored or returned here** — they
stay environment secrets.

``apply_llm_overrides`` runs on startup; ``save_llm_overrides`` runs on PATCH
and does a healthcheck + automatic rollback if the new config does not work.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from config import LLM_PROVIDER_NAMES, settings
from models import RuntimeConfig

logger = logging.getLogger(__name__)

_KEY = "llm"

# only these may be changed at runtime — never a *_API_KEY
_ALLOWED = {
    "LLM_PROVIDER": str,
    "LLM_MODEL": str,
    "LLM_MODELS_FALLBACK": str,
    "LLM_BASE_URL": str,
    "NVIDIA_MODEL": str, "NVIDIA_BASE_URL": str,
    "OPENAI_MODEL": str, "OPENAI_BASE_URL": str,
    "GEMINI_MODEL": str, "GEMINI_BASE_URL": str,
    "MISTRAL_MODEL": str, "MISTRAL_BASE_URL": str,
    "GROQ_MODEL": str, "GROQ_BASE_URL": str,
    "OPENAI_COMPATIBLE_MODEL": str, "OPENAI_COMPATIBLE_BASE_URL": str,
    "CUSTOM_LLM_MODEL": str, "CUSTOM_LLM_BASE_URL": str,
    "LLM_TEMPERATURE": float,
    "LLM_MAX_OUTPUT_TOKENS": int,
    "LLM_REQUEST_TIMEOUT": float,
    "LLM_MAX_CALLS_PER_MINUTE": int,
}
_PROVIDERS = LLM_PROVIDER_NAMES


def _snapshot() -> dict:
    return {k: getattr(settings, k) for k in _ALLOWED}


def _apply(values: dict) -> None:
    for k, v in values.items():
        if k in _ALLOWED and v is not None:
            try:
                setattr(settings, k, _ALLOWED[k](v))
            except (TypeError, ValueError):
                logger.warning("[runtime-config] ignoring bad value for %s", k)
    from services.gemini_client import reset_client
    reset_client()


def apply_llm_overrides(db: Session) -> dict:
    """Load the persisted LLM overrides and apply them. Called on startup."""
    row = db.get(RuntimeConfig, _KEY)
    if row and isinstance(row.value, dict) and row.value:
        _apply(row.value)
        logger.info("[runtime-config] applied %d LLM override(s)", len(row.value))
        return row.value
    return {}


def get_llm_overrides(db: Session) -> dict:
    row = db.get(RuntimeConfig, _KEY)
    return dict(row.value) if row and isinstance(row.value, dict) else {}


class ConfigError(ValueError):
    """A rejected runtime-config change (mapped to 400)."""


def save_llm_overrides(db: Session, patch: dict) -> dict:
    """Validate + persist + apply a partial LLM config change. Runs a
    healthcheck and **rolls back** (both settings and the DB row) if it fails.
    Returns the safe public config on success."""
    clean: dict = {}
    for k, v in patch.items():
        if v is None:
            continue
        if k not in _ALLOWED:
            raise ConfigError(f"'{k}' is not a runtime-changeable setting "
                              f"(API keys are environment secrets).")
        if k == "LLM_PROVIDER" and str(v).lower() not in _PROVIDERS:
            raise ConfigError(f"unknown provider '{v}' — one of {', '.join(_PROVIDERS)}")
        try:
            clean[k] = _ALLOWED[k](v)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"bad value for '{k}': {exc}") from exc
    if not clean:
        raise ConfigError("nothing to change")

    before = _snapshot()
    prev_row = db.get(RuntimeConfig, _KEY)
    prev_value = dict(prev_row.value) if prev_row and isinstance(prev_row.value, dict) else {}

    merged = {**prev_value, **clean}
    _apply(merged)

    from services.gemini_client import healthcheck
    health = healthcheck()
    if not health.get("ok"):
        _apply(before)   # roll settings back
        raise ConfigError(
            f"the new configuration failed its health check ({health.get('error') or 'unknown'}) "
            "— rolled back."
        )

    if prev_row is None:
        db.add(RuntimeConfig(key=_KEY, value=merged))
    else:
        prev_row.value = merged
    db.commit()

    from services.gemini_client import get_config
    return get_config()
