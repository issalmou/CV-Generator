"""We Work Remotely — public RSS feeds (no key).

Category feeds under ``https://weworkremotely.com/categories/<cat>.rss`` and
the site-wide ``https://weworkremotely.com/remote-jobs.rss``. Each
``<item>`` title is ``"Company: Role"``.
"""

from __future__ import annotations

import re

from schemas.jobs import JobSearchContext, NormalizedOffer
from services.providers import http
from services.providers.base import JobProvider
from services.providers.parsing import (
    clean_text, guess_experience_level, guess_job_type, html_to_text,
    keyword_match, normalize_url, passes_context_filters, parse_rss,
    search_terms, split_city_country, parse_posted_date,
)

_FEEDS = [
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    "https://weworkremotely.com/categories/remote-design-jobs.rss",
    "https://weworkremotely.com/categories/remote-data-jobs.rss",
    "https://weworkremotely.com/remote-jobs.rss",
]
_TITLE_SPLIT = re.compile(r"\s*[:–—-]\s*")


class WeWorkRemotelyProvider(JobProvider):
    name = "weworkremotely"
    allowed_hosts = ("weworkremotely.com",)
    application_priority = 35

    def _search(self, ctx: JobSearchContext, *, limit: int, deadline: float):
        terms = search_terms(ctx)
        seen: set[str] = set()
        offers: list[NormalizedOffer] = []
        used_any = False
        for feed in _FEEDS:
            try:
                result = http.fetch(feed, allowed_hosts=self.allowed_hosts,
                                    accept_language=ctx.language)
            except http.AccessDenied:
                raise
            except http.HttpError:
                continue
            used_any = True
            for item in parse_rss(result.text):
                offer = self._normalize(item)
                if offer is None or offer.source_job_id in seen:
                    continue
                if not passes_context_filters(offer, ctx):
                    continue
                blob = " ".join([offer.title, offer.company or "", offer.description or ""])
                if terms and not keyword_match(blob, terms):
                    continue
                seen.add(offer.source_job_id)
                offers.append(offer)
                if len(offers) >= limit:
                    return offers, "wwr_rss"
        if not used_any:
            raise http.HttpError("no WeWorkRemotely feed reachable")
        return offers, "wwr_rss"

    @staticmethod
    def _normalize(item: dict) -> NormalizedOffer | None:
        link = item.get("link") or item.get("guid")
        raw_title = item.get("title")
        if not link or not raw_title:
            return None
        company, _, role = raw_title.partition(":")
        if role.strip():
            company, title = company.strip(), role.strip()
        else:
            parts = _TITLE_SPLIT.split(raw_title, maxsplit=1)
            company, title = (parts[0].strip(), parts[-1].strip()) if len(parts) == 2 else (None, raw_title.strip())
        description = html_to_text(item.get("description") or item.get("content"))
        region = clean_text(item.get("region"))
        city, country = split_city_country(region)
        category = clean_text(item.get("category"))
        return NormalizedOffer(
            source="weworkremotely",
            source_job_id=normalize_url(link).rsplit("/", 1)[-1] or normalize_url(link),
            source_url=link,
            title=title or "Untitled",
            company=company or None,
            location=region,
            city=city,
            country=country,
            description=description,
            job_type=guess_job_type(title, description, category),
            remote_type="remote",
            experience_level=guess_experience_level(title, description),
            skills=[category] if category else [],
            language=None,
            posted_at=parse_posted_date(item.get("pubDate")),
            metadata={"category": category, "region": region},
        )
