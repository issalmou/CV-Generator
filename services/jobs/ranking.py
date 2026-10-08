"""
Deterministic, LLM-free relevance scoring.

``score(offer, ctx)`` -> a float in [0, 1] combining weighted signals.
No network, no model, fully unit-testable. ``rank()`` also drops offers
whose company is on ``ctx.excluded_companies``.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from schemas.jobs import FreshnessStatus, JobSearchContext

_TOKEN_RE = re.compile(r"[a-z0-9+#.]+")

_WEIGHTS = {
    "title": 0.30,
    "skills": 0.20,
    "location": 0.15,
    "job_type": 0.12,
    "experience": 0.08,
    "language": 0.05,
    "freshness": 0.10,
}
_PREFERRED_BOOST = 0.10

_FRESHNESS_SCORE = {
    FreshnessStatus.fresh: 1.0,
    FreshnessStatus.stale: 0.55,
    FreshnessStatus.unknown: 0.4,
    FreshnessStatus.expired: 0.0,
}


def _tokens(*parts: str | None) -> set[str]:
    blob = " ".join(p for p in parts if p).lower()
    return set(_TOKEN_RE.findall(blob))


def _overlap(wanted: set[str], have: set[str]) -> float:
    if not wanted:
        return 0.0
    return len(wanted & have) / len(wanted)


def score(offer, ctx: JobSearchContext, *, freshness: FreshnessStatus | None = None) -> float:
    title_tokens = _tokens(offer.title)
    body_tokens = title_tokens | _tokens(offer.description, " ".join(offer.skills or []))

    wanted_query = _tokens(ctx.query)
    wanted_skills = _tokens(" ".join([*ctx.skills, *ctx.technologies]))

    parts: dict[str, float] = {}
    parts["title"] = _overlap(wanted_query, title_tokens) if wanted_query else 0.5
    parts["skills"] = _overlap(wanted_skills, body_tokens) if wanted_skills else 0.5

    parts["location"] = _location_score(offer, ctx)
    parts["job_type"] = _job_type_score(offer, ctx)
    parts["experience"] = _exp_score(offer, ctx)

    if ctx.language and offer.language:
        parts["language"] = 1.0 if ctx.language.lower() == offer.language.lower() else 0.3
    else:
        parts["language"] = 0.5

    fresh = freshness or getattr(offer, "freshness", None)
    if isinstance(fresh, str):
        try:
            fresh = FreshnessStatus(fresh)
        except ValueError:
            fresh = FreshnessStatus.unknown
    parts["freshness"] = _FRESHNESS_SCORE.get(fresh or FreshnessStatus.unknown, 0.4)

    total = sum(_WEIGHTS[k] * v for k, v in parts.items())

    if ctx.preferred_companies and offer.company:
        low = offer.company.lower()
        if any(p.lower() in low for p in ctx.preferred_companies):
            total += _PREFERRED_BOOST

    return round(max(0.0, min(1.0, total)), 4)


def _location_score(offer, ctx: JobSearchContext) -> float:
    want_remote = ctx.remote_type.value if ctx.remote_type else None
    off_remote = offer.remote_type.value if getattr(offer.remote_type, "value", None) else offer.remote_type
    if want_remote and want_remote != "any":
        if off_remote == want_remote:
            return 1.0
        if want_remote == "remote" and off_remote in (None, "hybrid"):
            return 0.4
        return 0.2
    target = " ".join(t for t in [ctx.city, ctx.location, ctx.country] if t).lower()
    if not target:
        return 0.6
    haystack = " ".join(t for t in [offer.city, offer.location, offer.country] if t).lower()
    if not haystack:
        return 0.4
    tset, hset = _tokens(target), _tokens(haystack)
    return 1.0 if tset & hset else 0.15


def _job_type_score(offer, ctx: JobSearchContext) -> float:
    want = ctx.job_type.value if ctx.job_type else None
    if not want or want == "any":
        return 0.6
    off = offer.job_type.value if getattr(offer.job_type, "value", None) else offer.job_type
    return 1.0 if off == want else 0.0


def _exp_score(offer, ctx: JobSearchContext) -> float:
    if not ctx.experience_level or ctx.experience_level.value == "any":
        return 0.6
    off = offer.experience_level.value if getattr(offer.experience_level, "value", None) else offer.experience_level
    if not off:
        return 0.5
    order = ["student", "entry", "junior", "mid", "senior", "lead"]
    try:
        gap = abs(order.index(ctx.experience_level.value) - order.index(off))
    except ValueError:
        return 0.5
    return max(0.0, 1.0 - 0.34 * gap)


def rank(offers: list, ctx: JobSearchContext, freshness_map: dict[str, FreshnessStatus] | None = None) -> list:
    """Attach ``match_score`` and drop excluded companies. Does not sort."""
    freshness_map = freshness_map or {}
    kept = []
    excluded = [e.lower() for e in ctx.excluded_companies]
    for offer in offers:
        if excluded and offer.company and any(x in offer.company.lower() for x in excluded):
            continue
        key = getattr(offer, "id", None) or getattr(offer, "source_job_id", None)
        offer.match_score = score(offer, ctx, freshness=freshness_map.get(key))
        kept.append(offer)
    return kept
