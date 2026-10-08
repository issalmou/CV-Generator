"""Jobicy — free public remote-jobs JSON API v2 (no key).

``GET https://jobicy.com/api/v2/remote-jobs?count=&geo=&industry=&tag=`` -> ``{"jobs": [...]}``.
"""

from __future__ import annotations

from config import settings
from schemas.jobs import JobSearchContext, NormalizedOffer
from services.providers import http
from services.providers.base import JobProvider
from services.providers.parsing import (
    clean_text, guess_job_type, html_to_text, keyword_match,
    passes_context_filters, search_terms, split_city_country, parse_posted_date,
)

_BASE = "https://jobicy.com/api/v2/remote-jobs"
_LEVEL_MAP = {
    "any": None, "senior": "senior", "junior": "junior", "mid-level": "mid",
    "entry-level": "entry", "expert": "lead", "manager": "lead",
}


class JobicyProvider(JobProvider):
    name = "jobicy"
    allowed_hosts = ("jobicy.com",)
    application_priority = 35

    def _search(self, ctx: JobSearchContext, *, limit: int, deadline: float):
        params: dict[str, str | int] = {
            "count": min(max(limit, 10), 50),
        }
        if ctx.country:
            params["geo"] = ctx.country.lower()
        if ctx.query:
            params["tag"] = ctx.query[:40]

        payload = http.fetch_json(
            _BASE, params=params, allowed_hosts=self.allowed_hosts,
            accept_language=ctx.language,
        )
        rows = payload.get("jobs") if isinstance(payload, dict) else None
        terms = search_terms(ctx)
        offers: list[NormalizedOffer] = []
        for row in rows or []:
            offer = self._normalize(row)
            if offer is None or not passes_context_filters(offer, ctx):
                continue
            blob = " ".join([offer.title, offer.company or "", offer.description or ""])
            if terms and not keyword_match(blob, terms):
                continue
            offers.append(offer)
            if len(offers) >= limit:
                break
        return offers, "jobicy_api"

    @staticmethod
    def _normalize(row: dict) -> NormalizedOffer | None:
        job_id = row.get("id")
        url = row.get("url")
        if job_id is None or not row.get("jobTitle") or not url:
            return None
        description = html_to_text(row.get("jobDescription") or row.get("jobExcerpt"))
        geo = row.get("jobGeo")
        city, country = split_city_country(geo)
        industry = row.get("jobIndustry")
        industry = industry if isinstance(industry, list) else ([industry] if industry else [])
        jt_raw = row.get("jobType")
        jt_raw = jt_raw if isinstance(jt_raw, list) else ([jt_raw] if jt_raw else [])
        salary_min = _to_float(row.get("annualSalaryMin"))
        salary_max = _to_float(row.get("annualSalaryMax"))
        return NormalizedOffer(
            source="jobicy",
            source_job_id=str(job_id),
            source_url=url,
            title=clean_text(row.get("jobTitle")) or "Untitled",
            company=clean_text(row.get("companyName")),
            location=clean_text(geo),
            city=city,
            country=country,
            description=description,
            employment_type=clean_text(jt_raw[0]) if jt_raw else None,
            job_type=guess_job_type(row.get("jobTitle"), description, *jt_raw),
            remote_type="remote",
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=clean_text(row.get("salaryCurrency")) or ("USD" if salary_min else None),
            salary_period="year" if salary_min else None,
            experience_level=_LEVEL_MAP.get(str(row.get("jobLevel", "")).lower()),
            skills=[str(t) for t in industry if t][:20],
            posted_at=parse_posted_date(row.get("pubDate")),
            metadata={"industry": industry, "level": row.get("jobLevel")},
        )


def _to_float(value) -> float | None:
    try:
        return float(value) if value not in (None, "", "0") else None
    except (TypeError, ValueError):
        return None
