"""RemoteOK — public JSON feed (best-effort).

``GET https://remoteok.com/api`` -> a JSON array; element 0 is a legal
notice (skipped). RemoteOK gates non-browser User-Agents, so this provider
frequently degrades to ``unavailable`` — that is expected and handled.
"""

from __future__ import annotations

from schemas.jobs import JobSearchContext, NormalizedOffer
from services.providers import http
from services.providers.base import JobProvider
from services.providers.parsing import (
    clean_text, guess_experience_level, guess_job_type, html_to_text,
    keyword_match, passes_context_filters, search_terms, split_city_country, parse_posted_date,
)

_BASE = "https://remoteok.com/api"


class RemoteOkProvider(JobProvider):
    name = "remoteok"
    allowed_hosts = ("remoteok.com", "remoteok.io")
    application_priority = 30

    def _search(self, ctx: JobSearchContext, *, limit: int, deadline: float):
        payload = http.fetch_json(_BASE, allowed_hosts=self.allowed_hosts,
                                  accept_language=ctx.language)
        rows = payload if isinstance(payload, list) else []
        terms = search_terms(ctx)
        offers: list[NormalizedOffer] = []
        for row in rows:
            if not isinstance(row, dict) or row.get("legal") or not row.get("id"):
                continue
            offer = self._normalize(row)
            if offer is None or not passes_context_filters(offer, ctx):
                continue
            blob = " ".join([offer.title, offer.company or "", offer.description or "",
                             " ".join(offer.skills)])
            if terms and not keyword_match(blob, terms):
                continue
            offers.append(offer)
            if len(offers) >= limit:
                break
        return offers, "remoteok_api"

    @staticmethod
    def _normalize(row: dict) -> NormalizedOffer | None:
        job_id = row.get("id")
        url = row.get("url")
        if not job_id or not row.get("position") or not url:
            return None
        description = html_to_text(row.get("description"))
        tags = [str(t) for t in (row.get("tags") or []) if t]
        city, country = split_city_country(row.get("location"))
        return NormalizedOffer(
            source="remoteok",
            source_job_id=str(job_id),
            source_url=url,
            title=clean_text(row.get("position")) or "Untitled",
            company=clean_text(row.get("company")),
            company_url=clean_text(row.get("company_url"), max_len=1024),
            location=clean_text(row.get("location")),
            city=city,
            country=country,
            description=description,
            job_type=guess_job_type(row.get("position"), description, *tags),
            remote_type="remote",
            salary_min=_num(row.get("salary_min")),
            salary_max=_num(row.get("salary_max")),
            salary_currency="USD" if _num(row.get("salary_min")) else None,
            salary_period="year" if _num(row.get("salary_min")) else None,
            experience_level=guess_experience_level(row.get("position"), description),
            skills=tags[:20],
            posted_at=parse_posted_date(row.get("date") or row.get("epoch")),
            metadata={"tags": tags},
        )


def _num(value) -> float | None:
    try:
        return float(value) if value else None
    except (TypeError, ValueError):
        return None
