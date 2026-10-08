"""Greenhouse — public job-board JSON API (no key, no browser).

``GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true``
-> ``{"jobs": [...]}``. One token per company board;
``settings.greenhouse_boards_list`` holds the tokens. A board that 404s /
is blocked is skipped; only *every* board failing marks the provider.
"""

from __future__ import annotations

import html as _html

from config import settings
from services.jobs.ats_board_registry import resolved_tokens
from schemas.jobs import JobSearchContext, NormalizedOffer
from services.providers import http
from services.providers.ats_common import valid_slug
from services.providers.base import (
    JobProvider, ProviderBlocked, ProviderTemporarilyUnavailable,
)
from services.providers.parsing import (
    clean_text, guess_experience_level, guess_job_type, guess_remote_type,
    html_to_text, keyword_match, passes_context_filters, search_terms,
    split_city_country, parse_posted_date,
)

_BASE = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


class GreenhouseProvider(JobProvider):
    name = "greenhouse"
    allowed_hosts = ("greenhouse.io",)
    application_priority = 55

    @staticmethod
    def _boards() -> list[str]:
        return resolved_tokens("greenhouse", settings.greenhouse_boards_list, valid_slug)

    @property
    def enabled(self) -> bool:
        return bool(self._boards())

    def _search(self, ctx: JobSearchContext, *, limit: int, deadline: float):
        terms = search_terms(ctx) + ctx.skills
        offers: list[NormalizedOffer] = []
        boards = self._boards()
        failures = blocked = 0

        for token in boards:
            try:
                payload = http.fetch_json(
                    _BASE.format(token=token), params={"content": "true"},
                    allowed_hosts=self.allowed_hosts, accept_language=ctx.language,
                )
            except http.AccessDenied:
                failures += 1
                blocked += 1
                continue
            except http.HttpError:
                failures += 1
                continue
            rows = payload.get("jobs") if isinstance(payload, dict) else None
            for row in rows or []:
                offer = self._normalize(row, token)
                if offer is None or not passes_context_filters(offer, ctx):
                    continue
                blob = " ".join([offer.title, offer.company or "", offer.description or ""])
                if terms and not keyword_match(blob, terms):
                    continue
                offers.append(offer)
                if len(offers) >= limit:
                    return offers, "greenhouse_api"

        if boards and failures == len(boards):
            if blocked == len(boards):
                raise ProviderBlocked("all Greenhouse boards denied access")
            raise ProviderTemporarilyUnavailable("all Greenhouse boards unreachable")
        return offers, "greenhouse_api"

    @staticmethod
    def _normalize(row: dict, token: str) -> NormalizedOffer | None:
        if not isinstance(row, dict):
            return None
        job_id = row.get("id")
        url = row.get("absolute_url")
        title = row.get("title")
        if not job_id or not url or not isinstance(title, str) or not title.strip():
            return None
        content = row.get("content")
        description = html_to_text(_html.unescape(content)) if content else None
        loc = (row.get("location") or {}).get("name")
        city, country = split_city_country(loc)
        depts = [d.get("name") for d in (row.get("departments") or []) if d.get("name")]
        return NormalizedOffer(
            source="greenhouse",
            source_job_id=f"{token}:{job_id}",
            source_url=url,
            title=clean_text(title) or "Untitled",
            company=token.replace("-", " ").title(),
            company_url=f"https://boards.greenhouse.io/{token}",
            location=clean_text(loc),
            city=city,
            country=country,
            description=description,
            job_type=guess_job_type(title, description),
            remote_type=guess_remote_type(title, description, loc),
            experience_level=guess_experience_level(title, description),
            skills=[str(d) for d in depts][:10],
            posted_at=parse_posted_date(row.get("updated_at") or row.get("first_published")),
            metadata={"board": token, "departments": depts},
        )
