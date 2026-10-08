"""
The shared provider HTTP toolkit + circuit breaker.

These tests drive ``services/providers/http.py`` directly (marker
``real_http`` disables the network safety-net) with a fake ``httpx.Client``.
"""

from __future__ import annotations

import pytest

import services.providers.http as h
from services.providers.circuit import CircuitBreaker

pytestmark = pytest.mark.real_http


# ---------------------------------------------------------------------------
# SSRF / URL validation
# ---------------------------------------------------------------------------

def _force_ip(monkeypatch, ip: str):
    monkeypatch.setattr(h.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", (ip, 0))])


def test_validate_url_rejects_non_https():
    with pytest.raises(h.SsrfError):
        h.validate_url("http://example.com/x", ("example.com",))


def test_validate_url_rejects_off_allowlist_host(monkeypatch):
    _force_ip(monkeypatch, "93.184.216.34")
    with pytest.raises(h.SsrfError):
        h.validate_url("https://evil.example/x", ("linkedin.com",))


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.10",
                                "169.254.1.1", "::1", "0.0.0.0",
                                "100.64.0.1", "100.100.50.20", "100.127.255.254"])
def test_validate_url_rejects_private_and_loopback_ips(monkeypatch, ip):
    _force_ip(monkeypatch, ip)
    with pytest.raises(h.SsrfError):
        h.validate_url("https://internal.linkedin.com/x", ("linkedin.com",))


def test_validate_url_allows_ip_just_outside_cgnat(monkeypatch):
    # 100.63.x and 100.128.x are public — the CGNAT block must not overreach.
    _force_ip(monkeypatch, "100.63.255.255")
    assert h.validate_url("https://www.linkedin.com/x", ("linkedin.com",)) == "www.linkedin.com"


def test_validate_url_allows_public_ip_on_allowed_host(monkeypatch):
    _force_ip(monkeypatch, "93.184.216.34")
    assert h.validate_url("https://www.linkedin.com/x", ("linkedin.com",)) == "www.linkedin.com"


# ---------------------------------------------------------------------------
# fetch — a fake httpx.Client
# ---------------------------------------------------------------------------

class _FakeStream:
    def __init__(self, status, body=b"", *, headers=None, is_redirect=False):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.is_redirect = is_redirect
        self.encoding = "utf-8"
        self.url = "https://www.linkedin.com/x"

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def iter_bytes(self):
        chunk = 65536
        for i in range(0, len(self._body), chunk):
            yield self._body[i:i + chunk]


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []
        self.calls: list[dict] = []

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def stream(self, method, url, **kwargs):
        self.requests.append(url)
        self.calls.append({"method": method, "url": url, **kwargs})
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def patch_client(monkeypatch):
    def _apply(responses):
        monkeypatch.setattr(h.socket, "getaddrinfo",
                            lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))])
        client = _FakeClient(responses)
        monkeypatch.setattr(h.httpx, "Client", lambda **kw: client)
        return client
    return _apply


def test_fetch_returns_body(patch_client):
    patch_client([_FakeStream(200, b"hello world")])
    result = h.fetch("https://www.linkedin.com/x", allowed_hosts=("linkedin.com",))
    assert result.ok and result.text == "hello world"


def test_fetch_size_cap(patch_client, monkeypatch):
    monkeypatch.setattr(h.settings, "JOB_HTTP_MAX_BYTES", 10)
    patch_client([_FakeStream(200, b"x" * 5000)])
    with pytest.raises(h.HttpError):
        h.fetch("https://www.linkedin.com/x", allowed_hosts=("linkedin.com",))


def test_fetch_per_call_max_bytes_overrides_global(patch_client, monkeypatch):
    # global cap is tiny, but a per-call max_bytes lifts it for this one request
    monkeypatch.setattr(h.settings, "JOB_HTTP_MAX_BYTES", 10)
    patch_client([_FakeStream(200, b"y" * 5000)])
    result = h.fetch("https://www.linkedin.com/x", allowed_hosts=("linkedin.com",),
                     max_bytes=1_000_000)
    assert result.ok and len(result.text) == 5000


def test_fetch_per_call_max_bytes_still_enforced(patch_client, monkeypatch):
    monkeypatch.setattr(h.settings, "JOB_HTTP_MAX_BYTES", 10_000_000)
    patch_client([_FakeStream(200, b"z" * 5000)])
    with pytest.raises(h.HttpError):
        h.fetch("https://www.linkedin.com/x", allowed_hosts=("linkedin.com",), max_bytes=100)


