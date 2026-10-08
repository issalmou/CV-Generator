"""Indeed — best-effort public HTML + JSON-LD.

Indeed sits behind Cloudflare / anti-bot and blocks most automated access,
so this provider **usually degrades to ``unavailable``** — by design. We do
NOT try to defeat the protection (no CAPTCHA solving, no stealth, no proxy
rotation): a 403 / challenge -> ``AccessDenied`` -> ``unavailable``.

Strategies (in order):
  1. SERP page ``/jobs?q=&l=&start=`` -> embedded JSON-LD ``ItemList`` /
     ``mosaic`` provider data / job cards.
  2. per-job ``/viewjob?jk=<id>`` -> JSON-LD ``JobPosting`` (best-effort enrich).
"""

from __future__ import annotations

import re

from config import settings
from schemas.jobs import JobSearchContext, NormalizedOffer
from services.providers import http
from services.providers.base import ApplicationMethod, JobProvider, RevalidationResult
from services.providers.parsing import (
    clean_text, first_jobposting, guess_experience_level, guess_job_type,
    guess_remote_type, html_to_text, json_ld_blocks, parse_salary,
    passes_context_filters, split_city_country, parse_posted_date, to_utc,
)

_HOSTS = ("indeed.com",)
_JK_RE = re.compile(r"jk=([0-9a-f]{10,20})", re.I)


class IndeedProvider(JobProvider):
    name = "indeed"
    allowed_hosts = _HOSTS
    application_priority = 90

    def _domain(self, ctx: JobSearchContext) -> str:
        cc = (ctx.country or "").strip().lower()
        if cc in ("france", "fr"):
            return "https://fr.indeed.com"
        if cc in ("united kingdom", "uk", "gb"):
            return "https://uk.indeed.com"
        return "https://www.indeed.com"

    def _search(self, ctx: JobSearchContext, *, limit: int, deadline: float):
        base = self._domain(ctx)
        offers: list[NormalizedOffer] = []
        seen: set[str] = set()
        for page in range(settings.JOB_PROVIDER_MAX_PAGES):
            params = {"start": page * 10}
            if ctx.query:
                params["q"] = ctx.query
            where = ctx.city or ctx.location
            if where:
                params["l"] = where
            result = http.fetch(f"{base}/jobs", params=params,
                                allowed_hosts=self.allowed_hosts,
                                accept_language=ctx.language)
            page_offers = self._parse_serp(result.text, base)
            if not page_offers:
                break
            for offer in page_offers:
                if offer.source_job_id in seen or not passes_context_filters(offer, ctx):
                    continue
                seen.add(offer.source_job_id)
                offers.append(offer)
                if len(offers) >= limit:
                    return offers, "indeed_serp"
        return offers, "indeed_serp"

    # ------------------------------------------------------------------

    def _parse_serp(self, html: str, base: str) -> list[NormalizedOffer]:
        out: list[NormalizedOffer] = []
        for block in json_ld_blocks(html):
            items = block.get("itemListElement") if isinstance(block, dict) else None
            for element in items or []:
                node = element.get("item", element) if isinstance(element, dict) else {}
                offer = self._from_jsonld(node, base)
                if offer:
                    out.append(offer)
        if out:
            return out
        # fallback: anchor scan for job keys + titles
        for m in re.finditer(
            r'<a[^>]+href="(/rc/clk\?jk=|/viewjob\?jk=)([0-9a-f]{10,20})"[^>]*>(.*?)</a>',
            html, re.I | re.S,
        ):
            jk = m.group(2)
            title = html_to_text(m.group(3), max_len=200) or "Job"
            out.append(NormalizedOffer(
                source="indeed",
                source_job_id=jk,
                source_url=f"{base}/viewjob?jk={jk}",
                title=title,
                job_type=guess_job_type(title),
            ))
        return out

    @staticmethod
    def _from_jsonld(node: dict, base: str) -> NormalizedOffer | None:
        if not isinstance(node, dict):
            return None
        url = node.get("url") or node.get("@id") or ""
        jk_match = _JK_RE.search(url)
        job_id = jk_match.group(1) if jk_match else (node.get("identifier", {}) or {}).get("value")
        title = node.get("title") or node.get("name")
        if not job_id or not title:
            return None
        org = node.get("hiringOrganization") or {}
        loc = node.get("jobLocation") or {}
        if isinstance(loc, list):
            loc = loc[0] if loc else {}
        address = (loc.get("address") or {}) if isinstance(loc, dict) else {}
        location = ", ".join(str(x) for x in [
            address.get("addressLocality"), address.get("addressRegion"),
            address.get("addressCountry"),
        ] if x) or None
        city, country = split_city_country(location)
        description = html_to_text(node.get("description"))
        salary = node.get("baseSalary") or {}
        sal_val = (salary.get("value") or {}) if isinstance(salary, dict) else {}
        return NormalizedOffer(
            source="indeed",
            source_job_id=str(job_id),
            source_url=url if url.startswith("http") else f"{base}/viewjob?jk={job_id}",
            title=clean_text(title) or "Job",
            company=clean_text(org.get("name")),
            company_url=clean_text(org.get("sameAs"), max_len=1024),
            location=clean_text(location),
            city=city,
            country=country,
            description=description,
            employment_type=_emp_type(node.get("employmentType")),
            job_type=guess_job_type(title, description, _emp_type(node.get("employmentType"))),
            remote_type=guess_remote_type(title, description, location),
            salary_min=_to_float(sal_val.get("minValue")),
            salary_max=_to_float(sal_val.get("maxValue")),
            salary_currency=salary.get("currency") if isinstance(salary, dict) else None,
            salary_period=str(sal_val.get("unitText") or "").lower() or None,
            experience_level=guess_experience_level(title, description),
            posted_at=parse_posted_date(node.get("datePosted")),
            expires_at=to_utc(node.get("validThrough")),
        )

    # ------------------------------------------------------------------

    def revalidate(self, offer) -> RevalidationResult:
        try:
            result = http.fetch(offer.source_url, allowed_hosts=self.allowed_hosts)
        except http.AccessDenied:
            return RevalidationResult("unknown")
        except http.HttpError:
            return RevalidationResult("unknown")
        if result.status_code == 404:
            return RevalidationResult("gone")
        posting = first_jobposting(result.text)
        if posting is None and "no longer available" in result.text.lower():
            return RevalidationResult("gone")
        expires = to_utc(posting.get("validThrough")) if posting else None
        return RevalidationResult("alive", expires_at=expires)

    def application_method(self, offer) -> ApplicationMethod:
        return ApplicationMethod("external_url", offer.source_url)


def _emp_type(value) -> str | None:
    if not value:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
    return clean_text(str(value)) if value else None


def _to_float(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
