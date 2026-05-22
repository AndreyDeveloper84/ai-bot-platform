"""Per-handler timeout enforcement — `event-contract.md` §8.10.

PR #507 adversarial pass A12 + Round-2 amendments AS5/AS6/AS11.
The contract gives Ayla 10 seconds for an ingest response; this
module enforces an 8s per-handler budget so we always leave 2
seconds of headroom for HTTP return / network transit.

### Why this MUST land before #442 (first real handler)

The first consumer (booking.* family, #442) will call Ayla REST
inline. A slow / hung Ayla → handler blocks → worker thread held
indefinitely → single bad event can stop ALL ingestion. The §8.10
budget caps how long any one event can hog a worker.

### Implementation: ThreadPoolExecutor + bounded semaphore

ThreadPoolExecutor's internal queue is unbounded — saturation (e.g.
slow-Ayla incident affecting all in-flight events) would let
``submit()`` keep accepting work that never gets a worker (Round-2
AS6 thread-bomb). We wrap submit with a bounded :class:`Semaphore`
that bounds **in-flight** work (including timed-out orphans
holding DB connections).

When the semaphore would block (saturated), we DON'T queue — we
return ``DispatchOutcome.SATURATED`` so the view returns **503 +
Retry-After**. Ayla treats this as retryable per §6.3 and backs
off without amplifying the cascade.

### Why semaphore released on done-callback, not on result()

A timed-out handler's orphan thread keeps running (Python can't
safely terminate a thread). It still holds a DB connection until
the underlying HTTP / DB-statement timeout fires. If we released
the semaphore on ``result()`` timeout, the next request would
submit a NEW thread while the orphan still holds a connection —
amplifying DB-pool exhaustion (Round-2 AS5).

Releasing via ``future.add_done_callback`` defers the slot return
until the orphan ACTUALLY completes. In-flight count is the real
slot occupancy, including hangs.

### Defense in depth — handlers MUST set tight outbound timeouts

Every consumer handler MUST set outbound HTTP timeout ≤5s on Ayla
REST calls — orphan exits on its own before the request's
worst-case matters. PR template Security checklist enforces this.

### Operator capacity planning

Set Django ``DATABASES['default']['CONN_MAX_AGE']`` + the DB pool
size so that ``pool_size ≥ MAX_INFLIGHT × 2``. The factor of 2
absorbs the orphan-tail (worst case every slot is a hung orphan +
the OK path needs a fresh connection). Without that headroom,
chat/RAG/miniapp workers will be starved during a slow-Ayla
incident.
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading
from typing import Final

from apps.eventbus.ingest_dispatcher import (
    DispatchOutcome,
    DispatchResult,
    dispatch_envelope,
)
from apps.eventbus.ingest_envelope import IngestEnvelope


logger = logging.getLogger(__name__)


# `event-contract.md` §8.10 — 8s budget.
DEFAULT_HANDLER_TIMEOUT_S: Final[float] = 8.0


# Cap on concurrent in-flight handlers (Round-2 AS5/AS6). Each
# in-flight slot may hold a Django DB connection for the orphan-tail
# duration. Operators MUST size DB pool ≥ 2 × MAX_INFLIGHT (see module
# docstring).
_MAX_INFLIGHT: Final[int] = 32


# Module-level executor — context-manager form's __exit__ calls
# shutdown(wait=True), blocking on orphans + defeating the timeout.
_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=_MAX_INFLIGHT,
    thread_name_prefix="eventbus-ingest",
)


# Semaphore bounds CONCURRENT in-flight work including timed-out
# orphans. Released via future.add_done_callback so the slot returns
# only when the underlying thread actually completes — not when we
# time out. See module docstring §«Why semaphore released on
# done-callback».
_inflight = threading.Semaphore(value=_MAX_INFLIGHT)


def dispatch_with_timeout(
    envelope: IngestEnvelope,
    *,
    timeout_s: float = DEFAULT_HANDLER_TIMEOUT_S,
) -> DispatchResult:
    """Run :func:`dispatch_envelope` with the 8s per-handler cap.

    Returns:
      - ``DispatchResult(OK | DUPLICATE | UNKNOWN_* | HANDLER_EXCEPTION)``
        — passthrough from the inner dispatcher.
      - ``DispatchResult(HANDLER_EXCEPTION, TimeoutError)`` — handler
        exceeded ``timeout_s``. Orphan thread continues independently.
      - ``DispatchResult(SATURATED)`` — in-flight semaphore exhausted;
        view returns 503 + Retry-After per §6.3.
    """
    if not _inflight.acquire(blocking=False):
        # AS5/AS6 — bounded in-flight is exhausted. Reject fast so
        # Ayla's outbox queues this event for retry instead of
        # piling up unbounded work behind us.
        logger.warning(
            "eventbus.ingest.saturated event_id=%s name=%s version=%d max_inflight=%d",
            envelope.event_id,
            envelope.event_name,
            envelope.event_version,
            _MAX_INFLIGHT,
        )
        return DispatchResult(outcome=DispatchOutcome.SATURATED)

    future = _executor.submit(dispatch_envelope, envelope)
    # Release the slot only when the thread ACTUALLY finishes —
    # including the orphan-tail past our timeout. This is the
    # difference between «in-flight count» and «futures we're
    # awaiting»: we await for ``timeout_s``; in-flight runs longer.
    future.add_done_callback(lambda _f: _inflight.release())

    try:
        return future.result(timeout=timeout_s)
    except concurrent.futures.TimeoutError:
        logger.warning(
            "eventbus.ingest.handler_timeout event_id=%s name=%s version=%d budget_s=%.1f",
            envelope.event_id,
            envelope.event_name,
            envelope.event_version,
            timeout_s,
        )
        # Orphan thread continues + holds the in-flight slot until
        # its own work completes. Do NOT release here — that would
        # let a new submit race the orphan's DB connection.
        return DispatchResult(
            outcome=DispatchOutcome.HANDLER_EXCEPTION,
            exception=TimeoutError(
                f"handler exceeded {timeout_s}s budget per event-contract.md §8.10"
            ),
        )


def inflight_count() -> int:
    """How many slots are currently held (in-flight + orphan-tail).

    Test + observability hook. ``_MAX_INFLIGHT - inflight_count()``
    is the available capacity. Internals use the semaphore's value
    attribute which is CPython-implementation-detail; in production
    we'd expose this via a Prometheus gauge wired in separately.
    """
    # threading.Semaphore exposes _value on CPython — fine for tests
    # + an internal metric source. If this breaks on a future CPython
    # release we can swap to a manual atomic counter.
    return _MAX_INFLIGHT - _inflight._value  # type: ignore[attr-defined]
