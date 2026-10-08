"""
``JobProvider`` — the interface every job source implements.

Hard guarantees enforced here (subclasses only fill in ``_search``):
- ``search()`` **never raises** — any exception becomes a structured
  ``ProviderResult(status=unavailable, ...)`` and trips the circuit breaker.
- a per-call timeout budget is applied.
- an open circuit breaker short-circuits to ``unavailable`` without a request.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

from config import settings
from schemas.jobs import JobSearchContext, NormalizedOffer, ProviderState, ProviderStatus
from services.providers.circuit import CircuitBreaker
from services.providers.http import AccessDenied, HttpError
from services.providers.metrics import ProviderMetrics

logger = logging.getLogger(__name__)

RevalidationState = Literal["alive", "gone", "unknown"]


# --- provider-signalled states (all HttpError subclasses so existing
#     ``except HttpError`` handlers keep catching them) ---

class ProviderAuthRequired(HttpError):
    """The source demands an interactive login / session / security challenge.
    We stop and report — we never try to solve or bypass it."""

    def __init__(self, reason: str = "authentication required") -> None:
        super().__init__(reason)
        self.reason = reason


class ProviderBlocked(HttpError):
    """The source persistently denies automated access (hard 403 / 451 / 999)."""

    def __init__(self, reason: str = "access blocked") -> None:
        super().__init__(reason)
        self.reason = reason


class ProviderTemporarilyUnavailable(HttpError):
    """A transient failure (timeout, 429, network) — retry later."""

    def __init__(self, reason: str = "temporarily unavailable") -> None:
        super().__init__(reason)
        self.reason = reason


def _dedupe_by_id(offers: list[NormalizedOffer]) -> list[NormalizedOffer]:
    """Within-provider dedup — a source must not emit the same listing twice
    (e.g. the same job appearing on two overlapping result pages)."""
    seen: set[tuple[str, str]] = set()
    out: list[NormalizedOffer] = []
    for offer in offers:
        key = (offer.source.lower(), offer.source_job_id.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(offer)
    return out


@dataclass
class ProviderResult:
    provider: str
    status: ProviderStatus
    offers: list[NormalizedOffer] = field(default_factory=list)
    strategy_used: str | None = None
    error_kind: str | None = None
    state: ProviderState | None = None       # fine-grained; defaults from status

    def __post_init__(self) -> None:
        if self.state is None:
            self.state = {
                ProviderStatus.success: ProviderState.available,
                ProviderStatus.partial: ProviderState.degraded,
                ProviderStatus.disabled: ProviderState.disabled,
            }.get(self.status, ProviderState.temporarily_unavailable)


@dataclass
class RevalidationResult:
    state: RevalidationState = "unknown"
    expires_at: object | None = None            # datetime | None


@dataclass
class ApplicationMethod:
    # external_url  -> user finishes on the linked page   (manual_required)
    # platform_login_required / unknown -> extra step there (requires_user_action)
    kind: Literal["external_url", "platform_login_required", "unknown"] = "external_url"
    url: str | None = None


class JobProvider(ABC):
    """Base class. Subclasses set the class attributes and implement ``_search``."""

    name: str = "base"
    allowed_hosts: tuple[str, ...] = ()
    # Higher wins a dedup tie-break (keep this source's copy of a shared job).
    application_priority: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        self.breaker = CircuitBreaker(f"jobs:{self.name}")
        self.metrics = ProviderMetrics(self.name)
        self._last_status: ProviderStatus | None = None
        self._last_state: ProviderState | None = None
        self._run_started: float | None = None

    @property
    def enabled(self) -> bool:
        """Overridable — e.g. Adzuna returns False without credentials."""
        return True

    @property
    def last_status(self) -> ProviderStatus | None:
        return self._last_status

    @property
    def last_state(self) -> ProviderState | None:
        return self._last_state

    # ------------------------------------------------------------------
    # Public entrypoint — safe, never raises
    # ------------------------------------------------------------------

    def _result(self, state: ProviderState, *, offers=None, strategy=None,
                error_kind: str | None = None) -> ProviderResult:
        self._last_state = state
        self._last_status = state.to_status()
        duration_ms = (
            int((time.monotonic() - self._run_started) * 1000)
            if self._run_started is not None else None
        )
        self.metrics.record(
            state, offers=len(offers or []),
            duration_ms=duration_ms, error_kind=error_kind,
        )
        return ProviderResult(
            self.name, state.to_status(), offers=offers or [],
            strategy_used=strategy, error_kind=error_kind, state=state,
        )

    def search(self, ctx: JobSearchContext, *, limit: int) -> ProviderResult:
        self._run_started = time.monotonic()
        if not self.enabled:
            return self._result(ProviderState.disabled, error_kind="disabled")

        if not self.breaker.allow():
            logger.info("[%s] circuit open — skipping", self.name)
            return self._result(ProviderState.temporarily_unavailable, error_kind="circuit_open")

        started = self._run_started
        budget = settings.JOB_PROVIDER_TIMEOUT * settings.JOB_PROVIDER_MAX_PAGES + 5.0
        try:
            offers, strategy = self._search(ctx, limit=limit, deadline=started + budget)
        except ProviderAuthRequired as exc:
            self.breaker.record_failure()
            logger.warning("[%s] auth required: %s", self.name, exc.reason)
            return self._result(ProviderState.auth_required, error_kind=f"auth_required:{exc.reason}")
        except ProviderBlocked as exc:
            self.breaker.record_failure()
            logger.warning("[%s] blocked: %s", self.name, exc.reason)
            return self._result(ProviderState.blocked, error_kind=f"blocked:{exc.reason}")
        except ProviderTemporarilyUnavailable as exc:
            self.breaker.record_failure()
            logger.warning("[%s] temporarily unavailable: %s", self.name, exc.reason)
            return self._result(ProviderState.temporarily_unavailable,
                                error_kind=f"temp_unavailable:{exc.reason}")
        except AccessDenied as exc:
            self.breaker.record_failure()
            reason = exc.reason or ""
            state = (ProviderState.temporarily_unavailable if "429" in reason
                     else ProviderState.blocked)
            logger.warning("[%s] access denied: %s", self.name, reason)
            return self._result(state, error_kind=f"access_denied:{reason}")
        except HttpError as exc:
            self.breaker.record_failure()
            logger.warning("[%s] http error: %s", self.name, exc)
            return self._result(ProviderState.temporarily_unavailable, error_kind="http_error")
        except Exception as exc:  # noqa: BLE001 — isolation is the whole point
            self.breaker.record_failure()
            logger.exception("[%s] unexpected provider failure: %s", self.name, exc)
            return self._result(ProviderState.error, error_kind="exception")

        self.breaker.record_success()
        offers = _dedupe_by_id(offers)[:limit]
        # a strategy ran -> the provider is AVAILABLE even with 0 offers.
        # no strategy name + no offers -> it answered but incompletely -> DEGRADED.
        state = ProviderState.available if strategy or offers else ProviderState.degraded
        logger.info("[%s] %d offers via %s", self.name, len(offers), strategy or "-")
        return self._result(state, offers=offers, strategy=strategy)

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    @abstractmethod
    def _search(
        self, ctx: JobSearchContext, *, limit: int, deadline: float
    ) -> tuple[list[NormalizedOffer], str | None]:
        """Return ``(offers, strategy_name)``. Raise on unrecoverable failure —
        the wrapper turns it into a structured ``unavailable`` result."""

    def revalidate(self, offer) -> RevalidationResult:  # offer: models.JobOffer
        """Check whether ``offer`` is still listed. Default: cannot tell."""
        return RevalidationResult("unknown")

    def application_method(self, offer) -> ApplicationMethod:  # offer: models.JobOffer
        """Where/how the user applies. Default: the external listing URL."""
        return ApplicationMethod("external_url", offer.source_url)
