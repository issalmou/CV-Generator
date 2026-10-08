"""
Cross-provider (and within-provider) deduplication.

Signals, in decreasing confidence:
  1. same ``(source, source_job_id)``          — exact same listing
  2. same normalised URL                        — same listing, any source
  3. same ``slug(company)|slug(title)|slug(city|country)``  — same job re-posted
  4. same company + title similarity >= threshold + same city (or both remote)

**Jobs in different cities are never merged**, even with identical
company + title — they are genuinely different openings.

Merging keeps the highest-priority source's row and unions the rest:
``also_seen_on`` URLs, skills, the longest description, the earliest
posted date.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from config import settings
from schemas.jobs import NormalizedOffer
from services.providers.parsing import normalize_url

_slug_re = re.compile(r"[^a-z0-9]+")
_PROVIDER_PRIORITY: dict[str, int] = {}


def _priority(source: str) -> int:
    if not _PROVIDER_PRIORITY:
        from services.providers.registry import _PROVIDER_CLASSES
        _PROVIDER_PRIORITY.update(
            {name: cls.application_priority for name, cls in _PROVIDER_CLASSES.items()}
        )
    return _PROVIDER_PRIORITY.get(source, 0)


def _slug(value: str | None) -> str:
    return _slug_re.sub("-", (value or "").lower()).strip("-")


def _enum(value) -> str | None:
    return getattr(value, "value", value) if value is not None else None


def _fuzzy_mergeable(a: NormalizedOffer, b: NormalizedOffer) -> bool:
    """Guard the title-similarity merge: two postings that are the *same*
    company+city but a different job/internship split or a different, both-set
    experience level are separate openings — never merge them on title alone."""
    at, bt = _enum(a.job_type), _enum(b.job_type)
    if at in ("job", "internship") and bt in ("job", "internship") and at != bt:
        return False
    ae, be = _enum(a.experience_level), _enum(b.experience_level)
    if ae and be and ae != be:
        return False
    return True


def _city_key(offer: NormalizedOffer) -> str:
    if offer.remote_type and getattr(offer.remote_type, "value", offer.remote_type) == "remote":
        return "@remote"
    return _slug(offer.city or offer.country or offer.location)


def _strong_keys(offer: NormalizedOffer) -> list[str]:
    """Same-listing identity — a match here always merges."""
    return [
        f"id::{offer.source.lower()}::{offer.source_job_id.lower()}",
        f"url::{normalize_url(offer.source_url)}",
    ]


def _cts_key(offer: NormalizedOffer) -> str:
    """Same company + title + city — 'the same job, re-posted'. A match here
    still has to pass :func:`_fuzzy_mergeable` (job vs internship, level)."""
    return f"cts::{_slug(offer.company)}|{_slug(offer.title)}|{_city_key(offer)}"


def _merge(primary: NormalizedOffer, other: NormalizedOffer) -> NormalizedOffer:
    if _priority(other.source) > _priority(primary.source):
        primary, other = other, primary

    urls: set[str] = set(primary.also_seen_on) | set(other.also_seen_on)
    urls.add(normalize_url(other.source_url))
    urls.discard(normalize_url(primary.source_url))
    urls.discard("")
    primary.also_seen_on = sorted(urls)

    primary.skills = list(dict.fromkeys([*primary.skills, *other.skills]))[:25]

    if (other.description or "") and len(other.description or "") > len(primary.description or ""):
        primary.description = other.description

    for field in ("company", "company_url", "location", "city", "country",
                  "salary_min", "salary_max", "salary_currency", "salary_period",
                  "experience_level", "remote_type", "employment_type",
                  "internship_duration_months", "internship_start_date"):
        if getattr(primary, field) in (None, "") and getattr(other, field) not in (None, ""):
            setattr(primary, field, getattr(other, field))

    if other.posted_at and (primary.posted_at is None or other.posted_at < primary.posted_at):
        primary.posted_at = other.posted_at
    if other.expires_at and (primary.expires_at is None or other.expires_at > primary.expires_at):
        primary.expires_at = other.expires_at

    merged_sources = set(primary.metadata.get("merged_sources", []))
    merged_sources.update({primary.source, other.source})
    primary.metadata["merged_sources"] = sorted(merged_sources)
    return primary


def deduplicate(offers: list[NormalizedOffer]) -> list[NormalizedOffer]:
    """O(n·k) — the fuzzy-title pass only ever compares within the bucket of
    groups that share a company + city (k, typically 1–3), never the whole
    result set."""
    groups: list[NormalizedOffer] = []
    group_slug: list[str] = []                      # cached _slug(title) per group
    strong_to_group: dict[str, int] = {}           # id:: / url::  -> group idx
    cts_to_groups: dict[str, list[int]] = {}        # cts::         -> candidate group idxs
    cc_to_groups: dict[tuple[str, str], list[int]] = {}   # (company, city) -> group idxs
    threshold = settings.JOB_DEDUP_TITLE_RATIO

    for offer in offers:
        strong = _strong_keys(offer)
        cts = _cts_key(offer)
        comp, city = _slug(offer.company), _city_key(offer)
        title_slug = _slug(offer.title)

        idx = next((strong_to_group[k] for k in strong if k in strong_to_group), None)

        # same company/title/city -> merge only into a *compatible* group
        if idx is None:
            for cand in cts_to_groups.get(cts, ()):
                if _fuzzy_mergeable(groups[cand], offer):
                    idx = cand
                    break

        # last resort: fuzzy title similarity — only within the (company, city) bucket
        if idx is None and comp:
            matcher = SequenceMatcher(None, "", title_slug)
            for cand in cc_to_groups.get((comp, city), ()):
                if not _fuzzy_mergeable(groups[cand], offer):
                    continue
                matcher.set_seq1(group_slug[cand])
                if matcher.ratio() >= threshold:
                    idx = cand
                    break

        if idx is None:
            groups.append(offer)
            group_slug.append(title_slug)
            idx = len(groups) - 1
        else:
            groups[idx] = _merge(groups[idx], offer)
            group_slug[idx] = _slug(groups[idx].title)

        merged = groups[idx]
        for k in strong + _strong_keys(merged):
            strong_to_group[k] = idx
        for k in {cts, _cts_key(merged)}:
            b = cts_to_groups.setdefault(k, [])
            if idx not in b:
                b.append(idx)
        for cc in {(comp, city), (_slug(merged.company), _city_key(merged))}:
            if cc[0]:
                b = cc_to_groups.setdefault(cc, [])
                if idx not in b:
                    b.append(idx)

    return groups
