"""Phase 6 finalisation — the conversation agent's job search is REALLY async.

``JobSearchService.search_async`` reuses every deterministic step of
``search()`` unchanged (cache, dedup, DB upsert, freshness, ranking,
pagination — see ``_finish``); only the provider fan-out itself
(``_fan_out_async``) runs through ``asyncio``, dispatching each provider call
onto the SAME shared thread pool via ``loop.run_in_executor`` and awaiting
them concurrently. No provider becomes async, no new HTTP client, no new
LLM provider, no HTTP call to this backend's own API.

These tests use ``asyncio.run()`` directly (no pytest-asyncio dependency
needed) since ``search_async`` is a plain coroutine function.
"""

from __future__ import annotations

import asyncio
import time

from _jobs_helpers import FakeJobProvider, make_offer
from schemas.jobs import JobSearchContext, JobSearchRequest, ProviderState, ProviderStatus
from services.jobs.search_service import JobSearchService


def test_search_async_returns_the_same_shape_as_search(db, swap_providers):
    swap_providers([FakeJobProvider("arbeitnow", [make_offer(sid="1", title="Backend Engineer")])])
    req = JobSearchRequest(context=JobSearchContext(query="backend"))

    resp = asyncio.run(JobSearchService(db).search_async(req))

    assert resp.total == 1
    assert resp.results[0].title == "Backend Engineer"
    assert resp.sources["arbeitnow"] is ProviderStatus.success


def test_async_fanout_runs_independent_providers_concurrently(db, swap_providers):
    """5 providers each with a 0.25s artificial delay: run sequentially that
    is >= 1.25s; run concurrently (asyncio.gather-style) it should be close
    to a single provider's delay. This is the actual measured evidence that
    the parallelism is real, not cosmetic ``async def`` around blocking code."""
    delay = 0.25
    providers = [
        FakeJobProvider(f"p{i}", [make_offer(source=f"p{i}", sid="1", title=f"Role {i}")],
                        delay=delay)
        for i in range(5)
    ]
    swap_providers(providers)
    req = JobSearchRequest(context=JobSearchContext(query="x"))

    start = time.monotonic()
    resp = asyncio.run(JobSearchService(db).search_async(req))
    elapsed = time.monotonic() - start

    assert resp.total == 5
    # sequential would be 5 * 0.25s = 1.25s; concurrent should be well under
    # 2x a single provider's delay even with scheduling overhead.
    assert elapsed < delay * 2.5, f"fan-out took {elapsed:.2f}s — looks sequential, not concurrent"


def test_async_fanout_matches_sync_fanout_timing_characteristics(db, swap_providers):
    """Sanity check the async path isn't slower than the sync one for the
    same independent-provider workload — the whole point of Objective 4."""
    delay = 0.2
    providers = [
        FakeJobProvider(f"q{i}", [make_offer(source=f"q{i}", sid="1")], delay=delay)
        for i in range(4)
    ]
    req = JobSearchRequest(context=JobSearchContext(query="x"))

    swap_providers(providers)
    t0 = time.monotonic()
    JobSearchService(db).search(req)
    sync_elapsed = time.monotonic() - t0

    # cache would short-circuit an identical request, so change the query
    req2 = JobSearchRequest(context=JobSearchContext(query="y"))
    swap_providers(providers)
    t1 = time.monotonic()
    asyncio.run(JobSearchService(db).search_async(req2))
    async_elapsed = time.monotonic() - t1

    # Both are dominated by the same delay (parallel fan-out either way);
    # async must not be meaningfully slower than sync for this workload.
    assert async_elapsed < sync_elapsed + delay


def test_async_fanout_isolates_a_crashing_provider(db, swap_providers):
    good = FakeJobProvider("good", [make_offer(source="good", sid="1", title="Fine")])
    bad = FakeJobProvider("bad", [], behaviour="raise")
    swap_providers([good, bad])
    req = JobSearchRequest(context=JobSearchContext(query="x"))

    resp = asyncio.run(JobSearchService(db).search_async(req))

    assert resp.total == 1
    assert resp.results[0].title == "Fine"
    assert resp.sources["good"] is ProviderStatus.success
    assert resp.sources["bad"] is ProviderStatus.unavailable
    assert resp.provider_states["bad"] is ProviderState.error


def test_async_fanout_respects_the_shared_deadline(db, swap_providers, monkeypatch):
    """The slow provider's underlying thread cannot be forcibly killed once
    started (Python offers no safe way to do that) — cancelling its asyncio
    Task only stops the AWAITING agent from waiting on it. So the elapsed
    time is measured INSIDE the coroutine, right as ``search_async`` returns,
    not around ``asyncio.run()`` — whose own shutdown phase joins every
    leftover task, including the one still sleeping in its thread, which
    would otherwise make this measurement include that unrelated wait."""
    from config import settings

    monkeypatch.setattr(settings, "JOB_SEARCH_DEADLINE", 0.3)
    slow = FakeJobProvider("slow", [make_offer(source="slow", sid="1")], delay=1.5)
    fast = FakeJobProvider("fast", [make_offer(source="fast", sid="1", title="Quick")])
    swap_providers([slow, fast])
    req = JobSearchRequest(context=JobSearchContext(query="x"))

    timing: dict[str, float] = {}

    async def _run():
        t0 = time.monotonic()
        resp = await JobSearchService(db).search_async(req)
        timing["elapsed"] = time.monotonic() - t0
        return resp

    resp = asyncio.run(_run())

    assert timing["elapsed"] < 1.0, "the deadline should abandon the slow provider, not wait for it"
    assert resp.results and resp.results[0].title == "Quick"
    assert resp.provider_states["slow"] is ProviderState.temporarily_unavailable


def test_job_agent_and_search_service_never_import_httpx_directly():
    """The async path still goes straight from the agent to JobSearchService
    in Python (never HTTP to this backend's own /api/jobs/*, removed in
    Phase 6). `http.py` stays the sole outbound-HTTP chokepoint even for the
    new async fan-out — no provider or service module talks to `httpx`
    directly, async or not."""
    import inspect

    import services.conversations.job_agent_service as job_agent
    import services.jobs.search_service as search_service

    for mod in (job_agent, search_service):
        src = inspect.getsource(mod)
        assert "import httpx" not in src, mod.__name__
        assert "import requests" not in src, mod.__name__


def test_no_new_llm_provider_was_introduced():
    """Objective 4 forbids inventing AsyncGeminiProvider / AsyncOpenAIProvider
    / etc. — the job-search async work must not touch services/llm/ at all."""
    import inspect

    import services.conversations.job_agent_service as job_agent
    import services.jobs.search_service as search_service

    for mod in (job_agent, search_service):
        src = inspect.getsource(mod)
        assert "services.llm" not in src, mod.__name__
        assert "OpenAICompatibleProvider" not in src, mod.__name__