def test_fetch_sends_json_body_on_first_hop_only(patch_client):
    client = patch_client([
        _FakeStream(307, b"", headers={"location": "https://www.linkedin.com/moved"},
                    is_redirect=True),
        _FakeStream(200, b"ok"),
    ])
    result = h.fetch("https://www.linkedin.com/x", method="POST",
                     allowed_hosts=("linkedin.com",), json_body={"a": 1})
    assert result.text == "ok"
    assert client.calls[0].get("json") == {"a": 1}     # body on the first request
    assert "json" not in client.calls[1]               # never replayed after a redirect


def test_fetch_no_json_key_when_no_body(patch_client):
    client = patch_client([_FakeStream(200, b"ok")])
    h.fetch("https://www.linkedin.com/x", allowed_hosts=("linkedin.com",))
    assert "json" not in client.calls[0]


def test_fetch_blocks_403_without_retry(patch_client):
    client = patch_client([_FakeStream(403, b"nope"), _FakeStream(200, b"should not reach")])
    with pytest.raises(h.AccessDenied) as exc:
        h.fetch("https://www.linkedin.com/x", allowed_hosts=("linkedin.com",))
    assert "403" in exc.value.reason
    assert len(client.requests) == 1               # never retried


@pytest.mark.parametrize("code", [429, 451, 999])
def test_fetch_blocks_are_access_denied(patch_client, code):
    patch_client([_FakeStream(code, b"")])
    with pytest.raises(h.AccessDenied):
        h.fetch("https://www.linkedin.com/x", allowed_hosts=("linkedin.com",))


def test_fetch_retries_once_on_503(patch_client):
    client = patch_client([_FakeStream(503, b""), _FakeStream(200, b"ok")])
    result = h.fetch("https://www.linkedin.com/x", allowed_hosts=("linkedin.com",))
    assert result.text == "ok" and len(client.requests) == 2


def test_fetch_retries_once_on_connect_error(patch_client):
    client = patch_client([h.httpx.ConnectError("boom"), _FakeStream(200, b"ok")])
    result = h.fetch("https://www.linkedin.com/x", allowed_hosts=("linkedin.com",))
    assert result.text == "ok" and len(client.requests) == 2


def test_fetch_gives_up_after_second_failure(patch_client):
    patch_client([h.httpx.ConnectError("a"), h.httpx.ConnectError("b")])
    with pytest.raises(h.HttpError):
        h.fetch("https://www.linkedin.com/x", allowed_hosts=("linkedin.com",))


def test_fetch_follows_bounded_redirects_and_revalidates(patch_client, monkeypatch):
    seen_hosts = []
    real_validate = h.validate_url

    def _spy(url, hosts):
        host = real_validate(url, hosts)
        seen_hosts.append(host)
        return host

    monkeypatch.setattr(h, "validate_url", _spy)
    patch_client([
        _FakeStream(302, b"", headers={"location": "https://www.linkedin.com/final"}, is_redirect=True),
        _FakeStream(200, b"done"),
    ])
    result = h.fetch("https://www.linkedin.com/start", allowed_hosts=("linkedin.com",))
    assert result.text == "done"
    assert len(seen_hosts) == 2                     # each hop validated


def test_fetch_too_many_redirects(patch_client, monkeypatch):
    monkeypatch.setattr(h.settings, "JOB_HTTP_MAX_REDIRECTS", 1)
    patch_client([
        _FakeStream(302, b"", headers={"location": "https://www.linkedin.com/a"}, is_redirect=True),
        _FakeStream(302, b"", headers={"location": "https://www.linkedin.com/b"}, is_redirect=True),
        _FakeStream(302, b"", headers={"location": "https://www.linkedin.com/c"}, is_redirect=True),
    ])
    with pytest.raises(h.HttpError):
        h.fetch("https://www.linkedin.com/x", allowed_hosts=("linkedin.com",))


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

def test_circuit_opens_after_threshold_then_half_opens(monkeypatch):
    monkeypatch.setattr("services.providers.circuit.settings.JOB_CIRCUIT_FAIL_THRESHOLD", 3)
    monkeypatch.setattr("services.providers.circuit.settings.JOB_CIRCUIT_COOLDOWN_SECONDS", 100)
    cb = CircuitBreaker("test:x")
    assert cb.allow() and cb.state_name() == "closed"
    cb.record_failure()
    assert cb.allow() and cb.state_name() == "half_open"
    cb.record_failure()
    cb.record_failure()
    assert not cb.allow() and cb.state_name() == "open"

    # cooldown elapses -> half-open
    import services.providers.circuit as circ
    now = circ.time.time()
    monkeypatch.setattr(circ.time, "time", lambda: now + 200)
    assert cb.allow()

    cb.record_success()
    assert cb.allow() and cb.state_name() == "closed"
