"""
Job search orchestration — **platform-agnostic, LLM-free**.

    request -> cache?  -> pick providers -> fan out (thread pool, isolated)
            -> normalize (providers already did) -> deduplicate
            -> upsert into job_offers + freshness -> rank -> cache -> paginate

Contains no LinkedIn/Indeed/... specific logic — it only talks to the
:data:`services.providers.registry.registry`. A provider failing, timing
out or returning nothing never aborts the search; the response still
carries the successful providers' results plus an honest ``sources`` map.
"""

from __future__ import annotations

import asyncio
import atexit
import ipaddress as _ipaddress
import logging
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urlparse as _urlparse

# progress(providers_completed, providers_total, results_so_far, failed_names)
# -> return False to ask the fan-out to stop early (cancellation -> partial results)
ProgressCb = Callable[[int, int, int, list], bool]

from sqlalchemy import select
from sqlalchemy.orm import Session

from config import settings
from models import JobOffer
from schemas.jobs import (
    FreshnessStatus, JobOfferOut, JobSearchRequest, JobSearchResponse,
    NormalizedOffer, ProviderState, ProviderStatus, SortOrder, freshness_at_least,
)
from services.cache_service import cache
from services.jobs.dedup import deduplicate
from services.jobs.freshness import classify, recompute
from services.jobs.ranking import rank
from services.providers.registry import registry

logger = logging.getLogger(__name__)


_IN_CHUNK = 400          # keep every prefetch IN-clause well under the SQLite var limit


# --- shared provider-fan-out executor -------------------------------------
# ONE process-wide pool (not one per search) so N simultaneous searches can
# never spawn N*20 threads / connections. Sized to
# ``JOB_SEARCH_GLOBAL_CONCURRENCY``; rebuilt if that setting changes (tests).
_EXECUTOR: ThreadPoolExecutor | None = None
_EXECUTOR_WORKERS: int = 0
_EXECUTOR_LOCK = threading.Lock()


def _executor() -> ThreadPoolExecutor:
    global _EXECUTOR, _EXECUTOR_WORKERS
    want = max(1, settings.JOB_SEARCH_GLOBAL_CONCURRENCY)
    with _EXECUTOR_LOCK:
        if _EXECUTOR is None or _EXECUTOR_WORKERS != want:
            old = _EXECUTOR
            _EXECUTOR = ThreadPoolExecutor(max_workers=want, thread_name_prefix="jobsearch")
            _EXECUTOR_WORKERS = want
            if old is not None:
                old.shutdown(wait=False, cancel_futures=True)
        return _EXECUTOR


