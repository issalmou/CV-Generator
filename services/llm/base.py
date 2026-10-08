"""``BaseLLMProvider`` — the interface every LLM backend implements.

All current providers speak the OpenAI Chat Completions API, so the shared
:class:`OpenAILikeProvider` (in ``providers.py``) does the real work and the
named providers only supply defaults. The interface is kept minimal and
model-agnostic so business code never depends on a specific backend.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass


class LLMError(RuntimeError):
    """Any LLM call failure. Message is safe to log — never carries the key."""


@dataclass
class ProviderHealth:
    ok: bool
    provider: str
    model: str | None
    duration_ms: int
    error: str | None = None


@dataclass(frozen=True)
class Completion:
    """The result of :meth:`BaseLLMProvider.complete` — the text plus the
    metadata the profiler (and, later, the router) needs. ``prompt_tokens`` /
    ``completion_tokens`` come straight from the provider's ``usage`` block
    when it returns one, else ``None``. ``attempts`` is how many models in
    the fallback chain were tried; ``fallback_used`` is True when the answer
    did not come from the primary model."""
    text: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    attempts: int = 1
    fallback_used: bool = False


class BaseLLMProvider(ABC):
    #: registry key — ``settings.LLM_PROVIDER`` values
    name: str = "base"

    def __init__(self, *, base_url: str, api_key: str, models: list[str],
                 temperature: float, max_tokens: int, timeout: float) -> None:
        self._base_url = base_url
        self._api_key = api_key or ""
        self._models = [m for m in models if m]
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout

    # -- introspection (never exposes the key) -------------------------------

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def models(self) -> list[str]:
        return list(self._models)

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def public_config(self) -> dict:
        return {
            "provider": self.name,
            "base_url": self._base_url,
            "model": self._models[0] if self._models else None,
            "fallback_models": self._models[1:],
            "temperature": self._temperature,
            "max_output_tokens": self._max_tokens,
            "request_timeout": self._timeout,
            "configured": self.configured,
        }

    # -- the call ----------------------------------------------------------

    @abstractmethod
    def _complete_one(self, model: str, system: str, prompt: str, *,
                      response_format: dict | None = None,
                      temperature: float | None = None) -> tuple[str, dict]:
        """One completion against ``model``. Returns ``(text, usage)`` where
        ``usage`` is ``{"prompt_tokens": int|None, "completion_tokens": int|None}``
        (empty dict when the provider gives no usage block). Raise on failure.

        ``response_format`` — an OpenAI ``response_format`` dict (json_schema /
        json_object) applied when the model supports it, silently downgraded
        otherwise (the caller still validates + can repair).
        ``temperature`` — per-call override of the provider default (structured
        calls pass ``0.0``)."""

    def complete(self, system: str, prompt: str, *,
                 response_format: dict | None = None,
                 temperature: float | None = None) -> Completion:
        """Try each model in the fallback chain. Returns a :class:`Completion`.
        Raises :class:`LLMError` when every model fails."""
        if not self.configured:
            raise LLMError(f"LLM provider {self.name!r} has no API key configured.")
        last: Exception | None = None
        for i, model in enumerate(self._models):
            try:
                text, usage = self._complete_one(
                    model, system, prompt,
                    response_format=response_format, temperature=temperature)
                if text:
                    return Completion(
                        text=text,
                        model=model,
                        prompt_tokens=(usage or {}).get("prompt_tokens"),
                        completion_tokens=(usage or {}).get("completion_tokens"),
                        attempts=i + 1,
                        fallback_used=(i > 0),
                    )
                last = LLMError("empty response")
            except Exception as exc:  # noqa: BLE001 — try the next model
                last = exc
        raise LLMError(f"no {self.name} model available (last error: {last})")

    # -- health ----------------------------------------------------------

    def healthcheck(self, *, prompt: str = "ping") -> ProviderHealth:
        started = time.monotonic()
        model = self._models[0] if self._models else None
        try:
            result = self.complete("You are a health probe. Reply with 'ok'.", prompt)
            return ProviderHealth(True, self.name, result.model,
                                  int((time.monotonic() - started) * 1000))
        except Exception as exc:  # noqa: BLE001
            return ProviderHealth(False, self.name, model,
                                  int((time.monotonic() - started) * 1000),
                                  error=_sanitize(str(exc)))


def _sanitize(msg: str) -> str:
    """Strip anything key- or URL-shaped from an error message before it is
    logged or returned. Covers OpenAI (`sk-`), NVIDIA (`nvapi-`), Google
    (`AIza`), Groq (`gsk_`), plus bare 32+ char hex/base64 blobs (Mistral etc.)
    and any `Authorization` / `api_key` / `key=` value."""
    import re
    msg = re.sub(r"\b(sk-|nvapi-|AIza|gsk_)[A-Za-z0-9_\-]{6,}", "[key]", msg)
    # "Authorization: Bearer xxx", "api_key=xxx", "token: xxx" -> redact the value
    msg = re.sub(r"(?i)\b(authorization|api[_-]?key|token|key)\b\s*[:=]?\s*(bearer\s+)?[^\s,;)'\"]+",
                 r"\1=[redacted]", msg)
    msg = re.sub(r"(?i)\bbearer\s+[^\s,;)'\"]+", "bearer [redacted]", msg)
    msg = re.sub(r"\b[A-Za-z0-9]{32,}\b", "[token]", msg)
    msg = re.sub(r"(https?://[^\s'\"]+)", "[url]", msg)
    return msg[:300]
