"""Himalayas — public remote-jobs JSON API (best-effort).

``GET https://himalayas.app/jobs/api?limit=&offset=`` -> ``{"jobs": [...]}``.
May rate-limit / block server IPs -> degrades to ``unavailable``.
"""

from __future__ import annotations

from config import settings
from schemas.jobs import JobSearchContext, NormalizedOffer
from services.providers import http
from services.providers.base import JobProvider
from services.providers.parsing import (
    clean_text, guess_experience_level, guess_job_type, html_to_text,
    keyword_match, passes_context_filters, search_terms, split_city_country, parse_posted_date,
)

_BASE = "https://himalayas.app/jobs/api"


class HimalayasProvider(JobProvider):
    name = "himalayas"
    allowed_hosts = ("himalayas.app",)
    application_priority = 30

    def _search(self, ctx: JobSearchContext, *, limit: int, deadline: float):
        terms = search_terms(ctx)
        offers: list[NormalizedOffer] = []
        per_page = 20
        for page in range(settings.JOB_PROVIDER_MAX_PAGES):
            payload = http.fetch_json(
                _BASE, params={"limit": per_page, "offset": page * per_page},
                allowed_hosts=self.allowed_hosts, accept_language=ctx.language,
            )
            rows = payload.get("jobs") if isinstance(payload, dict) else None
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
                    return offers, "himalayas_api"
        return offers, "himalayas_api"

    @staticmethod
    def _normalize(row: dict) -> NormalizedOffer | None:
        job_id = row.get("guid") or row.get("id")
        url = row.get("applicationLink") or row.get("url")
        title = row.get("title")
        if not job_id or not url or not title:
            return None
        description = html_to_text(row.get("description") or row.get("excerpt"))
        locations = row.get("locationRestrictions") or row.get("locations") or []
        if isinstance(locations, str):
            locations = [locations]
        loc = ", ".join(str(x) for x in locations if x) or None
        city, country = split_city_country(loc)
        categories = row.get("categories") or []
        if isinstance(categories, str):
            categories = [categories]
        return NormalizedOffer(
            source="himalayas",
            source_job_id=str(job_id),
            source_url=url,
            title=clean_text(title) or "Untitled",
            company=clean_text(row.get("companyName") or row.get("company")),
            location=clean_text(loc),
            city=city,
            country=country,
            description=description,
            employment_type=clean_text(row.get("employmentType")),
            job_type=guess_job_type(title, description),
            remote_type="remote",
            experience_level=_map_seniority(row.get("seniority")) or guess_experience_level(title, description),
            skills=[str(c) for c in categories if c][:20],
            posted_at=parse_posted_date(row.get("pubDate") or row.get("publishedDate")),
            metadata={"categories": categories, "seniority": row.get("seniority")},
        )


def _map_seniority(value) -> str | None:
    if not value:
        return None
    items = value if isinstance(value, list) else [value]
    low = " ".join(str(x).lower() for x in items)
    for key, level in (("intern", "student"), ("entry", "entry"), ("junior", "junior"),
                       ("mid", "mid"), ("senior", "senior"), ("lead", "lead"),
                       ("principal", "lead"), ("staff", "lead")):
        if key in low:
            return level
    return None
