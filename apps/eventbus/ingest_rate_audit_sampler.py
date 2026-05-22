"""Audit sampling for the rate-limited 429 + saturation 503 paths.

Round-2 AS3 + Round-3 NEW-2 + NEW-3. Without sampling, every
over-limit request writes an AuditLog row → unauthenticated DoS
amplifier on the audit table.

Round-3 amendments:

* **NEW-2** — sampler fail-CLOSED on cache outage (Redis down). The
  Round-2 fail-OPEN behaviour revived the AS3 amplifier during a
  Redis incident. New layered fallback: Django cache → per-process
  locmem dict → fail-CLOSED.
* **NEW-3** — distinct cache key per audit slug. SATURATED + 429
  audits previously shared ``rate_audit:{ip}`` → one suppressed the
  other for the same IP. Now: ``rate_audit_429:{ip}`` vs
  ``rate_audit_saturated:{ip}``.

### Two-layer fallback rationale

Django cache (Redis in prod) provides cross-process per-IP
sampling — the desired behaviour. If Redis is unavailable, locmem
(per-process Python dict with timestamp-keyed eviction) bounds
amplification at 1 audit / minute / process / IP. With N gunicorn
workers, that's N audits / minute / IP — still bounded, several
orders of magnitude better than line-speed.

If BOTH fail (unlikely — locmem is pure Python), fail-CLOSED.
Losing forensics is the right tradeoff against keeping AS3
amplification alive.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Final

from django.core.cache import cache


logger = logging.getLogger(__name__)


# Round-2 AS3 — 60s window per source IP per slug.
_SAMPLE_WINDOW_S: Final[int] = 60

# Round-3 NEW-3 — distinct prefix per audit slug. The slug parameter
# names which audit family is being sampled (429 vs saturation).
_KEY_PREFIX_FMT: Final[str] = "eventbus:ingest:rate_audit:{slug}:"

# Round-3 NEW-2 — per-process locmem fallback. Keyed by
# (slug, remote_ip); value is the unix epoch second when that key
# was last admitted. Locked because gunicorn-style multi-worker
# deploys with thread pool inside also exercise this from multiple
# threads.
_locmem_lock = threading.Lock()
_locmem_seen: dict[tuple[str, str], float] = {}


def _locmem_admit(slug: str, remote_ip: str, now: float) -> bool:
    """Per-process fallback when the Django cache backend errors.

    Returns True iff this is the first admission for ``(slug, ip)``
    within the window — bounds 1 / process / window / IP / slug.
    """
    key = (slug, remote_ip)
    with _locmem_lock:
        last = _locmem_seen.get(key, 0.0)
        if now - last < _SAMPLE_WINDOW_S:
            return False
        _locmem_seen[key] = now
        # Opportunistic GC — drop stale entries once we cross 10000.
        if len(_locmem_seen) > 10000:
            cutoff = now - _SAMPLE_WINDOW_S
            stale = [k for k, ts in _locmem_seen.items() if ts < cutoff]
            for k in stale:
                _locmem_seen.pop(k, None)
        return True


def _should_audit(slug: str, remote_ip: str) -> bool:
    """Shared sampler — slug-distinct cache keys + locmem fallback.

    Returns True iff the audit row should be written.
    """
    cache_key = _KEY_PREFIX_FMT.format(slug=slug) + (remote_ip or "_unknown_")
    try:
        admitted = cache.add(cache_key, "1", timeout=_SAMPLE_WINDOW_S)
    except Exception:  # noqa: BLE001 — cache backend failure
        # Round-3 NEW-2: locmem fallback. Fail-CLOSED on the cache
        # exception alone would lose ALL audit forensics during a
        # Redis incident; per-process locmem keeps a bounded trace.
        logger.warning(
            "eventbus.ingest.rate_audit_sampler.cache_failed slug=%s falling_back_to_locmem",
            slug,
        )
        return _locmem_admit(slug, remote_ip or "_unknown_", time.time())
    return bool(admitted)


def should_audit_rate_limited(remote_ip: str) -> bool:
    """Return True iff the 429-rate-limited audit row should be written.

    Round-3 NEW-3 — distinct cache prefix from SATURATED.
    """
    return _should_audit("429", remote_ip)


def should_audit_saturated(remote_ip: str) -> bool:
    """Return True iff the SATURATED audit row should be written.

    Round-3 NEW-3 — distinct cache prefix from 429.
    """
    return _should_audit("saturated", remote_ip)


def should_audit_tenant_fail_open(remote_ip: str) -> bool:
    """Return True iff the tenant-verify-fail-open audit row should be written.

    Round-3 NEW-5 — every fall-through into the documented
    pre-#246 opt-in path emits an audit row (sampled). Distinct
    slug so it doesn't share cache state with rate / saturation.
    """
    return _should_audit("tenant_fail_open", remote_ip)
