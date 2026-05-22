"""Per-handler timeout enforcement — `event-contract.md` §8.10.

PR #507 adversarial pass A12. The contract gives Ayla 10 seconds to
get a response from the ingest endpoint; this module enforces an 8s
per-handler budget so we always leave 2 seconds of headroom for HTTP
return / network transit.

### Why this MUST land before #442 (first real handler)

The first consumer (booking.* family, #442) will call Ayla REST
inline (per ADR-0009 Mobile API split — booking lookup, status
fetch, etc.). A slow / hung Ayla → handler blocks → gunicorn /
ASGI worker thread held indefinitely → single bad event can stop
ALL ingestion across the deploy. The §8.10 budget is a hard upper
bound that limits how long any one event can hog a worker.

### Implementation choice — ThreadPoolExecutor

Python lacks a clean way to interrupt arbitrary sync code. The
options were:

  * ``signal.SIGALRM`` — Unix only; doesn't work on Windows or
    inside threads (signals are main-thread-only).
  * ``concurrent.futures.ThreadPoolExecutor.submit + future.result(timeout)``
    — cross-platform. We return 500 after 8s, but the orphaned
    thread KEEPS RUNNING in the background (Python can't safely
    terminate a thread). Documented limitation; see "Defense in
    depth" below.
  * ``asyncio.wait_for`` — clean cancellation but requires async
    handlers; current dispatcher is sync. A future Phase 1+ rework
    can switch to async; ThreadPoolExecutor unblocks #442 today.

### Defense in depth — handlers MUST set tight outbound timeouts

The orphan-thread limitation means a hung handler still holds a DB
connection + the dedupe row's transaction lock for the duration of
its actual hang time. **Every consumer handler MUST set an outbound
HTTP timeout ≤5s on Ayla REST calls** (well within the 8s budget) so
the orphaned thread exits on its own before the request's worst-case
matters. The PR template Security checklist enforces this.

### Outcome mapping

A handler timeout maps to :attr:`DispatchOutcome.HANDLER_EXCEPTION`
(same as a raised exception per §8.10 "treated as failure"). The
view returns 500; Ayla retries per §6.3. The audit log shows a
``TimeoutError`` exception type so ops can distinguish timeouts
from other handler failures.
"""

from __future__ import annotations

import concurrent.futures
import logging
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


# Cap on concurrent in-flight handlers. A worker thread is held for
# the duration of dispatch_envelope (DB transaction + handler body).
_MAX_WORKERS: Final[int] = 32


# Module-level executor — context-manager form calls shutdown(wait=True)
# which would block on orphaned threads, defeating the timeout. The
# module-level executor lets the timeout return immediately while the
# orphan continues independently.
_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=_MAX_WORKERS,
    thread_name_prefix="eventbus-ingest",
)


def dispatch_with_timeout(
    envelope: IngestEnvelope,
    *,
    timeout_s: float = DEFAULT_HANDLER_TIMEOUT_S,
) -> DispatchResult:
    """Run :func:`dispatch_envelope` with an 8-second per-handler cap.

    On timeout: returns ``DispatchResult(HANDLER_EXCEPTION, TimeoutError)``.
    The orphaned worker thread continues running independently — see
    the module docstring for the rationale and the defence-in-depth
    requirement on handler outbound timeouts.
    """
    future = _executor.submit(dispatch_envelope, envelope)
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
        # We do NOT cancel the future — Python can't safely terminate
        # a running thread. The orphan finishes on its own when its
        # underlying HTTP / DB statement timeout fires (handler authors
        # MUST set ≤5s outbound timeouts per the module docstring).
        return DispatchResult(
            outcome=DispatchOutcome.HANDLER_EXCEPTION,
            exception=TimeoutError(
                f"handler exceeded {timeout_s}s budget per event-contract.md §8.10"
            ),
        )
