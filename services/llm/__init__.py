"""Unified LLM layer — ONE implementation for every OpenAI-compatible backend.

``BaseLLMProvider`` (interface + model fallback chain + healthcheck + sanitise)
and the single :class:`OpenAICompatibleProvider`. A backend
(nvidia / openai / gemini / mistral / groq / openai_compatible / custom) is a
*configuration*, not a class — see ``providers.provider_config``.

Business code never imports these — it calls
``services.gemini_client.call_gemini``, which delegates to the active provider.
"""

from services.llm.base import BaseLLMProvider, Completion, LLMError, ProviderHealth
from services.llm.providers import (
    PROVIDER_NAMES, OpenAICompatibleProvider, build_active_provider,
    build_for, build_provider, provider_config, reset_instances,
)
from services.llm.routing import Route, RouteStep, resolve as resolve_route

__all__ = [
    "BaseLLMProvider", "Completion", "LLMError", "ProviderHealth",
    "OpenAICompatibleProvider",
    "PROVIDER_NAMES", "build_active_provider", "build_provider", "build_for",
    "provider_config", "reset_instances",
    "Route", "RouteStep", "resolve_route",
]
