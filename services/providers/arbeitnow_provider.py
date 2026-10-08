"""Arbeitnow — free public job-board JSON API (no key).

``GET https://www.arbeitnow.com/api/job-board-api?page=N`` -> ``{"data": [...]}``.
"""

from __future__ import annotations

from config import settings
from schemas.jobs import JobSearchContext, NormalizedOffer
from services.providers import http
from services.providers.base import JobProvider
from services.providers.parsing import (
    clean_text, guess_experience_level, guess_job_type, guess_remote_type,
    html_to_text, keyword_match, passes_context_filters, search_terms,
    split_city_country, parse_posted_date,
)

_BASE = "https://www.arbeitnow.com/api/job-board-api"


class ArbeitnowProvider(JobProvider):
    name = "arbeitnow"
    allowed_hosts = ("arbeitnow.com",)
    application_priority = 40

    def _search(self, ctx: JobSearchContext, *, limit: int, deadline: float):
        terms = search_terms(ctx)
        offers: list[NormalizedOffer] = []

        for page in range(1, settings.JOB_PROVIDER_MAX_PAGES + 1):
            payload = http.fetch_json(
                _BASE, params={"page": page},
                allowed_hosts=self.allowed_hosts, accept_language=ctx.language,
            )
            rows = payload.get("data") if isinstance(payload, dict) else None
            if not rows:
                break
            for row in rows:
                offer = self._normalize(row)
                if offer is None or not passes_context_filters(offer, ctx):
                    continue
                blob = " ".join([offer.title, offer.company or "", offer.description or ""])
                if terms and not keyword_match(blob, terms):
                    continue
                offers.append(offer)
                if len(offers) >= limit:
                    return offers, "arbeitnow_api"
        return offers, "arbeitnow_api"

    @staticmethod
    def _normalize(row: dict) -> NormalizedOffer | None:
        slug = row.get("slug") or row.get("url")
        if not slug or not row.get("title"):
            return None
        url = row.get("url") or f"https://www.arbeitnow.com/view/{slug}"
        description = html_to_text(row.get("description"))
        city, country = split_city_country(row.get("location"))
        tags = [str(t) for t in (row.get("tags") or []) if t]
        job_types = [str(t) for t in (row.get("job_types") or []) if t]
        remote = "remote" if row.get("remote") else guess_remote_type(row.get("location"))
        return NormalizedOffer(
            source="arbeitnow",
            source_job_id=str(slug),
            source_url=url,
            title=clean_text(row.get("title")) or "Untitled",
            company=clean_text(row.get("company_name")),
            location=clean_text(row.get("location")),
            city=city,
            country=country,
            description=description,
            employment_type=job_types[0] if job_types else None,
            job_type=guess_job_type(row.get("title"), description, *job_types),
            remote_type=remote,
            experience_level=guess_experience_level(row.get("title"), description),
            skills=tags[:20],
            posted_at=parse_posted_date(row.get("created_at")),
            metadata={"tags": tags, "job_types": job_types},
        )
