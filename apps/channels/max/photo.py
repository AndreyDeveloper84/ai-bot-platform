"""MAX photo attachment extraction + streaming download.

Ported from ``legacy_maxbot/handlers/food_scanner.py:_first_photo_url``
(line 525) and ``:_download_photo`` (line 538). Two changes vs the
legacy + one hardening pass added during the platform port:

1. **Sync, not async.** The platform's MAX channel handler runs in a
   sync Celery worker (see ``apps/channels/max/handler.py``); legacy
   ran inside maxapi's asyncio router. Sync ``httpx.Client.stream``
   gives us the same chunked-download + size-cap behaviour without
   the asgiref bridge.

2. **Dict-shaped attachments.** The parser (``apps/channels/max/parser.py``)
   keeps the raw wire payload — a list of dicts like
   ``{"type": "image", "payload": {"url": "..."}}``. Legacy operated
   on the maxapi SDK's ``Attachment`` dataclasses (``attr.type ==
   AttachmentType.IMAGE``). We work with the dict form directly to
   avoid dragging maxapi onto the platform's dispatch path.

3. **SSRF defence (added during PR #893 adversarial review).** The
   MAX webhook envelope is HMAC-verified upstream, but the
   ``attachments[i].payload.url`` field inside the envelope is
   attacker-controllable wire format — a hostile MAX user can ship
   ``http://169.254.169.254/latest/meta-data/...``,
   ``http://localhost:6379/``, ``file:///etc/passwd``, etc. Legacy
   mysite shipped without validation because it ran behind different
   trust boundaries; the production track record «30+ days, no
   incident» is not evidence of safety — it's evidence nobody
   probed yet. :func:`_validate_cdn_url` rejects anything that isn't
   ``https://`` to a public, non-loopback, non-link-local, non-private,
   non-multicast/reserved IP. Pattern class: GitLab CVE-2023-2825.

The two functions exposed here are the channel-adapter contract; the
food_scanner skill consumes ``conversation.last_photo_bytes`` set by
the handler via this module. See
``apps/skills/food_scanner/skill.py:_extract_photo_bytes``.

### Logging note

We deliberately never log the raw URL or its querystring. MAX CDN
URLs commonly include signed query parameters (auth tokens, expiry,
HMAC). Logging even a 60-char prefix risks token exfiltration via any
log sink (Sentry, Loki, CloudWatch). Log lines carry only the
hostname (or «redacted» when the URL is malformed) — enough to
correlate with operator alerts without leaking the photo's bearer.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Any, Final
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


# Match Ayla's serializer cap exactly. The legacy aborted the stream
# mid-flight when the cumulative chunk size crossed this; we preserve
# that behaviour so an attacker can't waste a worker's memory on a
# 1 GiB payload before the size check fires.
MAX_PHOTO_BYTES: Final[int] = 10 * 1024 * 1024  # 10 MiB

# Tight bound. MAX CDN photos are typically 200-800 KB and ship in
# under a second; 8 s is generous headroom for a slow Penza mobile
# connection. The worker is sync — a longer cap would pin the thread
# under a degraded CDN, which is worse for throughput than a fast fail
# that the user can retry on with a fresh photo.
PHOTO_DOWNLOAD_TIMEOUT_S: Final[float] = 8.0

# Allow-list of URL schemes. HTTPS only — http:// would permit
# downgrade-to-internal-network attacks and exposes the bearer token
# (if any) in plaintext.
_ALLOWED_SCHEMES: Final[frozenset[str]] = frozenset({"https"})


class PhotoTooLargeError(Exception):
    """Payload exceeded :data:`MAX_PHOTO_BYTES` mid-stream.

    Handler maps this to a Russian-language «фото слишком большое» reply
    via the food_scanner graceful path.
    """


class PhotoDownloadError(Exception):
    """Timeout, network error, 4xx, or 5xx from the MAX CDN.

    Handler maps this to a Russian-language «не получилось загрузить»
    reply. NOT raised on ``PhotoTooLargeError`` — those have their own
    user-facing copy. Also raised by :func:`_validate_cdn_url` for
    SSRF-class rejects (private IP, non-https scheme, malformed URL).
    """


def extract_first_photo_url(attachments: list[dict[str, Any]] | None) -> str | None:
    """Return the URL of the first IMAGE attachment, or None.

    Tolerant of mixed-type attachment lists — a message that carries a
    video AND an image picks the image. Tolerant of malformed entries
    (missing ``payload`` / non-string ``url``) — skips and continues to
    the next attachment. Returns None on empty/None input, or when no
    IMAGE is present.

    Does NOT validate the URL — that happens in :func:`download_photo`
    so the extractor stays a pure parser and the download path is the
    single SSRF chokepoint.
    """
    if not attachments:
        return None
    for att in attachments:
        if not isinstance(att, dict):
            continue
        if att.get("type") != "image":
            continue
        payload = att.get("payload")
        if not isinstance(payload, dict):
            continue
        url = payload.get("url")
        if isinstance(url, str) and url:
            return url
    return None


def safe_hostname(url: str) -> str:
    """Extract the hostname for log lines — never the path/querystring.

    Returns ``"<redacted>"`` when the URL is malformed enough that even
    the hostname can't be parsed. Callers must NEVER log the raw URL.

    Public (no leading underscore) because the handler also needs it
    for its own log lines around the photo block.
    """
    try:
        host = urlparse(url).hostname
    except Exception:  # noqa: BLE001 — log helper must not raise
        host = None
    return host or "<redacted>"


def _validate_cdn_url(url: str) -> None:
    """SSRF gate — reject anything that isn't ``https://`` to a public IP.

    Rejection rules (any failure → :class:`PhotoDownloadError`):

    * Malformed URL or empty host.
    * Scheme not in :data:`_ALLOWED_SCHEMES` (currently just ``https``).
      Catches ``http://``, ``file://``, ``ftp://``, ``gopher://``,
      ``data:``, etc.
    * Hostname is a special name we know is unsafe (``localhost``,
      ``metadata.google.internal``, ``metadata``).
    * Hostname is a literal IP address that's private, loopback,
      link-local, multicast, reserved, or unspecified.
    * Hostname resolves to one or more IPs, and **any** of them is
      private/loopback/link-local/multicast/reserved/unspecified.

    The «any unsafe» rule is deliberately strict — an attacker who
    can control a public DNS name can return mixed records (one
    public, one ``127.0.0.1``). httpx's connector picks one at its
    discretion; we'd lose. So we reject the whole hostname if any
    record is unsafe.

    DNS-rebind window remains: we resolve once for validation, then
    httpx resolves again on connect. An attacker-controlled DNS server
    that flips the answer between the two calls could still smuggle a
    private IP through. Full mitigation requires resolving once + locking
    the connection to that IP + passing the original Host header —
    deferred to a post-pilot follow-up (see PR #893 body).
    """
    if not isinstance(url, str) or not url:
        raise PhotoDownloadError("invalid url: empty or non-string")

    try:
        parsed = urlparse(url)
    except Exception as exc:  # noqa: BLE001 — defensive boundary
        raise PhotoDownloadError(f"invalid url: parse_failed {type(exc).__name__}") from exc

    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        # Don't echo the scheme on dangerous values (`file`, `gopher`) —
        # the hostname is the only safe field to log.
        raise PhotoDownloadError(f"scheme not allowed: {scheme or '<empty>'}")

    host = (parsed.hostname or "").lower()
    if not host:
        raise PhotoDownloadError("invalid url: empty host")

    # Strip trailing dot — `localhost.` is FQDN form, resolves
    # identically to `localhost` via getaddrinfo on most resolvers
    # but sails past an exact-match set lookup. B3 from PR #893
    # adversarial re-review.
    host = host.rstrip(".")

    # Special-name blocklist. These are NOT IP literals, so the
    # ipaddress check below would miss them; resolution might land
    # them on 127.0.0.1 (localhost) but DNS could be tricked.
    if host in {"localhost", "ip6-localhost", "metadata", "metadata.google.internal"}:
        raise PhotoDownloadError(f"host not allowed: {host}")

    # If the host is itself an IP literal, check it directly without
    # going through DNS — saves a round trip and avoids the rebind
    # window for the common attacker case of `http://169.254.169.254/`.
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        ip = None

    def _ip_is_unsafe(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_multicast
            or addr.is_reserved
            or addr.is_unspecified
        )

    if ip is not None:
        if _ip_is_unsafe(ip):
            raise PhotoDownloadError("host resolves to unsafe ip")
        return  # IP literal, public — done.

    # Hostname → resolve all records. AF_UNSPEC returns both A and
    # AAAA. Use type=SOCK_STREAM to dedupe entries that differ only by
    # protocol — we don't care about UDP/TCP variants, just unique IPs.
    try:
        addrinfo = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        # Can't resolve — let httpx surface the error; this is a
        # connectivity issue, not a security one. Reject cleanly so
        # the handler doesn't even open a socket.
        raise PhotoDownloadError(f"dns: {exc.strerror or 'lookup_failed'}") from exc

    for entry in addrinfo:
        try:
            sockaddr = entry[4]
            addr_str = sockaddr[0]
            resolved = ipaddress.ip_address(addr_str)
        except (ValueError, IndexError):
            # Malformed addrinfo entry — treat as unsafe.
            raise PhotoDownloadError("dns: malformed addrinfo entry")
        if _ip_is_unsafe(resolved):
            raise PhotoDownloadError("host resolves to unsafe ip")


def download_photo(url: str) -> bytes:
    """Stream-download ``url`` with SSRF validation + size cap + timeout.

    Streams via ``httpx.Client.stream`` so the bytes cap fires as soon
    as the cumulative payload crosses :data:`MAX_PHOTO_BYTES` — we
    never materialise a >10 MiB body in memory, even if the CDN
    advertises a larger ``Content-Length``.

    SSRF defence: every URL passes :func:`_validate_cdn_url` BEFORE any
    socket is opened. A rejected URL raises :class:`PhotoDownloadError`
    immediately; ``httpx.Client`` is never instantiated.

    Redirects are explicitly disabled (``follow_redirects=False``) —
    httpx 0.28's default is already False, but we pass it explicitly
    so a future default flip can't silently turn the CDN into a
    redirect-based SSRF hop.

    Raises:
      :class:`PhotoTooLargeError`: cumulative chunks > 10 MiB.
      :class:`PhotoDownloadError`: SSRF reject, timeout, network error,
                                   4xx, 5xx, or any unexpected httpx error.

    Returns:
      Raw bytes of the image. Caller stashes on
      ``conversation.last_photo_bytes`` for the food_scanner skill.
    """
    # SSRF gate. Raises PhotoDownloadError on any reject — no socket
    # opened, no httpx instantiation. The validator is the single
    # SSRF chokepoint; extract_first_photo_url stays a pure parser.
    _validate_cdn_url(url)

    safe_host = safe_hostname(url)

    try:
        with httpx.Client(
            timeout=PHOTO_DOWNLOAD_TIMEOUT_S,
            follow_redirects=False,
        ) as http:
            with http.stream("GET", url) as resp:
                if resp.status_code >= 500:
                    logger.warning(
                        "max.photo.cdn_5xx host=%s status=%d",
                        safe_host,
                        resp.status_code,
                    )
                    raise PhotoDownloadError(f"cdn 5xx: HTTP {resp.status_code}")
                if resp.status_code >= 400:
                    logger.warning(
                        "max.photo.cdn_4xx host=%s status=%d",
                        safe_host,
                        resp.status_code,
                    )
                    raise PhotoDownloadError(f"cdn 4xx: HTTP {resp.status_code}")

                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > MAX_PHOTO_BYTES:
                        # Abort the stream — close the connection before
                        # the CDN keeps shipping bytes we'll discard.
                        # `httpx.Client.stream` closes the underlying
                        # connection when the context manager exits.
                        raise PhotoTooLargeError(f"photo > {MAX_PHOTO_BYTES} bytes")
                    chunks.append(chunk)
                return b"".join(chunks)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        logger.warning(
            "max.photo.network_failure host=%s exc=%s",
            safe_host,
            type(exc).__name__,
        )
        raise PhotoDownloadError(f"network: {type(exc).__name__}") from exc
    except PhotoTooLargeError:
        # Already a typed exit — re-raise unchanged so the handler's
        # branch logic can give it a dedicated user copy.
        raise
    except PhotoDownloadError:
        # 4xx/5xx already raised above; re-raise to skip the catch-all.
        raise
    except Exception as exc:  # noqa: BLE001 — defensive boundary
        # Catch-all for anything the httpx layer might surface (e.g.
        # `httpx.InvalidURL`, `httpx.UnsupportedProtocol`, decoder
        # errors). Photo download MUST NOT crash the dispatcher; wrap
        # so the handler's PhotoDownloadError branch fires.
        logger.warning(
            "max.photo.unexpected_failure host=%s exc=%s",
            safe_host,
            type(exc).__name__,
        )
        raise PhotoDownloadError(f"unexpected: {type(exc).__name__}") from exc
