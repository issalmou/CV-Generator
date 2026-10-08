"""
Deterministic, LLM-free job <-> candidate matching for auto-apply preparation.

``evaluate(offer, ctx)`` returns a :class:`MatchResult`:
- ``score``           the numeric fit, reusing ``jobs.ranking.score`` (0..1)
- ``reasons``         short human-readable strings — *why* it matched
- ``missing_skills``  wanted skills not found anywhere in the offer

No network, no model, fully unit-testable. Signal comes from the merged
``JobSearchContext`` (the persistent ``UserProfile`` + anything the caller
typed) — nothing is invented.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from schemas.jobs import JobSearchContext
from services.jobs.ranking import score as _rank_score

_TOKEN_RE = re.compile(r"[a-z0-9+#][a-z0-9+#.]*")


def _tokens(*parts) -> set[str]:
    blob = " ".join(str(p) for p in parts if p).lower()
    return {t.strip(".") for t in _TOKEN_RE.findall(blob) if t.strip(".")}


@dataclass
class MatchResult:
    score: float
    reasons: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)


def evaluate(offer, ctx: JobSearchContext) -> MatchResult:
    score = 0.0
    try:
        score = _rank_score(offer, ctx)
    except Exception:  # noqa: BLE001 — a score is best-effort
        score = 0.0

    reasons: list[str] = []
    missing: list[str] = []

    offer_blob = _tokens(offer.title, getattr(offer, "description", None),
                         " ".join(getattr(offer, "skills", None) or []))
    title_tokens = _tokens(offer.title)

    # --- title / query overlap
    wanted_titles = [t for t in ([ctx.query] + list(ctx.keywords)) if t]
    for t in wanted_titles:
        if _tokens(t) & title_tokens:
            reasons.append(f"title matches “{t}”")
            break

    # --- skills
    wanted_skills = list(dict.fromkeys([*(ctx.skills or []), *(ctx.technologies or [])]))
    matched_skills = [s for s in wanted_skills if _tokens(s) & offer_blob]
    if matched_skills:
        reasons.append("skills present: " + ", ".join(matched_skills[:6]))
    missing = [s for s in wanted_skills if s not in matched_skills][:12]

    # --- location / remote
    want_remote = ctx.remote_type.value if ctx.remote_type else None
    off_remote = getattr(offer.remote_type, "value", offer.remote_type)
    if want_remote and want_remote != "any" and off_remote == want_remote:
        reasons.append(f"work mode: {want_remote}")
    elif ctx.city or ctx.location:
        target = _tokens(ctx.city, ctx.location, ctx.country)
        haystack = _tokens(getattr(offer, "city", None), offer.location,
                           getattr(offer, "country", None))
        if target and target & haystack:
            reasons.append("location matches")

    # --- experience
    if ctx.experience_level and getattr(ctx.experience_level, "value", None) not in (None, "any"):
        off_exp = getattr(offer.experience_level, "value", offer.experience_level)
        if off_exp and off_exp == ctx.experience_level.value:
            reasons.append(f"seniority: {off_exp}")

    # --- salary
    smin = getattr(offer, "salary_min", None)
    if ctx.salary_min and smin and smin >= ctx.salary_min:
        reasons.append("salary meets the minimum")
    if ctx.salary_min and smin and smin < ctx.salary_min:
        reasons.append("⚠ posted salary below your minimum")

    # --- language
    if ctx.language and offer.language and ctx.language.lower()[:2] == offer.language.lower()[:2]:
        reasons.append(f"language: {offer.language}")

    # --- sector
    if ctx.sectors:
        sblob = _tokens(offer.title, getattr(offer, "description", None), offer.company)
        hit = [s for s in ctx.sectors if _tokens(s) & sblob]
        if hit:
            reasons.append("sector: " + ", ".join(hit[:3]))

    if not reasons:
        reasons.append("weak match — few of your criteria are present in this posting")

    return MatchResult(score=round(float(score), 4), reasons=reasons[:8], missing_skills=missing)
