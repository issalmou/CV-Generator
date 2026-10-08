"""
LOT 4 — robust fallback + per-(provider,model) circuit breaker + sticky last-good.

- one attempt per step (no double SDK retry: OpenAI client max_retries=0);
- ANY provider error fast-fails to the next step;
- N consecutive failures open the breaker -> that step is skipped (still tried
  last-resort if every closed step also failed);
- after cooldown the breaker half-opens and one success closes it;
- once routing falls over to a healthy fallback it STAYS there (sticky last-good).
"""

from __future__ import annotations

import pytest

from services.llm import circuit
from services.llm.base import Completion, LLMError


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    from config import settings
    from services.llm import routing
    monkeypatch.setattr(settings, "LLM_ROUTING_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_CIRCUIT_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_CIRCUIT_FAIL_THRESHOLD", 3)
    monkeypatch.setattr(settings, "LLM_CIRCUIT_COOLDOWN_SECONDS", 0.05)
    monkeypatch.setattr(settings, "GROQ_API_KEY", "k")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "k")
    routing.reset()
    circuit.reset()
    yield
    routing.reset()
    circuit.reset()


def test_openai_client_has_no_sdk_retry():
    from services.llm.providers import build_for
    p = build_for("groq", "openai/gpt-oss-120b")
    assert p.client.max_retries == 0


# ---------------------------------------------------------------------------

class _Prov:
    def __init__(self, name, *, fail=False):
        self.name = name
        self.configured = True
        self.fail = fail
        self.calls = 0

    def complete(self, system, prompt, *, response_format=None, temperature=None):
        self.calls += 1
        if self.fail:
            raise LLMError(f"{self.name} 429 rate limit")
        return Completion(text=f"ok:{self.name}", model=f"{self.name}-m")


def _wire(monkeypatch, provs: dict):
    from services import gemini_client
    from services.llm import providers as prov
    monkeypatch.setattr(prov, "build_for", lambda p, m, **kw: provs[p])
    monkeypatch.setattr(gemini_client, "_check_rate_limit", lambda: None)


def test_fast_failover_one_attempt_per_step(monkeypatch):
    from services import gemini_client
    groq, gem = _Prov("groq", fail=True), _Prov("gemini")
    _wire(monkeypatch, {"groq": groq, "gemini": gem})
    out = gemini_client.call_gemini("hi", request_type="cover_letter", use_cache=False)
    assert out == "ok:gemini"
    assert groq.calls == 1   # tried exactly once, no retry


def test_breaker_opens_after_threshold_failures():
    for _ in range(2):
        circuit.record_failure("groq", "openai/gpt-oss-120b")
    assert not circuit.is_open("groq", "openai/gpt-oss-120b")
    circuit.record_failure("groq", "openai/gpt-oss-120b")
    assert circuit.is_open("groq", "openai/gpt-oss-120b")


def test_open_breaker_step_is_skipped_on_first_pass(monkeypatch):
    from services import gemini_client
    groq, gem = _Prov("groq"), _Prov("gemini")     # groq WOULD succeed
    _wire(monkeypatch, {"groq": groq, "gemini": gem})

    for _ in range(3):
        circuit.record_failure("groq", "openai/gpt-oss-120b")   # trip it

    out = gemini_client.call_gemini("hi", request_type="cover_letter", use_cache=False)
    assert out == "ok:gemini"       # gemini used even though groq would have worked
    assert groq.calls == 0          # skipped: circuit open


def test_open_breaker_is_last_resort_when_everything_else_fails(monkeypatch):
    from services import gemini_client
    groq, gem = _Prov("groq"), _Prov("gemini", fail=True)   # only groq can answer
    _wire(monkeypatch, {"groq": groq, "gemini": gem})
    for _ in range(3):
        circuit.record_failure("groq", "openai/gpt-oss-120b")

    out = gemini_client.call_gemini("hi", request_type="cover_letter", use_cache=False)
    assert out == "ok:groq"         # open breaker still tried as last resort


def test_sticky_last_good(monkeypatch):
    from services import gemini_client
    groq, gem = _Prov("groq", fail=True), _Prov("gemini")
    _wire(monkeypatch, {"groq": groq, "gemini": gem})

    gemini_client.call_gemini("hi", request_type="cover_letter", use_cache=False)
    lg = circuit.last_good("cover_letter")
    assert lg is not None and lg[0] == "gemini"

    groq.calls = gem.calls = 0
    gemini_client.call_gemini("hi", request_type="cover_letter", use_cache=False)
    # gemini is tried FIRST now — groq (primary) not even attempted
    assert gem.calls == 1 and groq.calls == 0


def test_breaker_half_opens_after_cooldown_and_recovers(monkeypatch):
    import time
    circuit.record_failure("groq", "m")
    circuit.record_failure("groq", "m")
    circuit.record_failure("groq", "m")
    assert circuit.is_open("groq", "m")
    time.sleep(0.06)
    assert circuit.is_open("groq", "m") is False   # half-open
    circuit.record_success("groq", "m")
    assert circuit.snapshot()["breakers"]["groq/m"]["fails"] == 0


def test_all_steps_fail_still_raises(monkeypatch):
    from services import gemini_client
    _wire(monkeypatch, {"groq": _Prov("groq", fail=True), "gemini": _Prov("gemini", fail=True)})
    with pytest.raises(RuntimeError, match="No LLM model available"):
        gemini_client.call_gemini("hi", request_type="cover_letter", use_cache=False)
