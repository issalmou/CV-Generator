"""BrowserSessionManager — lifecycle, single locked session, recovery, reaping.
A fake WebDriver is injected via ``_new_driver``; no real Chromium starts."""

from __future__ import annotations

import threading
import time

import pytest

from config import settings
from services.providers.browser import BrowserSessionManager, BrowserUnavailable


class _FakeDriver:
    instances = 0

    def __init__(self, *, crash_on_get=0):
        type(self).instances += 1
        self.gets = 0
        self.quit_called = False
        self._crash_on_get = crash_on_get
        self.page_source = "<html>rendered</html>"

    def set_page_load_timeout(self, t): pass
    def set_script_timeout(self, t): pass
    def implicitly_wait(self, t): pass

    def get(self, url):
        self.gets += 1
        if self._crash_on_get and self.gets <= self._crash_on_get:
            from selenium.common.exceptions import WebDriverException
            raise WebDriverException("boom")

    def quit(self):
        self.quit_called = True


@pytest.fixture
def mgr(monkeypatch):
    monkeypatch.setattr(settings, "BROWSER_ENABLED", True)
    monkeypatch.setattr(settings, "BROWSER_IDLE_SHUTDOWN_SECONDS", 300)
    monkeypatch.setattr(settings, "BROWSER_MAX_PAGES_PER_SESSION", 40)
    monkeypatch.setattr("services.providers.http._resolved_ips", lambda _host: ["93.184.216.34"])
    _FakeDriver.instances = 0
    m = BrowserSessionManager()
    monkeypatch.setattr(m, "_new_driver", lambda: _FakeDriver())
    return m


def test_disabled_reports_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "BROWSER_ENABLED", False)
    m = BrowserSessionManager()
    assert m.is_available() is False
    with pytest.raises(BrowserUnavailable):
        m.get_page("https://example.com", allowed_hosts=("example.com",))


def test_lazy_start_and_reuse(mgr):
    assert _FakeDriver.instances == 0
    html = mgr.get_page("https://example.com/a", allowed_hosts=("example.com",))
    assert html == "<html>rendered</html>"
    assert _FakeDriver.instances == 1
    mgr.get_page("https://example.com/b", allowed_hosts=("example.com",))
    assert _FakeDriver.instances == 1                 # reused, not recreated


def test_https_and_allowlist_enforced(mgr):
    from services.providers.http import SsrfError
    with pytest.raises(SsrfError):
        mgr.get_page("http://example.com", allowed_hosts=("example.com",))
    with pytest.raises(SsrfError):
        mgr.get_page("https://evil.example", allowed_hosts=("example.com",))


def test_single_session_under_concurrency(mgr):
    order = []
    real_render = mgr._render

    def _slow(url, sel, to):
        order.append(("in", url))
        time.sleep(0.05)
        order.append(("out", url))
        return real_render(url, sel, to)

    mgr._render = _slow
    threads = [
        threading.Thread(target=mgr.get_page,
                         kwargs=dict(url=f"https://example.com/{i}", allowed_hosts=("example.com",)))
        for i in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # renders never interleave (lock serialises them)
    for i in range(0, len(order), 2):
        assert order[i][0] == "in" and order[i + 1][0] == "out"
    assert _FakeDriver.instances == 1


def test_recycle_after_max_pages(mgr, monkeypatch):
    monkeypatch.setattr(settings, "BROWSER_MAX_PAGES_PER_SESSION", 2)
    mgr.get_page("https://example.com/1", allowed_hosts=("example.com",))
    mgr.get_page("https://example.com/2", allowed_hosts=("example.com",))  # hits the cap -> quit
    assert mgr._driver is None
    mgr.get_page("https://example.com/3", allowed_hosts=("example.com",))  # new driver
    assert _FakeDriver.instances == 2


def test_crash_recovers_once(mgr, monkeypatch):
    made = {"n": 0}

    def _factory():
        made["n"] += 1
        return _FakeDriver(crash_on_get=1 if made["n"] == 1 else 0)  # only #1 crashes

    monkeypatch.setattr(mgr, "_new_driver", _factory)
    html = mgr.get_page("https://example.com/x", allowed_hosts=("example.com",))
    assert html == "<html>rendered</html>"
    assert _FakeDriver.instances == 2                 # first crashed, second served


def test_crash_twice_raises_browser_unavailable(mgr, monkeypatch):
    monkeypatch.setattr(mgr, "_new_driver", lambda: _FakeDriver(crash_on_get=99))
    with pytest.raises(BrowserUnavailable):
        mgr.get_page("https://example.com/x", allowed_hosts=("example.com",))
    assert mgr._driver is None                        # cleaned up


def test_idle_reaping(mgr, monkeypatch):
    import services.providers.browser as _b
    monkeypatch.setattr(settings, "BROWSER_IDLE_SHUTDOWN_SECONDS", 1)
    mgr.get_page("https://example.com/1", allowed_hosts=("example.com",))
    d1 = mgr._driver
    real = _b.time.monotonic
    monkeypatch.setattr(_b.time, "monotonic", lambda: real() + 100)
    mgr.get_page("https://example.com/2", allowed_hosts=("example.com",))
    assert d1.quit_called is True
    assert _FakeDriver.instances == 2


def test_shutdown_is_idempotent(mgr):
    mgr.get_page("https://example.com/1", allowed_hosts=("example.com",))
    mgr.shutdown()
    assert mgr._driver is None
    mgr.shutdown()                                    # no error
