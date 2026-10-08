"""
``BrowserJobProvider`` — base class for providers that render a public page
with :data:`services.providers.browser.browser_session` before parsing.

Subclasses implement ``_render_and_parse(ctx, limit) -> (offers, strategy)``
using ``self.get_page(url, ...)``. The browser being off / not installed /
crashed is turned into a clean ``ProviderTemporarilyUnavailable`` (→ the
provider reports ``temporarily_unavailable``, never crashes the search).
"""

from __future__ import annotations

from config import settings
from schemas.jobs import JobSearchContext, NormalizedOffer
from services.providers.base import JobProvider, ProviderTemporarilyUnavailable
from services.providers.browser import BrowserUnavailable, browser_session


class BrowserJobProvider(JobProvider):
    @property
    def enabled(self) -> bool:
        return bool(settings.BROWSER_ENABLED) and browser_session.is_available()

    def get_page(self, url: str, **kwargs) -> str:
        try:
            return browser_session.get_page(url, **kwargs)
        except BrowserUnavailable as exc:
            raise ProviderTemporarilyUnavailable(f"browser: {exc}") from exc

    def _search(self, ctx: JobSearchContext, *, limit: int, deadline: float):
        return self._render_and_parse(ctx, limit=limit)

    def _render_and_parse(
        self, ctx: JobSearchContext, *, limit: int
    ) -> tuple[list[NormalizedOffer], str | None]:
        raise NotImplementedError
