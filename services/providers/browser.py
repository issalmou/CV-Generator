"""
BrowserSessionManager — a single, controlled, headless Chromium session
shared by the browser-based job providers.

Design constraints (see the plan):
- **One driver, serialised.** All access goes through ``get_page`` which
  holds an ``RLock``; at most one page renders at a time (no "10 Chromes").
- **Lazy.** The WebDriver is created on the first ``get_page``, never at
  import. ``import selenium`` failing or ``BROWSER_ENABLED=false`` →
  ``is_available()`` is False and ``get_page`` raises ``BrowserUnavailable``.
- **Recycled** after ``BROWSER_MAX_PAGES_PER_SESSION`` renders, **reaped**
  after ``BROWSER_IDLE_SHUTDOWN_SECONDS`` idle, **recovered** once on a
  ``WebDriverException``.
- **Public pages only.** ``http.validate_url`` (https-only + SSRF IP checks)
  gates every navigation. No credentials, no cookies, no login, no
  authenticated sessions, no LinkedIn.
- Logs carry ``operation / status / duration_ms / pages`` — never a URL
  query string, never a secret.

``main.lifespan`` calls ``browser_session.shutdown()`` on shutdown.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
import time

from config import settings
from services.providers.http import validate_url

logger = logging.getLogger(__name__)


class BrowserUnavailable(RuntimeError):
    """The browser cannot serve a page (disabled, not installed, or crashed)."""


def _selenium_installed() -> bool:
    return importlib.util.find_spec("selenium") is not None


class BrowserSessionManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._driver = None
        self._pages_this_session = 0
        self._last_used = 0.0

    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        return bool(settings.BROWSER_ENABLED) and _selenium_installed()

    # ------------------------------------------------------------------
    # Driver lifecycle
    # ------------------------------------------------------------------

    def _new_driver(self):
        """Create a headless Chromium WebDriver. Overridden/monkeypatched in tests."""
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service

        opts = Options()
        if settings.BROWSER_HEADLESS:
            opts.add_argument("--headless=new")
        for arg in (
            "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
            "--disable-extensions", "--disable-background-networking",
            "--window-size=1280,2400", "--lang=en-US",
        ):
            opts.add_argument(arg)
        opts.add_argument(f"--user-agent={settings.JOB_PROVIDER_USER_AGENT}")
        if settings.BROWSER_USER_DATA_DIR:
            opts.add_argument(f"--user-data-dir={settings.BROWSER_USER_DATA_DIR}")
        if settings.BROWSER_BINARY:
            opts.binary_location = settings.BROWSER_BINARY

        service = Service(executable_path=settings.BROWSER_DRIVER_PATH or None)
        driver = webdriver.Chrome(options=opts, service=service)
        driver.set_page_load_timeout(settings.BROWSER_PAGE_LOAD_TIMEOUT)
        driver.set_script_timeout(settings.BROWSER_SCRIPT_TIMEOUT)
        if settings.BROWSER_IMPLICIT_WAIT:
            driver.implicitly_wait(settings.BROWSER_IMPLICIT_WAIT)
        return driver

    def _ensure_driver(self):
        if self._driver is None:
            logger.info("[browser] starting session")
            t0 = time.monotonic()
            self._driver = self._new_driver()
            self._pages_this_session = 0
            logger.info("[browser] session started | duration_ms=%d",
                        (time.monotonic() - t0) * 1000)
        return self._driver

    def _quit(self) -> None:
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception as exc:  # noqa: BLE001 — best-effort teardown
                logger.debug("[browser] quit error: %s", exc)
            self._driver = None
            self._pages_this_session = 0

    def _maybe_reap(self) -> None:
        if (self._driver is not None
                and settings.BROWSER_IDLE_SHUTDOWN_SECONDS > 0
                and time.monotonic() - self._last_used > settings.BROWSER_IDLE_SHUTDOWN_SECONDS):
            logger.info("[browser] idle — reaping session")
            self._quit()

    def shutdown(self) -> None:
        with self._lock:
            self._quit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_page(
        self,
        url: str,
        *,
        allowed_hosts: tuple[str, ...] = (),
        wait_selector: str | None = None,
        timeout: float | None = None,
    ) -> str:
        """Render a public page and return its DOM HTML. Serialised."""
        if not self.is_available():
            raise BrowserUnavailable(
                "browser disabled" if not settings.BROWSER_ENABLED else "selenium not installed"
            )
        validate_url(url, allowed_hosts)   # https-only + SSRF IP checks — raises SsrfError

        with self._lock:
            self._maybe_reap()
            t0 = time.monotonic()
            try:
                html = self._render(url, wait_selector, timeout)
            except BrowserUnavailable:
                raise
            except Exception as exc:  # WebDriverException etc.
                logger.warning("[browser] render failed (%s) — recovering", type(exc).__name__)
                self._quit()
                try:
                    html = self._render(url, wait_selector, timeout)
                except Exception as exc2:  # noqa: BLE001
                    self._quit()
                    raise BrowserUnavailable(f"render failed after recovery: {type(exc2).__name__}")

            self._pages_this_session += 1
            self._last_used = time.monotonic()
            logger.info("[browser] page rendered | status=ok | duration_ms=%d | pages=%d",
                        (time.monotonic() - t0) * 1000, self._pages_this_session)
            if self._pages_this_session >= settings.BROWSER_MAX_PAGES_PER_SESSION:
                logger.info("[browser] recycling session after %d pages", self._pages_this_session)
                self._quit()
            return html

    def _render(self, url: str, wait_selector: str | None, timeout: float | None) -> str:
        driver = self._ensure_driver()
        if timeout:
            driver.set_page_load_timeout(timeout)
        driver.get(url)
        if wait_selector:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.support.ui import WebDriverWait

            WebDriverWait(driver, timeout or settings.BROWSER_PAGE_LOAD_TIMEOUT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, wait_selector))
            )
        return driver.page_source


browser_session = BrowserSessionManager()
