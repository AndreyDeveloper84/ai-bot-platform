"""Client-IP resolution for the ingest endpoint — Round-2 adversarial AS1+AS2.

`REMOTE_ADDR` is the proxy's IP behind ANY reverse proxy (Cloudflare,
nginx, Fly.io edge, k8s ingress). Using it as a rate-limit key collapses
every Ayla request into a single bucket — exhausting that bucket =
unauthenticated DoS amplifier on the AuditLog (per AS3).

This module owns the proxy-aware client-IP resolution + the trust
boundary.

### Resolution order

1. ``X-Real-IP`` header — set by the trusted edge proxy. Single value.
2. ``X-Forwarded-For`` chain — depth-N walk from the right (trusted
   end). Configurable via ``settings.EVENT_INGEST_TRUSTED_PROXY_DEPTH``.
3. ``REMOTE_ADDR`` — fallback when no header. Behind a proxy this is
   the proxy itself; in a direct-deployment scenario it's the real
   client.

### Trust boundary (AS2)

The headers are client-controllable. A forged XFF gives an attacker
unbounded buckets per the AS2 bypass. The trust depth setting tells
us how many leading XFF hops to discard before taking the IP — only
trustworthy when the edge layer (Cloudflare, nginx) is configured to
reset / canonicalise the header. The startup warning
:func:`warn_if_proxy_trust_unconfigured` fires when running with a
non-default depth but no documented edge configuration.

### Why a custom function vs django-ratelimit's built-in key

django-ratelimit has ``key='header:x-real-ip'`` but doesn't expose
the trust-depth logic. We need:

* fallback chain (header → header → REMOTE_ADDR);
* explicit depth control;
* a single emit point for both rate-limit AND audit sampling so the
  two stay in sync.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from django.conf import settings


logger = logging.getLogger(__name__)


# AS2 — header chosen by canonical edge proxies (nginx ``real_ip``,
# Cloudflare, AWS ALB). Always overrides XFF when present.
_REAL_IP_HEADER_META: Final[str] = "HTTP_X_REAL_IP"
_FORWARDED_FOR_HEADER_META: Final[str] = "HTTP_X_FORWARDED_FOR"


def get_remote_ip(request: Any) -> str:
    """Return the best-available client IP for the request.

    Args:
      request: Django HttpRequest (or any object with ``.META`` dict).

    Returns:
      A non-empty IP string. Worst case ``""`` if all sources are
      missing — caller should treat empty string as "no source IP"
      and audit-as-unknown.
    """
    meta = getattr(request, "META", {}) or {}
    depth = int(getattr(settings, "EVENT_INGEST_TRUSTED_PROXY_DEPTH", 0) or 0)
    edge_ack = bool(getattr(settings, "EVENT_INGEST_EDGE_CONFIGURED_ACK", False))

    # Round-3 NEW-1 — gate X-Real-IP behind the same proxy-trust
    # requirement as X-Forwarded-For. Without this gate, the AS1
    # fix's XFF tightening leaves the OTHER header wide open: an
    # attacker rotates X-Real-IP per request → isolated buckets.
    # X-Real-IP is only meaningful when (a) a trusted edge proxy
    # canonicalises it (signalled by edge_ack=True) OR (b) we have
    # an explicit depth>0 declaring "trust the headers you set".
    real_ip = str(meta.get(_REAL_IP_HEADER_META) or "").strip()
    if real_ip and (depth > 0 or edge_ack):
        return real_ip

    xff = str(meta.get(_FORWARDED_FOR_HEADER_META) or "").strip()
    if xff and depth > 0:
        # XFF format: "client, proxy1, proxy2, ...". The trusted-proxy
        # depth is the number of trailing hops we DROP. The remaining
        # rightmost entry after the drop is the client IP.
        hops = [h.strip() for h in xff.split(",") if h.strip()]
        if len(hops) > depth:
            return hops[-(depth + 1)]
        # Depth exceeds chain length → can't trust the header at all.
        # Fall through to REMOTE_ADDR.
        logger.warning(
            "eventbus.ingest.xff_chain_shorter_than_depth depth=%d hops=%d",
            depth,
            len(hops),
        )

    return str(meta.get("REMOTE_ADDR") or "").strip()


def warn_if_proxy_trust_unconfigured() -> None:
    """Log a startup warning when proxy trust configuration is risky.

    Three risky combinations:

    1. ``EVENT_INGEST_TRUSTED_PROXY_DEPTH > 0`` without a documented
       edge-proxy configuration (the operator may forge XFF locally).
    2. Production deploy (``DEBUG=False``) with depth=0 — REMOTE_ADDR
       is the proxy's IP, single-bucket DoS surface.
    3. ``EVENT_INGEST_TRUSTED_PROXY_DEPTH`` set but
       ``EVENT_INGEST_EDGE_CONFIGURED_ACK`` not explicitly True —
       operator must acknowledge they've configured the edge proxy
       to canonicalise XFF.
    """
    depth = int(getattr(settings, "EVENT_INGEST_TRUSTED_PROXY_DEPTH", 0) or 0)
    debug = bool(getattr(settings, "DEBUG", False))
    edge_ack = bool(getattr(settings, "EVENT_INGEST_EDGE_CONFIGURED_ACK", False))

    if depth > 0 and not edge_ack:
        logger.warning(
            "eventbus.ingest.proxy_trust_risky depth=%d edge_ack=False "
            "X-Forwarded-For chains are spoofable unless the edge proxy "
            "canonicalises the header. Set "
            "EVENT_INGEST_EDGE_CONFIGURED_ACK=True after verifying "
            "nginx/Cloudflare strips inbound XFF.",
            depth,
        )

    if not debug and depth == 0:
        logger.warning(
            "eventbus.ingest.proxy_trust_unconfigured "
            "Production deploy (DEBUG=False) with "
            "EVENT_INGEST_TRUSTED_PROXY_DEPTH=0 — REMOTE_ADDR will be "
            "the reverse proxy, collapsing all Ayla traffic into one "
            "rate-limit bucket. Set the depth to the number of trusted "
            "proxy hops, OR configure the edge to set X-Real-IP."
        )
