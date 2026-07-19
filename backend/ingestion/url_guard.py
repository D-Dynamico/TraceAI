"""SSRF guards for user-supplied URLs.

`/api/ingest-url` fetches a URL chosen by the caller and returns the body. That
is a server-side request forgery primitive unless the destination is checked:
without this module, `http://169.254.169.254/latest/meta-data/` reaches the
cloud metadata service once the app is deployed (plan.md §12 targets Render),
and `http://127.0.0.1:8000/api/documents` reads the app's own private API.

Three things are enforced, and all three are needed:

  1. **Scheme** — http/https only. Blocks `file://`, `gopher://`, `ftp://`.
  2. **Resolved address** — the hostname is resolved and *every* address it maps
     to must be publicly routable. Checking the literal hostname is not enough:
     an attacker controls DNS for their own domain and can point it at
     127.0.0.1. Every address is checked because a name can return several.
  3. **Each redirect hop** — a permitted public URL can 302 to an internal one,
     so redirects are followed manually and re-validated. `requests`' automatic
     redirect following would bypass checks 1 and 2 entirely.

Known limitation: DNS rebinding (a record whose value changes between our
resolution and requests' own) is not addressed. Closing it means pinning the
resolved IP into the connection, which needs a custom transport adapter.
Out of proportion to a single-user portfolio app; documented rather than fixed.
"""

from __future__ import annotations

import ipaddress
import json as jsonlib
import logging
import socket
from dataclasses import dataclass, field
from urllib.parse import urlparse, urlunparse

import requests

logger = logging.getLogger(__name__)

ALLOWED_SCHEMES = ("http", "https")
MAX_REDIRECTS = 5
# Cap the body we are willing to buffer. Without this a malicious or merely
# enormous URL can exhaust memory — the extracted text is truncated to 20k
# chars before Gemini sees it anyway, so nothing useful is lost.
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


class BlockedUrlError(ValueError):
    """The URL is syntactically fine but points somewhere we refuse to fetch.

    Subclasses ValueError so existing callers that catch ValueError and return
    HTTP 400 keep working unchanged.
    """


def _is_public_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for addresses that are routable on the public internet.

    `is_global` alone is not sufficient: it reports True for multicast, so
    239.255.255.250 (SSDP) and 224.0.0.1 would otherwise be fetchable.

    The IPv4-mapped unwrap is belt-and-braces. Python 3.11+ makes
    `IPv6Address.is_private` delegate to the mapped v4 address, so
    ::ffff:127.0.0.1 is already caught on 3.12 (what this project runs).
    On 3.10 and earlier it was not, and the address passed as a global v6 one.
    Kept so the guard does not silently weaken if the interpreter changes.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def _resolve(hostname: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise BlockedUrlError(f"Could not resolve host {hostname!r}: {exc}") from exc
    return [info[4][0] for info in infos]


def validate_url(url: str) -> str:
    """Return `url` unchanged if it is safe to fetch, else raise BlockedUrlError."""
    parsed = urlparse(url)

    if parsed.scheme not in ALLOWED_SCHEMES:
        raise BlockedUrlError(
            f"Unsupported URL scheme: {parsed.scheme!r} (expected http/https)"
        )

    hostname = parsed.hostname
    if not hostname:
        raise BlockedUrlError("URL has no host.")

    # A userinfo section is how `http://api.github.com@127.0.0.1/` gets mistaken
    # for a GitHub URL by a human reading it. We resolve the real host either
    # way, but rejecting it outright avoids the confusion entirely.
    if parsed.username or parsed.password:
        raise BlockedUrlError("URLs with embedded credentials are not accepted.")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    for address in _resolve(hostname, port):
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise BlockedUrlError(f"Unparseable address for {hostname!r}: {address}")
        if not _is_public_address(ip):
            # Deliberately does not echo the resolved address back to the
            # caller — that would turn the error message into an internal
            # network scanner.
            logger.warning("Blocked non-public destination %s -> %s", hostname, address)
            raise BlockedUrlError(
                f"Refusing to fetch {hostname!r}: it resolves to a private, "
                "loopback, or otherwise non-public address."
            )

    return url


@dataclass
class SafeResponse:
    """A fetched response with its body already buffered under a size cap.

    Not a `requests.Response`. Enforcing the size limit means consuming the
    stream, which leaves the real object's `.content`/`.text` unusable — so the
    bytes are captured here instead, and callers get the small surface they
    actually use.
    """

    url: str
    status_code: int
    content: bytes
    headers: dict[str, str] = field(default_factory=dict)
    encoding: str | None = None

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    @property
    def text(self) -> str:
        # `errors="replace"` because a mangled character in a scraped page is
        # not worth failing an ingestion over.
        return self.content.decode(self.encoding or "utf-8", errors="replace")

    def json(self):
        return jsonlib.loads(self.content)


def safe_get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 15,
) -> SafeResponse:
    """GET a URL, validating the destination and every redirect hop.

    Raises BlockedUrlError if any hop is disallowed. Other network failures
    surface as the usual `requests.RequestException` so callers keep their
    existing graceful-degradation paths.
    """
    current = validate_url(url)

    for _ in range(MAX_REDIRECTS + 1):
        response = requests.get(
            current,
            headers=headers,
            timeout=timeout,
            allow_redirects=False,  # validated manually below
            stream=True,            # so an oversized body is never fully buffered
        )
        try:
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                if not location:
                    raise BlockedUrlError(
                        f"Redirect from {current} had no Location header."
                    )
                # Location may be relative; resolve against the current URL,
                # then re-run the full check on the result.
                current = validate_url(requests.compat.urljoin(current, location))
                continue

            body = _read_capped(response)
            return SafeResponse(
                url=current,
                status_code=response.status_code,
                content=body,
                headers=dict(response.headers),
                encoding=response.encoding,
            )
        finally:
            response.close()

    raise BlockedUrlError(f"Too many redirects (>{MAX_REDIRECTS}) starting at {url}")


def _read_capped(response: requests.Response) -> bytes:
    """Buffer the body, refusing anything over MAX_RESPONSE_BYTES.

    The declared Content-Length is checked first as a cheap exit, but it is
    server-controlled and may be absent or a lie, so the stream is counted as
    it is actually read.
    """
    declared = response.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
        raise BlockedUrlError(
            f"Response exceeds {MAX_RESPONSE_BYTES} byte limit "
            f"(declared {declared})."
        )

    chunks = bytearray()
    for chunk in response.iter_content(8192):
        chunks.extend(chunk)
        if len(chunks) > MAX_RESPONSE_BYTES:
            raise BlockedUrlError(f"Response exceeds {MAX_RESPONSE_BYTES} byte limit.")
    return bytes(chunks)


def normalize_url(url: str) -> str:
    """Trim whitespace and add a scheme if the user omitted one.

    Users paste `github.com/foo/bar`. Defaulting to https keeps that working
    without weakening validation — the result still goes through validate_url.
    """
    url = (url or "").strip()
    if not url:
        raise BlockedUrlError("URL is empty.")
    parsed = urlparse(url)
    if not parsed.scheme:
        url = urlunparse(("https", *urlparse(f"//{url}")[1:]))
    return url
