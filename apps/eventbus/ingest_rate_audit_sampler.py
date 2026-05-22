"""Audit sampling for the rate-limited 429 path — Round-2 AS3.

Without sampling, every over-limit request writes an AuditLog row.
A scanner without HMAC at line speed = unbounded AuditLog growth =
unauthenticated DoS amplifier on the audit table.

Bound the amplification at the source-IP level: **1 audit row per
IP per minute**. The rate-limit signal itself remains accurate (the
ratelimit decorator counts every over-limit request); only the
*audit emission* is sampled.

### Cache backing

Uses Django's default cache (Redis in prod, locmem in tests). Key
shape: ``eventbus:ingest:rate_audit:<ip>``. TTL 60s. First over-
limit hit per IP per 60s window sets the key + writes audit; cache
hit during the window short-circuits the audit.

### Counter recovery

We do NOT track suppressed count in the cache because:

* The ratelimit decorator + Prometheus counters (when added) carry
  the real volume signal;
* A single audit row per minute is enough for forensic triage —
  operators read the rate-limit Grafana panel for volume, the audit
  row for the «who/what/when» of the first hit.
"""

from __future__ import annotations

import logging
from typing import Final

from django.core.cache import cache


logger = logging.getLogger(__name__)


# AS3 — 60s window per source IP. Worst-case audit volume from a
# botnet of N distinct IPs hitting at line speed: N rows/min instead
# of N × (line-speed × 60) rows/min. The audit table grows linearly
# with attack diversity, not attack volume.
_SAMPLE_WINDOW_S: Final[int] = 60
_KEY_PREFIX: Final[str] = "eventbus:ingest:rate_audit:"


def should_audit_rate_limited(remote_ip: str) -> bool:
    """Return True iff the rate-limit audit row should be written.

    First call per ``remote_ip`` per ``_SAMPLE_WINDOW_S`` returns True;
    subsequent calls within the window return False.

    Args:
      remote_ip: The (proxy-aware-resolved) client IP. Empty string
                 ``""`` is treated as a distinct «no source» bucket —
                 still bounded to 1 audit/min for unknown-source
                 traffic.

    Returns:
      bool — True if audit should be written.

    Note: this is a best-effort sampler. Under cache backend failure
    (Redis down) the cache call may raise — caller should fall back
    to writing audit (fail-open on observability per AS3 — losing
    rate-limit forensics is worse than gaining a few audit rows).
    """
    key = _KEY_PREFIX + (remote_ip or "_unknown_")
    try:
        # add() is atomic: returns True if key was absent (we win the
        # race + write audit); False if key existed (sampled out).
        added = cache.add(key, "1", timeout=_SAMPLE_WINDOW_S)
    except Exception:  # noqa: BLE001 — cache backend failure
        logger.exception("eventbus.ingest.rate_audit_sampler_failed")
        # Fail-open on observability — write the audit so we don't
        # silently lose attack forensics during a cache outage.
        return True
    return bool(added)
