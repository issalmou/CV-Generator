"""Remotive — free public remote-jobs JSON API (no key).

``GET https://remotive.com/api/remote-jobs?search=&limit=`` -> ``{"jobs": [...]}``.
"""

from __future__ import annotations

from config import settings
from schemas.jobs import JobSearchContext, NormalizedOffer
from services.providers import http
from services.providers.base import JobProvider
from services.providers.parsing import (
    clean_text, guess_experience_level, guess_job_type, html_to_text,
    parse_salary, passes_context_filters, search_terms, split_city_country, parse_posted_date,
)

_BASE = "https://remotive.com/api/remote-jobs"


class RemotiveProvider(JobProvider):
    name = "remotive"
    allowed_hosts = ("remotive.com",)
    application_priority = 40

    def _search(self, ctx: JobSearchContext, *, limit: int, deadline: float):
        params: dict[str, str | int] = {"limit": min(limit, settings.JOB_PROVIDER_MAX_RESULTS)}
        terms = search_terms(ctx)
        if terms:
            params["search"] = " ".join(terms)[:80]

        payload = http.fetch_json(
            _BASE, params=params, allowed_hosts=self.allowed_hosts,
            accept_language=ctx.language,
        )
        rows = payload.get("jobs") if isinstance(payload, dict) else None
        offers: list[NormalizedOffer] = []
        for row in rows or []:
            offer = self._normalize(row)
            if offer is None or not passes_context_filters(offer, ctx):
                continue
            offers.append(offer)
            if len(offers) >= limit:
                break
        return offers, "remotive_api"

    @staticmethod
    def _normalize(row: dict) -> NormalizedOffer | None:
        job_id = row.get("id")
        if job_id is None or not row.get("title") or not row.get("url"):
            return None
        description = html_to_text(row.get("description"))
        loc = row.get("candidate_required_location")
        city, country = split_city_country(loc)
        tags = [str(t) for t in (row.get("tags") or []) if t]
        lo, hi, cur = parse_salary(row.get("salary"))
        return NormalizedOffer(
            source="remotive",
            source_job_id=str(job_id),
            source_url=row["url"],
            title=clean_text(row.get("title")) or "Untitled",
            company=clean_text(row.get("company_name")),
            location=clean_text(loc),
            city=city,
            country=country,
            description=description,
            employment_type=clean_text(row.get("job_type")),
            job_type=guess_job_type(row.get("title"), description, row.get("job_type")),
            remote_type="remote",
            salary_min=lo,
            salary_max=hi,
            salary_currency=cur,
            salary_period="year" if (lo or 0) > 1000 else None,
            experience_level=guess_experience_level(row.get("title"), description),
            skills=tags[:20],
            posted_at=parse_posted_date(row.get("publication_date")),
            metadata={"category": row.get("category"), "tags": tags},
        )
