"""
Provider registry — the only thing ``jobs.search_service`` imports.

Instantiates every :class:`JobProvider` named in
``settings.enabled_providers_list`` (unknown names are ignored with a
warning; a provider whose ``enabled`` is False — e.g. Adzuna without
credentials — is registered but reported as disabled).
"""

from __future__ import annotations

import logging

from config import settings
from schemas.jobs import ProviderInfo, ProviderState
from services.providers.adzuna_provider import AdzunaProvider
from services.providers.arbeitnow_provider import ArbeitnowProvider
from services.providers.ashby_provider import AshbyProvider
from services.providers.base import JobProvider
from services.providers.career_pages_provider import CareerPagesProvider
from services.providers.greenhouse_provider import GreenhouseProvider
from services.providers.hackernews_provider import HackerNewsProvider
from services.providers.himalayas_provider import HimalayasProvider
from services.providers.indeed_provider import IndeedProvider
from services.providers.jobicy_provider import JobicyProvider
from services.providers.lever_provider import LeverProvider
from services.providers.linkedin_provider import LinkedInProvider
from services.providers.oracle_hcm_provider import OracleHcmProvider
from services.providers.phenom_provider import PhenomProvider
from services.providers.remoteok_provider import RemoteOkProvider
from services.providers.remotive_provider import RemotiveProvider
from services.providers.rippling_provider import RipplingProvider
from services.providers.smartrecruiters_provider import SmartRecruitersProvider
from services.providers.talentbrew_provider import TalentBrewProvider
from services.providers.weworkremotely_provider import WeWorkRemotelyProvider
from services.providers.workday_provider import WorkdayProvider

logger = logging.getLogger(__name__)

_PROVIDER_CLASSES: dict[str, type[JobProvider]] = {
    LinkedInProvider.name: LinkedInProvider,
    IndeedProvider.name: IndeedProvider,
    ArbeitnowProvider.name: ArbeitnowProvider,
    WeWorkRemotelyProvider.name: WeWorkRemotelyProvider,
    HackerNewsProvider.name: HackerNewsProvider,
    RemotiveProvider.name: RemotiveProvider,
    JobicyProvider.name: JobicyProvider,
    RemoteOkProvider.name: RemoteOkProvider,
    HimalayasProvider.name: HimalayasProvider,
    AdzunaProvider.name: AdzunaProvider,
    GreenhouseProvider.name: GreenhouseProvider,
    LeverProvider.name: LeverProvider,
    CareerPagesProvider.name: CareerPagesProvider,
    AshbyProvider.name: AshbyProvider,
    WorkdayProvider.name: WorkdayProvider,
    OracleHcmProvider.name: OracleHcmProvider,
    SmartRecruitersProvider.name: SmartRecruitersProvider,
    RipplingProvider.name: RipplingProvider,
    PhenomProvider.name: PhenomProvider,
    TalentBrewProvider.name: TalentBrewProvider,
}


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, JobProvider] = {}
        self.reload()

    def reload(self) -> None:
        """(Re)build the provider set from the current settings — used by tests."""
        self._providers = {}
        for name in settings.enabled_providers_list:
            cls = _PROVIDER_CLASSES.get(name)
            if cls is None:
                logger.warning("[jobs.registry] unknown provider %r — ignored", name)
                continue
            self._providers[name] = cls()

    # ------------------------------------------------------------------

    def names(self) -> list[str]:
        return list(self._providers)

    def get(self, name: str) -> JobProvider | None:
        return self._providers.get((name or "").lower())

    def active(self, only: list[str] | None = None) -> list[JobProvider]:
        """Providers that are enabled AND whose circuit breaker is closed."""
        wanted = {n.lower() for n in only} if only else None
        out = []
        for name, provider in self._providers.items():
            if wanted is not None and name not in wanted:
                continue
            if not provider.enabled or not provider.breaker.allow():
                continue
            out.append(provider)
        return out

    def all_info(self) -> list[ProviderInfo]:
        from datetime import datetime, timezone

        from services.providers.metrics import error_rate, health_note

        def _ts(epoch):
            return datetime.fromtimestamp(epoch, tz=timezone.utc) if epoch else None

        out: list[ProviderInfo] = []
        for name, provider in self._providers.items():
            snap = provider.metrics.snapshot()
            circuit = provider.breaker.state_name()
            last_state = provider.last_state
            if last_state is None and snap.get("last_state"):
                try:
                    last_state = ProviderState(snap["last_state"])
                except ValueError:
                    last_state = None
            out.append(ProviderInfo(
                name=name,
                enabled=provider.enabled,
                circuit_state=circuit,
                last_status=provider.last_status
                or (last_state.to_status() if last_state else None),
                state=last_state,
                runs=int(snap.get("runs", 0) or 0),
                error_rate=error_rate(snap),
                last_run_at=_ts(snap.get("last_run_at")),
                last_ok_at=_ts(snap.get("last_ok_at")),
                last_offer_count=snap.get("last_offer_count"),
                last_duration_ms=snap.get("last_duration_ms"),
                last_error=snap.get("last_error"),
                health_note=health_note(snap, circuit),
            ))
        return out


registry = ProviderRegistry()