@atexit.register
def _shutdown_executor() -> None:  # pragma: no cover - process teardown
    with _EXECUTOR_LOCK:
        if _EXECUTOR is not None:
            _EXECUTOR.shutdown(wait=False, cancel_futures=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _index_row(index: dict, row) -> None:
    if row.content_hash:
        index[("h", row.content_hash)] = row
    index[("p", row.source, row.source_job_id)] = row


class JobSearchService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------

    def search(self, request: JobSearchRequest,
               *, progress: ProgressCb | None = None) -> JobSearchResponse:
        cache_key = cache.key("jobs", "search", cache.digest(self._canonical(request)))
        cached = cache.get(cache_key)
        if cached is not None:
            return self._paginate(JobSearchResponse.model_validate(cached), request, from_cache=True)

        providers = registry.active(request.sources)
        sources, states = self._initial_sources(request)

        normalized: list[NormalizedOffer] = []
        if providers:
            normalized = self._fan_out(providers, request, sources, states, progress=progress)

        return self._finish(request, normalized, sources, states, cache_key)

    async def search_async(self, request: JobSearchRequest,
                           *, progress: ProgressCb | None = None) -> JobSearchResponse:
        """Async twin of :meth:`search`, used by the conversation agent
        (``JobConversationAgent``) so provider I/O is awaited concurrently
        instead of blocking the caller's thread.

        Every deterministic step — cache lookup, dedup, DB upsert, freshness,
        ranking, pagination — is the exact same synchronous code as
        :meth:`search` (see :meth:`_finish`); no provider becomes async, no
        new HTTP client, no new dependency, and SQLAlchemy stays synchronous.
        Only the fan-out itself (:meth:`_fan_out_async`) is real ``asyncio``:
        each provider call is dispatched to the SAME shared, process-wide
        thread pool via ``loop.run_in_executor`` and awaited with
        :func:`asyncio.gather`-style concurrency, so N independent providers
        take roughly the slowest one's time, not the sum."""
        cache_key = cache.key("jobs", "search", cache.digest(self._canonical(request)))
        cached = cache.get(cache_key)
        if cached is not None:
            return self._paginate(JobSearchResponse.model_validate(cached), request, from_cache=True)

        providers = registry.active(request.sources)
        sources, states = self._initial_sources(request)

        normalized: list[NormalizedOffer] = []
        if providers:
            normalized = await self._fan_out_async(providers, request, sources, states, progress=progress)

        return self._finish(request, normalized, sources, states, cache_key)

    def _finish(
        self,
        request: JobSearchRequest,
        normalized: list[NormalizedOffer],
        sources: dict[str, ProviderStatus],
        states: dict[str, ProviderState],
        cache_key: str,
    ) -> JobSearchResponse:
        """The deterministic tail shared by :meth:`search` and
        :meth:`search_async` — dedup, DB upsert, freshness, ranking, cache
        write, pagination. Never touches a provider or the network."""
        merged = self._hard_filter(
            deduplicate(self._with_usable_link(normalized)), request.context
        )
        existing = self._prefetch_rows(merged)
        rows = [self._upsert(offer, existing) for offer in merged]
        self.db.commit()

        rows = self._apply_freshness(rows, request)
        rows = rank(rows, request.context)
        rows.sort(
            key=lambda r: (r.match_score, r.posted_at or datetime.min.replace(tzinfo=timezone.utc)),
            reverse=True,
        )
        if request.sort == SortOrder.date:
            rows.sort(
                key=lambda r: r.posted_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
        rows = rows[: settings.JOB_SEARCH_MAX_RESULTS]

        full = JobSearchResponse(
            query=request.context,
            results=[self._to_out(r) for r in rows],
            total=len(rows),
            page=1,
            page_size=request.page_size,
            sources=sources,
            provider_states=states,
            from_cache=False,
        )
        cache.set(cache_key, full.model_dump(mode="json"), ttl=settings.JOB_SEARCH_CACHE_TTL)
        return self._paginate(full, request, from_cache=False)

    # ------------------------------------------------------------------
    # provider fan-out
    # ------------------------------------------------------------------

    def _fan_out(
        self,
        providers: list,
        request: JobSearchRequest,
        sources: dict[str, ProviderStatus],
        states: dict[str, ProviderState],
        *,
        progress: ProgressCb | None = None,
    ) -> list[NormalizedOffer]:
        """Query every provider concurrently and collect what finishes before a
        single wall-clock deadline.

        Provider calls run on a **shared** process-wide pool
        (``JOB_SEARCH_GLOBAL_CONCURRENCY``) with a per-search cap
        (``JOB_SEARCH_MAX_CONCURRENCY``, enforced by a semaphore each provider
        task acquires). Results are harvested as they complete; when the shared
        ``JOB_SEARCH_DEADLINE`` elapses, whatever is still running is abandoned
        (marked ``temporarily_unavailable``, breaker tripped) — so one hung
        provider costs the deadline *once*, never once per hung provider, and a
        fast provider is never held up behind a slow one.
        """
        per_provider = settings.JOB_PROVIDER_MAX_RESULTS
        deadline = time.monotonic() + max(0.1, settings.JOB_SEARCH_DEADLINE)
        normalized: list[NormalizedOffer] = []
        per_search = max(1, min(len(providers), settings.JOB_SEARCH_MAX_CONCURRENCY))
        gate = threading.Semaphore(per_search)

        def _call(p):
            # bound per-search concurrency without blocking a whole worker slot
            # for the whole time — a task past the deadline still returns fast
            # because the provider checks its own deadline.
            with gate:
                return p.search(request.context, limit=per_provider)

        pool = _executor()   # shared, process-wide — never shut down here
        futures = {pool.submit(_call, p): p for p in providers}
        pending = set(futures)
        total = len(providers)
        completed = 0
        failed: list[str] = []
        cancelled = False

        def _report() -> bool:
            if progress is None:
                return True
            try:
                return progress(completed, total, len(normalized), list(failed)) is not False
            except Exception:  # noqa: BLE001 — progress reporting must never break a search
                return True

        _report()   # initial: 0/total

        while pending and not cancelled:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            done, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            if not done:
                break  # deadline hit with nothing newly finished
            for future in done:
                provider = futures[future]
                completed += 1
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001 — never let one break the batch
                    provider.breaker.record_failure()
                    states[provider.name] = ProviderState.error
                    sources[provider.name] = ProviderStatus.unavailable
                    failed.append(provider.name)
                    logger.exception("[jobsearch] %s crashed: %s", provider.name, exc)
                    continue
                states[provider.name] = result.state or ProviderState.available
                sources[provider.name] = result.status
                if result.status is not ProviderStatus.success:
                    failed.append(provider.name)
                normalized.extend(result.offers)
            if not _report():
                cancelled = True
                logger.info("[jobsearch] fan-out stopped early on request — returning partial results")

        # anything still running when the deadline elapsed (or the search was
        # cancelled) — abandon it. The task keeps going on the shared pool,
        # bounded by the provider's own per-request timeout, then the worker is
        # freed. On a real deadline we trip the breaker; on an explicit
        # cancellation we do not (the provider did nothing wrong).
        for future in pending:
            provider = futures[future]
            future.cancel()
            if not cancelled:
                provider.breaker.record_failure()
                logger.warning(
                    "[jobsearch] %s did not finish within the %.0fs fan-out deadline",
                    provider.name, settings.JOB_SEARCH_DEADLINE,
                )
            states[provider.name] = ProviderState.temporarily_unavailable
            sources[provider.name] = ProviderStatus.unavailable
        return normalized

    async def _fan_out_async(
        self,
        providers: list,
        request: JobSearchRequest,
        sources: dict[str, ProviderStatus],
        states: dict[str, ProviderState],
        *,
        progress: ProgressCb | None = None,
    ) -> list[NormalizedOffer]:
        """Async twin of :meth:`_fan_out` — identical semantics (one shared
        wall-clock deadline, a per-search concurrency cap, per-provider fault
        isolation, an abandoned-at-deadline provider trips its breaker); the
        only difference is *how* completions are awaited. Each provider call
        still runs its normal, synchronous ``search()`` — dispatched onto the
        SAME shared, process-wide thread pool via ``loop.run_in_executor`` —
        so independent providers genuinely run concurrently under asyncio
        without any provider, without ``http.py``, and without SQLAlchemy
        becoming async."""
        per_provider = settings.JOB_PROVIDER_MAX_RESULTS
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.1, settings.JOB_SEARCH_DEADLINE)
        normalized: list[NormalizedOffer] = []
        per_search = max(1, min(len(providers), settings.JOB_SEARCH_MAX_CONCURRENCY))
        gate = asyncio.Semaphore(per_search)
        pool = _executor()   # shared, process-wide — never shut down here

        async def _call(p):
            async with gate:
                return await loop.run_in_executor(pool, lambda: p.search(request.context, limit=per_provider))

        tasks = {asyncio.ensure_future(_call(p)): p for p in providers}
        pending = set(tasks)
        total = len(providers)
        completed = 0
        failed: list[str] = []
        cancelled = False

        def _report() -> bool:
            if progress is None:
                return True
            try:
                return progress(completed, total, len(normalized), list(failed)) is not False
            except Exception:  # noqa: BLE001 — progress reporting must never break a search
                return True

        _report()   # initial: 0/total

        while pending and not cancelled:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            done, pending = await asyncio.wait(
                pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
            if not done:
                break  # deadline hit with nothing newly finished
            for task in done:
                provider = tasks[task]
                completed += 1
                try:
                    result = task.result()
                except Exception as exc:  # noqa: BLE001 — never let one break the batch
                    provider.breaker.record_failure()
                    states[provider.name] = ProviderState.error
                    sources[provider.name] = ProviderStatus.unavailable
                    failed.append(provider.name)
                    logger.exception("[jobsearch] %s crashed (async): %s", provider.name, exc)
                    continue
                states[provider.name] = result.state or ProviderState.available
                sources[provider.name] = result.status
                if result.status is not ProviderStatus.success:
                    failed.append(provider.name)
                normalized.extend(result.offers)
            if not _report():
                cancelled = True
                logger.info("[jobsearch] async fan-out stopped early on request — returning partial results")

        # anything still running when the deadline elapsed (or the search was
        # cancelled) — abandon it, same as the sync fan-out: cancelling the
        # asyncio Task does not forcibly kill the underlying thread if it has
        # already started (Python offers no safe way to do that), so the
        # provider call keeps running on the shared pool, bounded by its own
        # per-request timeout, then the worker is freed.
        for task in pending:
            provider = tasks[task]
            task.cancel()
            if not cancelled:
                provider.breaker.record_failure()
                logger.warning(
                    "[jobsearch] %s did not finish within the %.0fs async fan-out deadline",
                    provider.name, settings.JOB_SEARCH_DEADLINE,
                )
            states[provider.name] = ProviderState.temporarily_unavailable
            sources[provider.name] = ProviderStatus.unavailable
        return normalized

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _canonical(request: JobSearchRequest) -> dict:
        data = request.model_dump(mode="json")
        data.pop("page", None)
        data.pop("page_size", None)
        return data

    @staticmethod
    def _initial_sources(
        request: JobSearchRequest,
    ) -> tuple[dict[str, ProviderStatus], dict[str, ProviderState]]:
        sources: dict[str, ProviderStatus] = {}
        states: dict[str, ProviderState] = {}
        wanted = {s.lower() for s in request.sources} if request.sources else None
        for info in registry.all_info():
            if wanted is not None and info.name not in wanted:
                continue
            if not info.enabled:
                state = ProviderState.disabled
            elif info.circuit_state == "open":
                state = ProviderState.temporarily_unavailable
            else:
                state = ProviderState.temporarily_unavailable  # overwritten on run
            states[info.name] = state
            sources[info.name] = state.to_status()
        return sources, states

    @staticmethod
    def _with_usable_link(offers: list[NormalizedOffer]) -> list[NormalizedOffer]:
        """Every offer we surface must carry a real, safe link back to the
        original posting. An offer whose ``source_url`` is missing, blank, not
        ``http(s)``, carries credentials, or points at an internal / non-public
        host is **dropped** — never shown with a dead link, never repaired, never
        given a fabricated URL. (SSRF at *fetch* time is a separate guard in
        ``http.py``; this is about what we return to the user.)"""
        out: list[NormalizedOffer] = []
        dropped: dict[str, int] = {}
        for offer in offers:
            url = (offer.source_url or "").strip()
            ok = _usable_offer_url(url)
            if ok:
                if url != offer.source_url:
                    offer.source_url = url
                out.append(offer)
            else:
                dropped[offer.source] = dropped.get(offer.source, 0) + 1
        for source, n in dropped.items():
            logger.warning("[jobsearch] dropped %d %s offer(s) with no usable source_url", n, source)
        return out

    @staticmethod
    def _hard_filter(offers: list[NormalizedOffer], ctx) -> list[NormalizedOffer]:
        """Enforce the caller's hard constraints uniformly (a provider that
        forgot to filter cannot leak a wrong job/internship or an excluded
        company into the results)."""
        want_type = ctx.job_type.value if ctx.job_type else None
        excluded = [e.lower() for e in ctx.excluded_companies]
        excluded_kw = [k.lower() for k in getattr(ctx, "excluded_keywords", []) if len(k) >= 3]
        out: list[NormalizedOffer] = []
        for offer in offers:
            otype = _enum_value(offer.job_type)
            if want_type in ("job", "internship") and otype != want_type:
                continue
            if excluded and offer.company and any(x in offer.company.lower() for x in excluded):
                continue
            if excluded_kw:
                title = (offer.title or "").lower()
                if any(re.search(rf"\b{re.escape(k)}\b", title) for k in excluded_kw):
                    continue
            out.append(offer)
        return out

    def _prefetch_rows(self, offers: list[NormalizedOffer]) -> dict:
        """Load every existing ``JobOffer`` a merged offer could map to — by
        ``content_hash`` and by ``(source, source_job_id)`` — in a handful of
        chunked SELECTs instead of two per offer. Chunked to stay well under
        the SQLite variable limit."""
        if not offers:
            return {}
        index: dict = {}
        hashes = sorted({o.content_hash() for o in offers})
        for i in range(0, len(hashes), _IN_CHUNK):
            stmt = select(JobOffer).where(JobOffer.content_hash.in_(hashes[i:i + _IN_CHUNK]))
            for row in self.db.scalars(stmt):
                _index_row(index, row)

        by_source: dict[str, list[str]] = {}
        for o in offers:
            by_source.setdefault(o.source, []).append(o.source_job_id)
        for source, ids in by_source.items():
            uniq = sorted(set(ids))
            for i in range(0, len(uniq), _IN_CHUNK):
                stmt = select(JobOffer).where(
                    JobOffer.source == source,
                    JobOffer.source_job_id.in_(uniq[i:i + _IN_CHUNK]),
                )
                for row in self.db.scalars(stmt):
                    _index_row(index, row)
        return index

    def _upsert(self, offer: NormalizedOffer, existing: dict | None = None) -> JobOffer:
        now = _now()
        content_hash = offer.content_hash()
        if existing is not None:
            # `existing` already covers *both* lookup keys for every offer in
            # this batch — a miss here means the row genuinely does not exist.
            row = (existing.get(("h", content_hash))
                   or existing.get(("p", offer.source, offer.source_job_id)))
        else:
            row = self.db.scalar(select(JobOffer).where(JobOffer.content_hash == content_hash))
            if row is None:
                row = self.db.scalar(
                    select(JobOffer).where(
                        JobOffer.source == offer.source,
                        JobOffer.source_job_id == offer.source_job_id,
                    )
                )

        posted = offer.posted_at
        expires = offer.expires_at or (posted or now) + timedelta(days=settings.JOB_DEFAULT_EXPIRY_DAYS)

        if row is None:
            row = JobOffer(
                source=offer.source,
                source_job_id=offer.source_job_id,
                content_hash=content_hash,
                first_seen_at=now,
            )
            self.db.add(row)

        row.source_url = offer.source_url
        row.title = offer.title
        row.company = offer.company
        row.company_url = offer.company_url
        row.location = offer.location
        row.city = offer.city
        row.country = offer.country
        if (offer.description or "") and len(offer.description or "") >= len(row.description or ""):
            row.description = offer.description
        row.employment_type = offer.employment_type or row.employment_type
        row.job_type = _enum_value(offer.job_type) or row.job_type or "job"
        row.remote_type = _enum_value(offer.remote_type) or row.remote_type
        row.salary_min = offer.salary_min if offer.salary_min is not None else row.salary_min
        row.salary_max = offer.salary_max if offer.salary_max is not None else row.salary_max
        row.salary_currency = offer.salary_currency or row.salary_currency
        row.salary_period = offer.salary_period or row.salary_period
        row.experience_level = _enum_value(offer.experience_level) or row.experience_level
        row.skills = list(dict.fromkeys([*(row.skills or []), *offer.skills]))[:25]
        row.language = offer.language or row.language
        row.internship_duration_months = offer.internship_duration_months or row.internship_duration_months
        row.internship_start_date = offer.internship_start_date or row.internship_start_date
        row.metadata_json = {**(row.metadata_json or {}), **offer.metadata}
        row.also_seen_on = sorted(set(row.also_seen_on or []) | set(offer.also_seen_on))
        row.posted_at = posted or row.posted_at
        row.expires_at = expires
        row.scraped_at = now
        row.last_verified_at = now
        row.is_active = True
        row.freshness = FreshnessStatus.fresh.value
        return row

    def _apply_freshness(self, rows: list[JobOffer], request: JobSearchRequest) -> list[JobOffer]:
        now = _now()
        max_age = timedelta(days=request.max_age_days) if request.max_age_days else None
        out: list[JobOffer] = []
        for row in rows:
            status = recompute(row, now)
            if not row.is_active or status == FreshnessStatus.expired:
                continue
            if not freshness_at_least(status, request.min_freshness):
                continue
            if max_age and row.posted_at:
                posted = row.posted_at if row.posted_at.tzinfo else row.posted_at.replace(tzinfo=timezone.utc)
                if now - posted > max_age:
                    continue
            out.append(row)
        self.db.commit()
        return out

    @staticmethod
    def _to_out(row: JobOffer) -> JobOfferOut:
        return JobOfferOut(
            id=row.id,
            source=row.source,
            source_job_id=row.source_job_id,
            source_url=row.source_url,
            title=row.title,
            company=row.company,
            company_url=row.company_url,
            location=row.location,
            city=row.city,
            country=row.country,
            description=row.description,
            employment_type=row.employment_type,
            job_type=row.job_type,
            remote_type=row.remote_type,
            salary_min=row.salary_min,
            salary_max=row.salary_max,
            salary_currency=row.salary_currency,
            salary_period=row.salary_period,
            experience_level=row.experience_level,
            skills=row.skills or [],
            language=row.language,
            posted_at=row.posted_at,
            expires_at=row.expires_at,
            scraped_at=row.scraped_at,
            last_verified_at=row.last_verified_at,
            internship_duration_months=row.internship_duration_months,
            internship_start_date=row.internship_start_date,
            match_score=getattr(row, "match_score", 0.0),
            freshness=classify(row),
            is_active=row.is_active,
            also_seen_on=row.also_seen_on or [],
        )

    @staticmethod
    def _paginate(full: JobSearchResponse, request: JobSearchRequest, *, from_cache: bool) -> JobSearchResponse:
        results = full.results
        if from_cache:
            # A cache hit must never resurface a listing that has expired
            # since the entry was written — re-check freshness on the way out.
            results = [r for r in results if _cached_offer_still_fresh(r, request)]
        start = (request.page - 1) * request.page_size
        page_items = results[start:start + request.page_size]
        return JobSearchResponse(
            query=full.query,
            results=page_items,
            total=len(results) if from_cache else full.total,
            page=request.page,
            page_size=request.page_size,
            sources=full.sources,
            provider_states=full.provider_states,
            from_cache=from_cache,
        )


def _enum_value(value) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", value)


_INTERNAL_HOSTNAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "metadata"}


def _usable_offer_url(url: str) -> bool:
    """True only for an ``http(s)`` URL, with a host, no embedded credentials,
    no control characters, and not an internal / private / loopback target."""
    if not url or len(url) > 2048:
        return False
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in url):
        return False
    low = url.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        return False
    try:
        p = _urlparse(url)
    except ValueError:
        return False
    if p.username or p.password:
        return False
    host = (p.hostname or "").lower().rstrip(".")
    if not host or host in _INTERNAL_HOSTNAMES:
        return False
    try:
        ip = _ipaddress.ip_address(host)
    except ValueError:
        return True   # a domain name — fine (SSRF re-checked at fetch time)
    return not (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _cached_offer_still_fresh(offer, request: JobSearchRequest) -> bool:
    """Freshness re-check for a cached ``JobOfferOut`` (no DB access)."""
    now = _now()
    if not offer.is_active:
        return False
    expires = offer.expires_at
    if expires is not None:
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < now:
            return False
    if request.max_age_days and offer.posted_at:
        posted = offer.posted_at
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        if now - posted > timedelta(days=request.max_age_days):
            return False
    return True
