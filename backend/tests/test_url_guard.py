"""SSRF guard tests.

These are security assertions, so they follow the rule in CLAUDE.md: each was
validated by mutation — the guard was broken, the test confirmed failing, then
restored. Notes on which mutation proves which test are inline.

Most cases use literal IP addresses rather than hostnames so the checks run
against the local resolver only and the suite stays offline.
"""

from __future__ import annotations

import pytest
import requests

from ingestion import url_guard
from ingestion.url_guard import BlockedUrlError


# --- Fake transport --------------------------------------------------------


class FakeResponse:
    """The slice of requests.Response that url_guard.safe_get actually touches."""

    def __init__(self, status_code=200, body=b"", headers=None, encoding="utf-8"):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.encoding = encoding
        self.closed = False

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308) and "Location" in self.headers

    @property
    def is_permanent_redirect(self):
        return self.status_code in (301, 308)

    def iter_content(self, chunk_size):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]

    def close(self):
        self.closed = True


@pytest.fixture
def public_dns(monkeypatch):
    """Resolve every hostname to a public address, so no real DNS is needed."""
    monkeypatch.setattr(url_guard, "_resolve", lambda host, port: ["93.184.216.34"])


@pytest.fixture
def fake_get(monkeypatch):
    """Queue responses; returns the list of URLs that were requested."""
    requested: list[str] = []
    queue: list[FakeResponse] = []

    def _get(url, **kwargs):
        requested.append(url)
        assert kwargs.get("allow_redirects") is False, (
            "safe_get must not delegate redirect following to requests — "
            "that skips validation of the redirect target"
        )
        return queue.pop(0) if queue else FakeResponse()

    monkeypatch.setattr(url_guard.requests, "get", _get)
    return requested, queue


# --- Scheme ----------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "file://C:/Windows/win.ini",
    "gopher://127.0.0.1:11211/_stats",
    "ftp://internal.example.com/secrets",
    "data:text/html,<h1>hi</h1>",
])
def test_non_http_schemes_are_rejected(url):
    # Mutation: allow any scheme in validate_url -> file:// case fails.
    with pytest.raises(BlockedUrlError):
        url_guard.validate_url(url)


# --- Destination address ---------------------------------------------------


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/api/documents",   # the app's own API
    "http://localhost:8000/api/documents",
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata service
    "http://10.0.0.5/admin",
    "http://192.168.1.1/",
    "http://172.16.0.1/",
    "http://[::1]:8000/",                    # IPv6 loopback
    "http://0.0.0.0:8000/",
    "http://[::ffff:127.0.0.1]/",            # IPv4-mapped loopback
])
def test_non_public_destinations_are_blocked(url):
    """The exact payloads the handoff flagged as reachable before this guard.

    Mutation: drop the `_is_public_address` check -> every case here passes,
    including the 169.254.169.254 metadata endpoint.

    Note the ::ffff: case does *not* isolate the `ipv4_mapped` unwrap —
    verified by mutation: removing that unwrap keeps this suite green, because
    Python 3.11+ already delegates `is_private` to the mapped v4 address. The
    unwrap only matters on 3.10 and earlier. Kept as a regression test for the
    behaviour, not as proof of that line.
    """
    with pytest.raises(BlockedUrlError):
        url_guard.validate_url(url)


@pytest.mark.parametrize("address", [
    "224.0.0.1",          # all-hosts multicast
    "239.255.255.250",    # SSDP — the one an attacker would actually reach for
    "ff02::1",
])
def test_multicast_destinations_are_blocked(monkeypatch, address):
    """Multicast reports `is_global == True`, so it needs its own exclusion.

    Mutation: drop `and not ip.is_multicast` -> all three pass. This clause is
    load-bearing, which is not obvious from reading `is_global`.
    """
    monkeypatch.setattr(url_guard, "_resolve", lambda host, port: [address])
    with pytest.raises(BlockedUrlError):
        url_guard.validate_url("https://multicast.example.com/")


def test_public_destination_is_allowed(public_dns):
    assert url_guard.validate_url("https://example.com/page") == "https://example.com/page"


def test_hostname_resolving_to_loopback_is_blocked(monkeypatch):
    """An attacker controls DNS for their own domain and can point it inward.

    Mutation: validate the literal hostname instead of resolved addresses ->
    this passes, because "evil.example.com" looks perfectly ordinary.
    """
    monkeypatch.setattr(url_guard, "_resolve", lambda host, port: ["127.0.0.1"])
    with pytest.raises(BlockedUrlError):
        url_guard.validate_url("https://evil.example.com/")


def test_blocked_when_any_resolved_address_is_private(monkeypatch):
    """A name can return several addresses; one bad one is enough to refuse.

    Mutation: check only the first address -> this passes.
    """
    monkeypatch.setattr(
        url_guard, "_resolve", lambda host, port: ["93.184.216.34", "10.0.0.7"]
    )
    with pytest.raises(BlockedUrlError):
        url_guard.validate_url("https://mixed.example.com/")


