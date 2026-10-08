"""
The single outbound-HTTP chokepoint for every job provider.

A provider must NEVER create its own ``httpx``/``requests`` client — it
calls :func:`fetch` / :func:`fetch_json` here so that these protections
apply uniformly:

- **HTTPS only.**
- **Host allow-list** per provider (suffix match).
- **SSRF guard**: the host is DNS-resolved and every resolved IP is
  rejected if it is private / loopback / link-local / reserved / multicast
  / unspecified / RFC 6598 carrier-grade NAT (``100.64.0.0/10`` —
  Tailscale / overlay networks Python does not flag as private).
  Re-checked on every redirect hop.
- **Bounded redirects** (``JOB_HTTP_MAX_REDIRECTS``), each hop re-validated.
- **Response size cap** (``JOB_HTTP_MAX_BYTES``, or a per-call ``max_bytes``
  for the larger ATS JSON boards) — the body is streamed and the
  connection dropped once the cap is hit.
- **Timeout** (``JOB_PROVIDER_TIMEOUT``).
- **POST JSON body** (``json_body=``) — sent on the first hop only; a
  redirect drops it (never replayed to another host).
- **Retry policy**: one retry on a connection error / 502 / 503 / 504.
  **Never** retried — 403 / 429 / 451 / 999 raise :class:`AccessDenied`
  (we treat the source as unavailable; we never try to defeat a block).
- **Per-host politeness delay** (``JOB_PROVIDER_REQUEST_DELAY``).
- A configurable, honest **User-Agent** identifying the bot.

No secrets are ever logged here (only method + host + status).
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from config import settings

logger = logging.getLogger(__name__)

_NO_RETRY_STATUS = {403, 429, 451, 999}
_RETRY_STATUS = {502, 503, 504}

# RFC 6598 carrier-grade NAT. ``ipaddress`` does not flag this range as
# private, but Tailscale and several self-hosting overlay networks live
# here — so it is off-limits for an outbound provider fetch.
_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")

# per-host last-request monotonic timestamp, for the politeness delay
_last_request: dict[str, float] = {}
_throttle_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class HttpError(Exception):
    """Any outbound-HTTP failure a provider should treat as 'source down'."""


class SsrfError(HttpError):
    """A URL failed the security validation (scheme / host / resolved IP)."""


class AccessDenied(HttpError):
    """The host explicitly blocked us (403/429/451/999). Not retried, not evaded."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"access denied: {reason}")
        self.reason = reason


@dataclass
class HttpResult:
    status_code: int
    text: str
    url: str
    ok: bool
    headers: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# SSRF validation
# ---------------------------------------------------------------------------

def _host_allowed(host: str, allowed_hosts: tuple[str, ...]) -> bool:
    host = host.lower()
    for h in allowed_hosts:
        h = h.lower().lstrip(".")
        if host == h or host.endswith("." + h):
            return True
    return False


def _resolved_ips(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SsrfError(f"DNS resolution failed for {host!r}") from exc
    return list({info[4][0] for info in infos})


def _ip_is_public(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_NET:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_url(url: str, allowed_hosts: tuple[str, ...]) -> str:
    """Raise :class:`SsrfError` unless ``url`` is a safe https URL on an
    allowed host that resolves only to public IPs. Returns the host."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise SsrfError(f"non-https scheme: {parsed.scheme!r}")
    host = parsed.hostname or ""
    if not host:
        raise SsrfError("missing host")
    if allowed_hosts and not _host_allowed(host, allowed_hosts):
        raise SsrfError(f"host {host!r} not in the provider allow-list")
    for ip in _resolved_ips(host):
        if not _ip_is_public(ip):
            raise SsrfError(f"host {host!r} resolves to non-public address {ip}")
    return host


# ---------------------------------------------------------------------------
# Politeness
# ---------------------------------------------------------------------------

def _throttle(host: str) -> None:
    delay = settings.JOB_PROVIDER_REQUEST_DELAY
    if delay <= 0:
        return
    with _throttle_lock:
        now = time.monotonic()
        wait = delay - (now - _last_request.get(host, 0.0))
        _last_request[host] = now + max(0.0, wait)
    if wait > 0:
        time.sleep(min(wait, delay))


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

def _default_headers(accept_language: str | None) -> dict[str, str]:
    return {
        "User-Agent": settings.JOB_PROVIDER_USER_AGENT,
        "Accept-Language": accept_language or "en,fr;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    }


def fetch(
    url: str,
    *,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    json_body: Any | None = None,
    allowed_hosts: tuple[str, ...] = (),
    accept_language: str | None = None,
    timeout: float | None = None,
    max_bytes: int | None = None,
) -> HttpResult:
    """Perform one guarded request (following safe redirects manually).

    ``json_body`` (POST/PUT etc.) is serialised as JSON and sent on the
    **first** hop only — a redirect drops it, so a request body is never
    replayed against a different host. ``max_bytes`` overrides the global
    response-size cap for this one call (still enforced)."""
    timeout = timeout or settings.JOB_PROVIDER_TIMEOUT
    size_cap = max_bytes if max_bytes and max_bytes > 0 else settings.JOB_HTTP_MAX_BYTES
    req_headers = _default_headers(accept_language)
    if headers:
        req_headers.update(headers)

    current = url
    body_payload = json_body
    attempt_retry_done = False

    with httpx.Client(follow_redirects=False, timeout=timeout) as client:
        for _hop in range(settings.JOB_HTTP_MAX_REDIRECTS + 1):
            host = validate_url(current, allowed_hosts)
            _throttle(host)
            logger.info("[jobs.http] %s %s", method, host)

            stream_kwargs: dict[str, Any] = {"params": params, "headers": req_headers}
            if body_payload is not None:
                stream_kwargs["json"] = body_payload
            try:
                with client.stream(method, current, **stream_kwargs) as resp:
                    if resp.status_code in _NO_RETRY_STATUS:
                        raise AccessDenied(f"HTTP {resp.status_code} from {host}")
                    if resp.status_code in _RETRY_STATUS and not attempt_retry_done:
                        attempt_retry_done = True
                        time.sleep(0.5)
                        continue
                    if resp.is_redirect:
                        location = resp.headers.get("location", "")
                        if not location:
                            raise HttpError("redirect without Location")
                        current = urljoin(current, location)
                        params = None       # only on the first request
                        body_payload = None  # never replay a body across a redirect
                        continue

                    body = bytearray()
                    for chunk in resp.iter_bytes():
                        body.extend(chunk)
                        if len(body) > size_cap:
                            raise HttpError(f"response exceeded {size_cap} bytes")
                    text = body.decode(resp.encoding or "utf-8", errors="replace")
                    return HttpResult(
                        status_code=resp.status_code,
                        text=text,
                        url=str(resp.url),
                        ok=resp.status_code < 400,
                        headers={k.lower(): v for k, v in resp.headers.items()},
                    )
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.PoolTimeout,
                    httpx.ConnectTimeout, httpx.RemoteProtocolError) as exc:
                if attempt_retry_done:
                    raise HttpError(f"connection failure: {exc!r}") from exc
                attempt_retry_done = True
                time.sleep(0.5)
                continue

        raise HttpError("too many redirects")


def fetch_json(url: str, **kwargs: Any) -> Any:
    """:func:`fetch` + JSON decode. Malformed JSON -> :class:`HttpError`."""
    import json

    result = fetch(url, **kwargs)
    try:
        return json.loads(result.text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HttpError(f"malformed JSON from {urlparse(result.url).hostname}") from exc
