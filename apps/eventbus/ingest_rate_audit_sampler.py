"""Audit sampling for the rate-limited 429 + saturation 503 paths.

Round-2 AS3 + Round-3 NEW-2/NEW-3 + Round-4 R3-3. Without sampling,
every over-limit request writes an AuditLog row → unauthenticated
DoS amplifier on the audit table.

Round-4 amendments:

* **R3-3** — locmem fallback uses an :class:`OrderedDict` with
  bounded capacity + ``popitem(last=False)`` for oldest-eviction
  on every insert past the cap. Previous round-3 implementation
  swept by cutoff at 10000 entries — unbounded between sweeps under
  distributed attack + Redis outage. Bounded OrderedDict caps
  memory deterministically.

Round-3 amendments (preserved):

* **NEW-2** — sampler fail-CLOSED on cache outage (Redis down) with
  layered locmem fallback before failing closed.
* **NEW-3** — distinct cache key per audit slug (429 vs saturated
  vs tenant_fail_open).

### Two-layer fallback rationale

Django cache (Redis in prod) provides cross-process per-IP
sampling. If Redis is unavailable, locmem (per-process Python
OrderedDict) bounds amplification at 1 audit / minute / process
/ key. With N gunicorn workers, that's N audits / minute / key —
still bounded.

If BOTH fail (unlikely — locmem is pure Python), fail-CLOSED.
Losing forensics is the right tradeoff against keeping AS3
amplification alive.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Final

from django.core.cache import cache


logger = logging.getLogger(__name__)


# Round-2 AS3 — 60s window per source IP per slug.
_SAMPLE_WINDOW_S: Final[int] = 60

# Round-3 NEW-3 — distinct prefix per audit slug.
_KEY_PREFIX_FMT: Final[str] = "eventbus:ingest:rate_audit:{slug}:"

# Round-4 R3-3 — hard cap on the per-process locmem fallback. Under
# distributed attack at line speed, the previous «sweep at 10000»
# was unbounded between sweeps. OrderedDict with popitem(last=False)
# evicts oldest entry on every insert past the cap → deterministic
# memory ceiling regardless of attack volume.
_LOCMEM_MAX_ENTRIES: Final[int] = 10_000

_locmem_lock = threading.Lock()
# OrderedDict so popitem(last=False) evicts oldest (FIFO insertion).
# Re-inserting a key (move_to_end) bumps it to «newest» on every
# admit — frequently-seen keys stay; cold keys age out.
_locmem_seen: OrderedDict[tuple[str, str], float] = OrderedDict()


def _locmem_admit(slug: str, remote_ip: str, now: float) -> bool:
    """Per-process fallback when the Django cache backend errors.

    Returns True iff this is the first admission for ``(slug, key)``
    within the window.

    Round-4 R3-3: OrderedDict + popitem(last=False) caps memory at
    :data:`_LOCMEM_MAX_ENTRIES` regardless of attack diversity.
    """
    key = (slug, remote_ip)
    with _locmem_lock:
        last = _locmem_seen.get(key, 0.0)
        if now - last < _SAMPLE_WINDOW_S:
            # Recent — re-insert to bump «freshness» so this key
            # doesn't get evicted while still being actively
            # suppressed.
            _locmem_seen.move_to_end(key)
            return False
        _locmem_seen[key] = now
        _locmem_seen.move_to_end(key)
        # Oldest-eviction past the cap.
        while len(_locmem_seen) > _LOCMEM_MAX_ENTRIES:
            _locmem_seen.popitem(last=False)
        return True


def _should_audit(slug: str, remote_ip: str) -> bool:
    """Shared sampler — slug-distinct cache keys + locmem fallback.

    Returns True iff the audit row should be written.
    """
    cache_key = _KEY_PREFIX_FMT.format(slug=slug) + (remote_ip or "_unknown_")
    try:
        admitted = cache.add(cache_key, "1", timeout=_SAMPLE_WINDOW_S)
    except Exception:  # noqa: BLE001 — cache backend failure
        # Round-3 NEW-2: locmem fallback.
        logger.warning(
            "eventbus.ingest.rate_audit_sampler.cache_failed slug=%s falling_back_to_locmem",
            slug,
        )
        return _locmem_admit(slug, remote_ip or "_unknown_", time.time())
    return bool(admitted)


def should_audit_rate_limited(remote_ip: str) -> bool:
    """Round-3 NEW-3 — slug='429'."""
    return _should_audit("429", remote_ip)


def should_audit_saturated(remote_ip: str) -> bool:
    """Round-3 NEW-3 — slug='saturated'."""
    return _should_audit("saturated", remote_ip)


# Round-4 R3-2 — threshold-emit ladder. The previous round-3 "sampled
# at 1/min/composite" defeated the forensic purpose: emit happened on
# call #1 with count_in_window=1, the next 999 calls only incremented
# the counter but never emitted a row carrying that count. The audit
# trail under-reported true volume by 99.9%.
#
# This ladder emits on a logarithmic-ish schedule so every order-of-
# magnitude of attack volume produces an audit row. The row's
# count_in_window field carries the exact count at emit time, so
# operators reading the audit trail see the volume curve directly.
#
# Volume bound: at N total fall-throughs per (user_id, tenant_id),
# the ladder emits ~10 rows up to N=10000, then ~N/1000 rows past
# that — far better than 1/min/composite while never dropping volume
# signal.
_EMIT_THRESHOLDS: Final[frozenset[int]] = frozenset({1, 10, 50, 100, 500, 1000, 5000, 10000})


def should_emit_tenant_fail_open_audit(count: int) -> bool:
    """Return True iff an audit row should be emitted at ``count``.

    The audit row's ``count_in_window`` carries ``count`` as the
    forensic snapshot. Subsequent calls between thresholds increment
    the counter but don't emit until the next threshold is crossed.
    """
    if count in _EMIT_THRESHOLDS:
        return True
    # After 10k, emit every additional 10k → bounded linear growth.
    return count > 10_000 and count % 10_000 == 0


# ─── R3-2 — Forensic count for sampled tenant-fail-open audits ──────────


_TENANT_FAIL_OPEN_COUNTER_PREFIX: Final[str] = "eventbus:ingest:tenant_fail_open_count:"


def increment_tenant_fail_open_count(composite_key: str) -> int:
    """Atomic-ish increment of the per-window counter; return the new value.

    Round-4 R3-2 — the round-3 NEW-5 sampler suppressed 999/1000
    fall-throughs from a single attacker. The ladder now emits one
    row per threshold crossing — 1000 events produce 6 rows
    {1, 10, 50, 100, 500, 1000} with the LAST row carrying
    ``count_in_window=1000``. Operators read the volume curve from
    the row sequence (count_in_window field IS the snapshot at emit
    time; the counter does NOT reset between thresholds — it
    decays only via the cache TTL after 90s of inactivity).

    Cache is Redis in prod; ``incr`` is atomic across workers. On
    cache failure we return 1 (caller still writes the row with at
    least the lower bound).
    """
    key = _TENANT_FAIL_OPEN_COUNTER_PREFIX + composite_key
    try:
        # add() returns True if the key was set (first hit); else
        # incr returns the new value. The (window + 30s grace) TTL
        # gives the sampler-window timer headroom over the counter
        # itself so a single window's count doesn't get clipped by
        # premature key expiry.
        if cache.add(key, 1, timeout=_SAMPLE_WINDOW_S + 30):
            return 1
        try:
            return int(cache.incr(key))
        except Exception:  # noqa: BLE001 — backend may not support incr
            return 1
    except Exception:  # noqa: BLE001 — cache backend failure
        logger.warning(
            "eventbus.ingest.tenant_fail_open_counter.cache_failed key=%s",
            composite_key,
        )
        return 1


# read_and_reset_tenant_fail_open_count was removed (zero callers).
# Counter decays via the cache TTL (window + 30s grace). If a future
# round wants explicit per-window reset semantics, that's a follow-up
# (see GH issue tracking R4-COMP-2).