def test_error_message_does_not_leak_resolved_address(monkeypatch):
    """Otherwise the error turns the endpoint into an internal network scanner."""
    monkeypatch.setattr(url_guard, "_resolve", lambda host, port: ["10.1.2.3"])
    with pytest.raises(BlockedUrlError) as exc:
        url_guard.validate_url("https://evil.example.com/")
    assert "10.1.2.3" not in str(exc.value)


def test_embedded_credentials_are_rejected(public_dns):
    """`http://api.github.com@127.0.0.1/` reads as GitHub to a human."""
    with pytest.raises(BlockedUrlError):
        url_guard.validate_url("http://api.github.com@example.com/")


def test_unresolvable_host_is_blocked():
    with pytest.raises(BlockedUrlError):
        url_guard.validate_url("https://nonexistent.invalid/")


# --- Redirects -------------------------------------------------------------


def test_redirect_to_internal_address_is_blocked(monkeypatch, fake_get):
    """A permitted public URL 302s to the metadata service.

    Mutation: pass allow_redirects=True and drop the manual loop -> this passes
    and the metadata body is returned to the caller. This is the case that
    scheme-only validation misses entirely.
    """
    requested, queue = fake_get
    monkeypatch.setattr(
        url_guard,
        "_resolve",
        lambda host, port: ["127.0.0.1"] if host == "169.254.169.254" or host.startswith("169") else ["93.184.216.34"],
    )
    queue.append(FakeResponse(
        302, headers={"Location": "http://169.254.169.254/latest/meta-data/"}
    ))

    with pytest.raises(BlockedUrlError):
        url_guard.safe_get("https://innocent.example.com/")

    assert requested == ["https://innocent.example.com/"], (
        "the redirect target must never be fetched"
    )


def test_relative_redirect_is_followed(monkeypatch, public_dns, fake_get):
    requested, queue = fake_get
    queue.append(FakeResponse(302, headers={"Location": "/landing"}))
    queue.append(FakeResponse(200, b"done"))

    resp = url_guard.safe_get("https://example.com/start")

    assert resp.text == "done"
    assert requested == ["https://example.com/start", "https://example.com/landing"]


def test_redirect_loop_is_capped(monkeypatch, public_dns, fake_get):
    requested, queue = fake_get
    for _ in range(url_guard.MAX_REDIRECTS + 2):
        queue.append(FakeResponse(302, headers={"Location": "https://example.com/next"}))

    with pytest.raises(BlockedUrlError, match="Too many redirects"):
        url_guard.safe_get("https://example.com/start")


# --- Response size ---------------------------------------------------------


def test_oversized_declared_body_is_refused(public_dns, fake_get):
    _, queue = fake_get
    queue.append(FakeResponse(
        200, b"x", headers={"Content-Length": str(url_guard.MAX_RESPONSE_BYTES + 1)}
    ))
    with pytest.raises(BlockedUrlError, match="byte limit"):
        url_guard.safe_get("https://example.com/huge")


def test_oversized_actual_body_is_refused_despite_honest_looking_header(
    public_dns, fake_get
):
    """Content-Length is server-controlled, so the real stream must be counted.

    Mutation: trust Content-Length and skip the streaming count -> this passes
    while the process buffers an unbounded body.
    """
    _, queue = fake_get
    queue.append(FakeResponse(
        200,
        b"x" * (url_guard.MAX_RESPONSE_BYTES + 1024),
        headers={"Content-Length": "10"},
    ))
    with pytest.raises(BlockedUrlError, match="byte limit"):
        url_guard.safe_get("https://example.com/lying")


def test_body_at_the_limit_is_accepted(public_dns, fake_get):
    _, queue = fake_get
    queue.append(FakeResponse(200, b"y" * 1000))
    assert len(url_guard.safe_get("https://example.com/ok").content) == 1000


# --- normalize_url ---------------------------------------------------------


def test_normalize_adds_https_scheme():
    assert url_guard.normalize_url("  github.com/foo/bar ") == "https://github.com/foo/bar"


def test_normalize_leaves_explicit_scheme_alone():
    assert url_guard.normalize_url("http://example.com/x") == "http://example.com/x"


def test_normalize_rejects_empty():
    with pytest.raises(BlockedUrlError):
        url_guard.normalize_url("   ")


def test_normalized_url_still_goes_through_validation():
    """Adding a scheme must not become a way to smuggle an internal host in.

    Mutation: have scrape_url skip validate_url after normalize_url -> passes.
    """
    with pytest.raises(BlockedUrlError):
        url_guard.validate_url(url_guard.normalize_url("127.0.0.1:8000/api"))
