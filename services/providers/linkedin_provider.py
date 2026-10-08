"""
LinkedIn — multi-strategy **public** collector (``name="linkedin"``).

Only **legitimate, publicly accessible** representations are used. There is
**no** Voyager / authenticated API, no ``li_at`` cookie, no login, no
browser session, no CAPTCHA solving, no stealth/anti-bot evasion — the
authenticated path is intentionally not implemented. A 403 / 451 / HTTP-999
-> ``blocked``; an ``/authwall`` / ``/checkpoint`` / sign-in page ->
``auth_required``; a 429 / timeout -> ``temporarily_unavailable``. In every
case the provider moves on and never affects the other providers.

Strategy chain (see the matrix in the plan / README):

    LIST      A  guest search results API   (HTML card list, paged by start=+25,
                 LINKEDIN_MAX_PAGES pages, f_TPR recency filter, salary off the card)
              B  guest search page          (HTML cards **merged with** the
                 embedded JSON-LD ItemList of JobPosting — dates / descriptions)
    ENRICH    C  guest job-posting fragment (HTML: description + criteria + apply URL)
              D  guest job view page        (JSON-LD JobPosting: dates, salary)

The provider tries A then B for the list. Enrichment runs only for offers
still missing a description, capped at ``LINKEDIN_ENRICH_MAX`` and the
per-provider deadline (guest enrichment is throttled to 1 req/host/s), and
never fails a result.
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from bs4 import BeautifulSoup

from config import settings
from schemas.jobs import JobSearchContext, NormalizedOffer
from services.providers import http
from services.providers.ats_common import rank_enrichment_targets
from services.providers.base import (
    ApplicationMethod, JobProvider, ProviderAuthRequired, ProviderBlocked,
    ProviderTemporarilyUnavailable, RevalidationResult,
)
from services.providers.parsing import (
    clean_text, first_jobposting, guess_experience_level, guess_job_type,
    guess_remote_type, html_to_text, json_ld_blocks, parse_salary,
    passes_context_filters, split_city_country, parse_posted_date, to_utc,
)

logger = logging.getLogger(__name__)

_HOST = "https://www.linkedin.com"
_PAGE_SIZE = 25                       # the guest search API returns ~25 cards / page
_URN_RE = re.compile(r"urn:li:jobPosting:(\d+)")
_VIEW_ID_RE = re.compile(r"/jobs/view/(?:[^/?]*-)?(\d+)")

_WT_MAP = {"onsite": "1", "remote": "2", "hybrid": "3"}
_EXPERIENCE_MAP = {
    "student": "1", "entry": "2", "junior": "3",
    "mid": "4", "senior": "4", "lead": "5",
}
_AUTHWALL_MARKERS = ("/authwall", "/checkpoint/challenge", "/uas/login",
                     "authwall", "join now to see", "sign in to see")


def _looks_like_authwall(url: str, html: str) -> bool:
    low_url = (url or "").lower()
    if any(m in low_url for m in ("/authwall", "/checkpoint", "/uas/login")):
        return True
    low = (html or "")[:6000].lower()
    return ("authwall" in low
            or ("sign in" in low and "join now" in low and "job-search-card" not in low))


class StrategyUnavailable(Exception):
    def __init__(self, strategy: str, reason: str) -> None:
        super().__init__(f"{strategy}: {reason}")
        self.strategy = strategy
        self.reason = reason


# ---------------------------------------------------------------------------
# LIST strategies
# ---------------------------------------------------------------------------

def _build_params(ctx: JobSearchContext, start: int, *, keywords: str | None = None) -> dict[str, str]:
    params: dict[str, str] = {"start": str(start)}
    kw = keywords if keywords is not None else ctx.query
    if kw:
        params["keywords"] = kw
    loc = ctx.location or ctx.city or ctx.country
    if loc:
        params["location"] = loc
    if ctx.remote_type and ctx.remote_type.value in _WT_MAP:
        params["f_WT"] = _WT_MAP[ctx.remote_type.value]
    if ctx.experience_level and ctx.experience_level.value in _EXPERIENCE_MAP:
        params["f_E"] = _EXPERIENCE_MAP[ctx.experience_level.value]
    if ctx.job_type and ctx.job_type.value == "internship":
        params["f_JT"] = "I"
    # LinkedIn's own recency filter — trims stale postings at the source so we
    # do not fetch + enrich + rank jobs the freshness step would drop anyway.
    days = settings.LINKEDIN_FRESHNESS_DAYS
    if days and days > 0:
        params["f_TPR"] = f"r{int(days) * 86400}"
    return params


def _parse_cards(html: str) -> list[NormalizedOffer]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("li div.base-card, li div.job-search-card, div.base-search-card")
    if not cards:
        cards = soup.select("li")
    offers: list[NormalizedOffer] = []
    for card in cards:
        urn = card.get("data-entity-urn") or ""
        m = _URN_RE.search(urn) or _URN_RE.search(str(card.get("data-row", "")))
        link_el = card.select_one("a.base-card__full-link, a.job-search-card__link, a[href*='/jobs/view/']")
        href = (link_el.get("href") if link_el else "") or ""
        job_id = m.group(1) if m else None
        if not job_id:
            vm = _VIEW_ID_RE.search(href)
            job_id = vm.group(1) if vm else None
        if not job_id:
            continue
        title_el = card.select_one("h3.base-search-card__title, h3.job-search-card__title, h3")
        company_el = card.select_one("h4.base-search-card__subtitle a, a.job-search-card__subtitle-link, h4 a")
        loc_el = card.select_one("span.job-search-card__location, span.job-result-card__location")
        time_el = card.select_one("time")
        salary_el = card.select_one(
            "span.job-search-card__salary-info, span.job-result-card__salary-info, div.salary"
        )
        title = clean_text(title_el.get_text()) if title_el else None
        if not title:
            continue
        location = clean_text(loc_el.get_text()) if loc_el else None
        city, country = split_city_country(location)
        url = href.split("?")[0] if href else f"{_HOST}/jobs/view/{job_id}"
        sal_min = sal_max = sal_cur = None
        if salary_el:
            sal_min, sal_max, sal_cur = parse_salary(salary_el.get_text(" ", strip=True))
        offers.append(NormalizedOffer(
            source="linkedin",
            source_job_id=job_id,
            source_url=url,
            title=title,
            company=clean_text(company_el.get_text()) if company_el else None,
            company_url=(company_el.get("href").split("?")[0]
                        if company_el and company_el.get("href") else None),
            location=location,
            city=city,
            country=country,
            job_type=guess_job_type(title),
            remote_type=guess_remote_type(title, location),
            experience_level=guess_experience_level(title),
            salary_min=sal_min,
            salary_max=sal_max,
            salary_currency=sal_cur,
            posted_at=parse_posted_date(time_el.get("datetime")) if time_el and time_el.get("datetime") else None,
            metadata={},
        ))
    return offers


def _parse_jsonld_itemlist(html: str) -> list[NormalizedOffer]:
    """Fallback list source: the ``ItemList`` of ``JobPosting`` LinkedIn embeds
    in the ``/jobs/search`` page ``<script type="application/ld+json">``."""
    offers: list[NormalizedOffer] = []
    seen: set[str] = set()
    for block in json_ld_blocks(html):
        items = block.get("itemListElement") if isinstance(block, dict) else None
        for element in items or []:
            node = element.get("item") if isinstance(element, dict) else None
            if not isinstance(node, dict) or "jobposting" not in str(node.get("@type", "")).lower():
                continue
            url = node.get("url") or ""
            m = _VIEW_ID_RE.search(url)
            job_id = m.group(1) if m else None
            title = clean_text(node.get("title") or node.get("name"))
            if not job_id or not title or job_id in seen:
                continue
            seen.add(job_id)
            org = node.get("hiringOrganization") or {}
            loc_node = node.get("jobLocation") or {}
            if isinstance(loc_node, list):
                loc_node = loc_node[0] if loc_node else {}
            addr = (loc_node.get("address") or {}) if isinstance(loc_node, dict) else {}
            location = ", ".join(str(x) for x in (
                addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry"),
            ) if x) or None
            city, country = split_city_country(location)
            description = html_to_text(node.get("description"))
            offers.append(NormalizedOffer(
                source="linkedin",
                source_job_id=job_id,
                source_url=(url or f"{_HOST}/jobs/view/{job_id}").split("?")[0],
                title=title,
                company=clean_text(org.get("name")) if isinstance(org, dict) else None,
                location=location,
                city=city,
                country=country,
                description=description,
                job_type=guess_job_type(title, description),
                remote_type=guess_remote_type(title, description, location),
                experience_level=guess_experience_level(title, description),
                posted_at=parse_posted_date(node.get("datePosted")),
                expires_at=to_utc(node.get("validThrough")),
                metadata={"list_source": "jsonld_itemlist"},
            ))
    return offers


class _GuestSearchApiStrategy:
    name = "guest_search_api"
    url = f"{_HOST}/jobs-guest/jobs/api/seeMoreJobPostings/search"

    def fetch(self, ctx: JobSearchContext, *, limit: int, deadline: float = 0.0,
              keywords: str | None = None) -> list[NormalizedOffer]:
        offers: list[NormalizedOffer] = []
        seen: set[str] = set()
        for page in range(settings.LINKEDIN_MAX_PAGES):
            if deadline and time.monotonic() >= deadline:
                break
            try:
                result = http.fetch(
                    self.url, params=_build_params(ctx, page * _PAGE_SIZE, keywords=keywords),
                    allowed_hosts=("linkedin.com",), accept_language=ctx.language,
                )
            except http.AccessDenied as exc:
                raise StrategyUnavailable(self.name, exc.reason) from exc
            except http.HttpError as exc:
                raise StrategyUnavailable(self.name, str(exc)) from exc
            if _looks_like_authwall(result.url, result.text):
                raise ProviderAuthRequired("linkedin authwall / sign-in page")
            page_offers = _parse_cards(result.text)
            fresh = 0
            for offer in page_offers:
                if offer.source_job_id in seen:
                    continue
                seen.add(offer.source_job_id)
                fresh += 1
                offers.append(offer)
                if len(offers) >= limit:
                    return offers
            if not fresh:                      # page had nothing new -> end of results
                break
        if not offers:
            raise StrategyUnavailable(self.name, "no cards parsed")
        return offers


class _GuestSearchPageStrategy:
    name = "guest_search_page"
    url = f"{_HOST}/jobs/search"

    def fetch(self, ctx: JobSearchContext, *, limit: int,
              deadline: float = 0.0) -> list[NormalizedOffer]:
        try:
            result = http.fetch(
                self.url, params=_build_params(ctx, 0),
                allowed_hosts=("linkedin.com",), accept_language=ctx.language,
            )
        except http.AccessDenied as exc:
            raise StrategyUnavailable(self.name, exc.reason) from exc
        except http.HttpError as exc:
            raise StrategyUnavailable(self.name, str(exc)) from exc

        if _looks_like_authwall(result.url, result.text):
            raise ProviderAuthRequired("linkedin authwall / sign-in page")
        offers = _parse_cards(result.text)
        # merge the JSON-LD ItemList — it carries dates / descriptions the
        # cards lack, and on some responses it is the *only* list source.
        by_id = {o.source_job_id: o for o in offers}
        for jld in _parse_jsonld_itemlist(result.text):
            existing = by_id.get(jld.source_job_id)
            if existing is None:
                by_id[jld.source_job_id] = jld
            else:
                existing.description = existing.description or jld.description
                existing.posted_at = existing.posted_at or jld.posted_at
                existing.expires_at = existing.expires_at or jld.expires_at
        offers = list(by_id.values())
        if not offers:
            raise StrategyUnavailable(self.name, "no cards or JSON-LD")
        return offers[:limit]


# ---------------------------------------------------------------------------
# ENRICH strategies (best-effort, never raise past the provider)
# ---------------------------------------------------------------------------

class _GuestFragmentStrategy:
    name = "guest_fragment"

    @staticmethod
    def url(job_id: str) -> str:
        return f"{_HOST}/jobs-guest/jobs/api/jobPosting/{job_id}"

    def enrich(self, offer: NormalizedOffer) -> None:
        result = http.fetch(self.url(offer.source_job_id),
                            allowed_hosts=("linkedin.com",))
        soup = BeautifulSoup(result.text, "html.parser")
        desc_el = soup.select_one("div.description__text, div.show-more-less-html__markup")
        if desc_el and not offer.description:
            offer.description = html_to_text(str(desc_el))
        for li in soup.select("ul.description__job-criteria-list li"):
            label = clean_text(li.select_one("h3, .description__job-criteria-subheader") and
                               li.select_one("h3, .description__job-criteria-subheader").get_text() or "")
            value = clean_text(li.select_one("span, .description__job-criteria-text") and
                               li.select_one("span, .description__job-criteria-text").get_text() or "")
            if not label or not value:
                continue
            low = label.lower()
            if "seniority" in low and not offer.experience_level:
                offer.experience_level = guess_experience_level(value)
            elif "employment type" in low and not offer.employment_type:
                offer.employment_type = value
                if guess_job_type(value) == "internship":
                    offer.job_type = "internship"
            elif "industries" in low or "job function" in low:
                offer.metadata.setdefault("criteria", {})[label] = value
                offer.skills = list(dict.fromkeys(offer.skills + [value]))[:20]
        apply_el = soup.select_one(
            "a[data-tracking-control-name*='apply'], a.apply-button, code#applyUrl"
        )
        apply_url = None
        if apply_el:
            apply_url = apply_el.get("href") or (apply_el.get_text() or "").strip() or None
        if apply_url and apply_url.startswith("http"):
            offer.metadata["apply_url"] = apply_url
        if not offer.job_type or offer.job_type == "job":
            offer.job_type = guess_job_type(offer.title, offer.description)
        if not offer.remote_type:
            offer.remote_type = guess_remote_type(offer.title, offer.description, offer.location)


class _JobViewJsonLdStrategy:
    name = "job_view_jsonld"

    @staticmethod
    def url(job_id: str) -> str:
        return f"{_HOST}/jobs/view/{job_id}"

    def enrich(self, offer: NormalizedOffer) -> None:
        result = http.fetch(self.url(offer.source_job_id),
                            allowed_hosts=("linkedin.com",))
        posting = first_jobposting(result.text)
        if not posting:
            return
        if not offer.posted_at:
            offer.posted_at = parse_posted_date(posting.get("datePosted"))
        if not offer.expires_at:
            offer.expires_at = to_utc(posting.get("validThrough"))
        if not offer.description:
            offer.description = html_to_text(posting.get("description"))
        if not offer.employment_type and posting.get("employmentType"):
            et = posting["employmentType"]
            offer.employment_type = et[0] if isinstance(et, list) else str(et)
        salary = posting.get("baseSalary") or {}
        value = (salary.get("value") or {}) if isinstance(salary, dict) else {}
        if value and not offer.salary_min:
            offer.salary_min = _num(value.get("minValue"))
            offer.salary_max = _num(value.get("maxValue"))
            offer.salary_currency = salary.get("currency")
            offer.salary_period = str(value.get("unitText") or "").lower() or None
        elif not offer.salary_min and isinstance(salary, str):
            offer.salary_min, offer.salary_max, offer.salary_currency = parse_salary(salary)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class LinkedInProvider(JobProvider):
    name = "linkedin"
    allowed_hosts = ("linkedin.com",)
    application_priority = 100

    _LIST_STRATEGIES = (_GuestSearchApiStrategy(), _GuestSearchPageStrategy())
    _ENRICH_STRATEGIES = (_GuestFragmentStrategy(), _JobViewJsonLdStrategy())

    def _search(self, ctx: JobSearchContext, *, limit: int, deadline: float):
        offers: list[NormalizedOffer] = []
        used: str | None = None
        errors: list[str] = []
        for strategy in self._LIST_STRATEGIES:
            try:
                offers = strategy.fetch(ctx, limit=limit, deadline=deadline)
            except StrategyUnavailable as exc:
                errors.append(f"{exc.strategy}:{exc.reason}")
                logger.info("[linkedin] %s unavailable (%s)", exc.strategy, exc.reason)
                continue
            if offers:
                used = strategy.name
                break
        if not offers:
            joined = "; ".join(errors)
            if any(code in joined for code in ("403", "451", "999")):
                raise ProviderBlocked(f"linkedin blocked ({joined})")
            raise ProviderTemporarilyUnavailable(f"linkedin list strategies failed ({joined})")

        offers = [o for o in offers if passes_context_filters(o, ctx)]

        # One extra guest-API pass biased toward the top preferred company,
        # when the first pass returned few results. Best-effort, <= 1 request.
        if ctx.preferred_companies and len(offers) < limit and (
            not deadline or time.monotonic() < deadline
        ):
            company = ctx.preferred_companies[0]
            kw = f"{ctx.query} {company}".strip() if ctx.query else company
            try:
                extra = self._LIST_STRATEGIES[0].fetch(
                    ctx, limit=limit, deadline=deadline, keywords=kw,
                )
                have = {o.source_job_id for o in offers}
                for o in extra:
                    if o.source_job_id not in have and passes_context_filters(o, ctx):
                        offers.append(o)
                        have.add(o.source_job_id)
            except (StrategyUnavailable, http.HttpError):
                pass  # the primary results already stand

        # enrich the offers that lack a description — the most relevant first,
        # bounded by LINKEDIN_ENRICH_MAX and the deadline (guest enrichment is
        # throttled to ~1 req/host/s).
        need = rank_enrichment_targets(
            [o for o in offers if not o.description], ctx
        )[: settings.LINKEDIN_ENRICH_MAX]
        self._enrich(need, deadline=deadline)
        return offers, used

    # ------------------------------------------------------------------

    def _enrich(self, offers: list[NormalizedOffer], *, deadline: float = 0.0) -> None:
        if not offers:
            return

        def _one(offer: NormalizedOffer) -> None:
            if deadline and time.monotonic() >= deadline:
                return
            for strategy in self._ENRICH_STRATEGIES:
                try:
                    strategy.enrich(offer)
                    if offer.description:
                        return
                except http.AccessDenied:
                    return  # blocked -> stop enriching this offer, keep the card
                except Exception as exc:  # noqa: BLE001 — enrichment is best-effort
                    logger.debug("[linkedin] enrich %s via %s failed: %s",
                                 offer.source_job_id, strategy.name, exc)

        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="li-enrich") as pool:
            futures = [pool.submit(_one, o) for o in offers]
            for _ in as_completed(futures):
                pass

    # ------------------------------------------------------------------

    def revalidate(self, offer) -> RevalidationResult:
        url = f"{_HOST}/jobs-guest/jobs/api/jobPosting/{offer.source_job_id}"
        try:
            result = http.fetch(url, allowed_hosts=self.allowed_hosts)
        except http.AccessDenied:
            return RevalidationResult("unknown")
        except http.HttpError:
            return RevalidationResult("unknown")
        if result.status_code in (404, 410):
            return RevalidationResult("gone")
        low = result.text.lower()
        if ("no longer accepting applications" in low
                or "this job is no longer available" in low
                or len(result.text.strip()) < 200):
            return RevalidationResult("gone")
        return RevalidationResult("alive")

    def application_method(self, offer) -> ApplicationMethod:
        external = (offer.metadata_json or {}).get("apply_url") if hasattr(offer, "metadata_json") \
            else (getattr(offer, "metadata", {}) or {}).get("apply_url")
        if external and str(external).startswith("http") and "linkedin.com" not in str(external):
            return ApplicationMethod("external_url", external)
        # the public /jobs/view page — the user applies there (login may be needed)
        return ApplicationMethod("platform_login_required", offer.source_url)


def _num(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
