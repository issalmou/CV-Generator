"""Lever — public postings JSON API (no key, no browser).

``GET https://api.lever.co/v0/postings/{token}?mode=json`` -> ``[ {...}, ... ]``.
``settings.lever_boards_list`` holds the company tokens.
"""

from __future__ import annotations

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

_BASE = "https://api.lever.co/v0/postings/{token}"
_WORKPLACE = {"remote": "remote", "on-site": "onsite", "onsite": "onsite", "hybrid": "hybrid"}


class LeverProvider(JobProvider):
    name = "lever"
    allowed_hosts = ("lever.co",)
    application_priority = 55

    @staticmethod
    def _boards() -> list[str]:
        return resolved_tokens("lever", settings.lever_boards_list, valid_slug)

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
                    _BASE.format(token=token), params={"mode": "json"},
                    allowed_hosts=self.allowed_hosts, accept_language=ctx.language,
                )
            except http.AccessDenied:
                failures += 1
                blocked += 1
                continue
            except http.HttpError:
                failures += 1
                continue
            for row in payload if isinstance(payload, list) else []:
                offer = self._normalize(row, token)
                if offer is None or not passes_context_filters(offer, ctx):
                    continue
                blob = " ".join([offer.title, offer.company or "", offer.description or ""])
                if terms and not keyword_match(blob, terms):
                    continue
                offers.append(offer)
                if len(offers) >= limit:
                    return offers, "lever_api"

        if boards and failures == len(boards):
            if blocked == len(boards):
                raise ProviderBlocked("all Lever boards denied access")
            raise ProviderTemporarilyUnavailable("all Lever boards unreachable")
        return offers, "lever_api"

    @staticmethod
    def _normalize(row: dict, token: str) -> NormalizedOffer | None:
        if not isinstance(row, dict):
            return None
        job_id = row.get("id")
        url = row.get("hostedUrl") or row.get("applyUrl")
        title = row.get("text")
        if not job_id or not url or not isinstance(title, str) or not title.strip():
            return None
        cats = row.get("categories") or {}
        loc = cats.get("location")
        city, country = split_city_country(loc)
        description = (clean_text(row.get("descriptionPlain"), max_len=8000)
                       or html_to_text(row.get("description")))
        team = cats.get("team") or cats.get("department")
        return NormalizedOffer(
            source="lever",
            source_job_id=f"{token}:{job_id}",
            source_url=url,
            title=clean_text(title) or "Untitled",
            company=token.replace("-", " ").title(),
            company_url=f"https://jobs.lever.co/{token}",
            location=clean_text(loc),
            city=city,
            country=country,
            description=description,
            employment_type=clean_text(cats.get("commitment")),
            job_type=guess_job_type(title, description, cats.get("commitment")),
            remote_type=_WORKPLACE.get(str(row.get("workplaceType", "")).lower())
                        or guess_remote_type(title, description, loc),
            experience_level=guess_experience_level(title, description),
            skills=[team] if team else [],
            posted_at=parse_posted_date(row.get("createdAt")),
            metadata={"board": token, "team": team},
        )
