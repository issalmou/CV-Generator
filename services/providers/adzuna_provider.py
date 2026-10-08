"""Adzuna — structured jobs API (optional, free tier).

Requires ``ADZUNA_APP_ID`` + ``ADZUNA_APP_KEY``. Without them the provider
reports ``enabled = False`` and the registry never calls it (the whole
system stays fully functional without Adzuna).

``GET https://api.adzuna.com/v1/api/jobs/{country}/search/{page}
    ?app_id=&app_key=&what=&where=&full_time=&contract=`` -> ``{"results": [...]}``.
"""

from __future__ import annotations

from config import settings
from schemas.jobs import JobSearchContext, NormalizedOffer
from services.providers import http
from services.providers.base import JobProvider
from services.providers.parsing import (
    clean_text, guess_experience_level, guess_job_type, guess_remote_type,
    html_to_text, passes_context_filters, split_city_country, parse_posted_date,
)

_HOST = "https://api.adzuna.com"
_DEFAULT_COUNTRY = "gb"
_COUNTRY_CODES = {
    "united kingdom": "gb", "uk": "gb", "great britain": "gb",
    "united states": "us", "usa": "us", "us": "us",
    "france": "fr", "germany": "de", "canada": "ca", "australia": "au",
    "spain": "es", "italy": "it", "netherlands": "nl", "poland": "pl",
    "austria": "at", "belgium": "be", "brazil": "br", "switzerland": "ch",
    "india": "in", "mexico": "mx", "new zealand": "nz", "singapore": "sg",
    "south africa": "za",
}


class AdzunaProvider(JobProvider):
    name = "adzuna"
    allowed_hosts = ("api.adzuna.com",)
    application_priority = 45

    @property
    def enabled(self) -> bool:
        return settings.adzuna_configured

    def _search(self, ctx: JobSearchContext, *, limit: int, deadline: float):
        country = self._country(ctx)
        offers: list[NormalizedOffer] = []
        for page in range(1, settings.JOB_PROVIDER_MAX_PAGES + 1):
            params = {
                "app_id": settings.ADZUNA_APP_ID,
                "app_key": settings.ADZUNA_APP_KEY,
                "results_per_page": 20,
                "content-type": "application/json",
            }
            if ctx.query:
                params["what"] = ctx.query[:120]
            if ctx.city or ctx.location:
                params["where"] = (ctx.city or ctx.location)[:120]
            if ctx.job_type and ctx.job_type.value == "internship":
                params["what_or"] = "intern internship stage"
            payload = http.fetch_json(
                f"{_HOST}/v1/api/jobs/{country}/search/{page}",
                params=params, allowed_hosts=self.allowed_hosts,
                accept_language=ctx.language,
            )
            rows = payload.get("results") if isinstance(payload, dict) else None
            if not rows:
                break
            for row in rows:
                offer = self._normalize(row, country)
                if offer is None or not passes_context_filters(offer, ctx):
                    continue
                offers.append(offer)
                if len(offers) >= limit:
                    return offers, "adzuna_api"
        return offers, "adzuna_api"

    @staticmethod
    def _country(ctx: JobSearchContext) -> str:
        for hint in (ctx.country, ctx.location, ctx.city):
            if hint and hint.strip().lower() in _COUNTRY_CODES:
                return _COUNTRY_CODES[hint.strip().lower()]
        return _DEFAULT_COUNTRY

    @staticmethod
    def _normalize(row: dict, country: str) -> NormalizedOffer | None:
        job_id = row.get("id")
        url = row.get("redirect_url")
        title = row.get("title")
        if not job_id or not url or not title:
            return None
        description = html_to_text(row.get("description"))
        company = (row.get("company") or {}).get("display_name")
        loc = (row.get("location") or {}).get("display_name")
        city, ctry = split_city_country(loc)
        contract_time = row.get("contract_time")           # full_time | part_time
        contract_type = row.get("contract_type")           # permanent | contract
        return NormalizedOffer(
            source="adzuna",
            source_job_id=str(job_id),
            source_url=url,
            title=clean_text(title) or "Untitled",
            company=clean_text(company),
            location=clean_text(loc),
            city=city,
            country=ctry or country.upper(),
            description=description,
            employment_type=clean_text(contract_time or contract_type),
            job_type=guess_job_type(title, description),
            remote_type=guess_remote_type(title, description, loc),
            salary_min=_num(row.get("salary_min")),
            salary_max=_num(row.get("salary_max")),
            salary_currency="GBP" if country == "gb" else None,
            salary_period="year" if _num(row.get("salary_min")) else None,
            experience_level=guess_experience_level(title, description),
            skills=[c for c in [(row.get("category") or {}).get("label")] if c],
            posted_at=parse_posted_date(row.get("created")),
            metadata={"category": (row.get("category") or {}).get("label")},
        )


def _num(value) -> float | None:
    try:
        return float(value) if value else None
    except (TypeError, ValueError):
        return None
