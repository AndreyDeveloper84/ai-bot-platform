"""Process warm-up for the vendor SDK clients (DRF-1445).

### What this pays for, measured on the pilot

The owner's first message after a worker restart came back in 57 s;
every later message in the same process came back in ~3-7 s. The 54 s
lived inside ONE honest model call - no retries, no errors. Network,
proxy, database and request size were each excluded by measurement
(see the ticket); what was left was the cost of the FIRST call in a
fresh process.

Broken down inside the pilot's own worker container, 2026-09-03::

    import anthropic                31.1 s   (cold page cache)
    import anthropic                 4.8 s   (same files, next process)
    import anthropic                 3.5 s   (third)
    import httpx (httpx2)            1.1 s
    ssl.create_default_context()     1.0 s   (certifi CA bundle off disk)
    DefaultAsyncHttpxClient(proxy=)  2.8 s
    AsyncAnthropic(...)              0.0 s
    messages.create #1               1.7 s
    messages.create #2               0.7 s

So the minute is **module import and client construction**, not the
vendor. ``AnthropicProvider._get_client()`` does both lazily, on the
first ``complete()`` - i.e. on a human's turn. This module moves that
work to process start, onto a background thread.

Paired before/after in that same container, page cache dropped for the
vendor packages first (``posix_fadvise(POSIX_FADV_DONTNEED)``) so both
runs start equally cold::

    before   first complete() ON THE TURN     484.4 s   then 0.76 s
    after    warm-up OFF the turn             464.1 s
             first complete() ON THE TURN       1.8 s   then 0.76 s

The steady state is identical either way (0.76 s) - warming buys the
first turn and costs the rest nothing.

### No vendor call

Warming deliberately stops at ``_get_client()``. A ``sys.modules`` diff
across the first request shows it imports exactly two modules
(``anyio._backends._asyncio``) and costs ~1 s more than a subsequent
one - so a warm-up ping would buy about a second and would spend a real
request at every restart. Everything expensive is local, so we warm
entirely locally.

### Why this is a splint, not a cure

Eight minutes to read an already-installed Python package off disk is a
host symptom, not a code one. The same machine shows ``r_await`` 174 ms
/ ``w_await`` 313 ms, ``%util`` 84, load average 11.9 on 2 CPUs, and
**1963 MB of 2047 MB swap in use**. Memory pressure evicts the page
cache, so every "first" read of a library is a physical read from a
saturated disk - which is also why the same import measured 31.1 s at
05:29 and 464 s at 06:10.

Warm-up moves that cost off the user's turn. It does not make the
machine faster, it cannot help whatever a cold process nobody warmed
reaches for next, and it will not survive a host this loaded getting
any busier. The cure is a machine with RAM enough to hold its own page
cache. See DRF-1437 / DRF-1445.

### Contract

* **Never blocks readiness.** :func:`start_background_warmup` returns
  immediately; the work runs on a daemon thread. A worker that has not
  finished warming still consumes messages - it just pays the old price
  for the first one. Trading a slow answer for a lost message would be
  a worse bug than the one we are fixing.
* **Never fails loudly.** Every failure path is logged and swallowed. A
  missing SDK, an unset key or a dead proxy must not stop a process
  from booting.
* **Idempotent.** Warming twice is a no-op: providers cache their
  client, the router caches the provider, and a module guard stops a
  second thread.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


# Module-level guard so two call sites in one process (say a future ASGI
# hook alongside the consumer's) cannot start two threads racing to
# build the same client.
_lock = threading.Lock()
_started = False


def warmup_provider_names() -> list[str]:
    """Vendors this process is actually configured to call.

    Order: ``LLM_WARMUP_PROVIDERS`` when an operator pinned one,
    otherwise the router's own resolution inputs - the org-wide
    ``LLM_PROVIDER`` plus every value in ``SKILL_LLM_PROVIDER``.

    The per-TENANT tier (``Tenant.features["llm_provider"]``) is
    deliberately NOT consulted: reading it means a database query at
    boot, and a tenant override is by definition the canary case, not
    the one that paid the pilot's minute.

    Vendors without a configured API key are dropped - building a client
    we can never use spends the import for nothing.
    """
    from apps.llm.router import provider_is_configured, registered_provider_names

    known = set(registered_provider_names())

    pinned = [
        str(name).strip()
        for name in (getattr(settings, "LLM_WARMUP_PROVIDERS", []) or [])
        if str(name).strip()
    ]
    if pinned:
        wanted: list[str] = pinned
    else:
        wanted = [str(getattr(settings, "LLM_PROVIDER", "") or "")]
        skill_map = getattr(settings, "SKILL_LLM_PROVIDER", {}) or {}
        wanted += [value for value in skill_map.values() if isinstance(value, str)]

    chosen: list[str] = []
    for name in wanted:
        if not name or name in chosen:
            continue
        if name not in known:
            logger.warning("llm.warmup.unknown_provider name=%r ignored", name)
            continue
        if not provider_is_configured(name):
            logger.info("llm.warmup.skipped provider=%s reason=no_api_key", name)
            continue
        chosen.append(name)
    return chosen


def warm_llm_clients() -> dict[str, float]:
    """Import each configured vendor SDK and build its HTTP client.

    Synchronous and network-free. Returns ``{provider_name: seconds}``
    for the vendors that warmed, so the caller - and the tests - can see
    what the process actually paid.

    Goes through :meth:`apps.llm.router.LLMRouter.preload` rather than
    constructing a provider of its own, because the router caches
    providers per process: warming a private instance would build a
    client the serving path never sees, and the human would still pay
    the import on their turn.
    """
    from apps.llm.router import get_router

    router = get_router()
    timings: dict[str, float] = {}

    for name in warmup_provider_names():
        started = time.monotonic()
        try:
            provider: Any = router.preload(name)
            warm = getattr(provider, "warm_up", None)
            if warm is None:
                # A provider without the hook is not an error - it is a
                # provider whose client is not expensive enough to have
                # one, or a test double.
                logger.info("llm.warmup.no_hook provider=%s", name)
                continue
            warm()
        except Exception:  # noqa: BLE001 - warm-up must never break boot
            logger.warning(
                "llm.warmup.failed provider=%s elapsed=%.2fs - process continues cold",
                name,
                time.monotonic() - started,
                exc_info=True,
            )
            continue
        elapsed = time.monotonic() - started
        timings[name] = elapsed
        logger.info("llm.warmup.ready provider=%s elapsed=%.2fs", name, elapsed)

    return timings


def _run_warmup() -> None:
    """Thread body. Swallows everything - see the module contract."""
    started = time.monotonic()
    try:
        timings = warm_llm_clients()
    except Exception:  # noqa: BLE001 - belt and braces around the loop's own guard
        logger.warning("llm.warmup.aborted", exc_info=True)
        return
    logger.info(
        "llm.warmup.done providers=%s total=%.2fs",
        ",".join(timings) or "none",
        time.monotonic() - started,
    )


def start_background_warmup() -> threading.Thread | None:
    """Kick the warm-up off on a daemon thread and return at once.

    Returns the thread, or ``None`` when warm-up is disabled or already
    running in this process.

    The thread is a daemon so a ``SIGINT`` during warm-up still exits
    promptly: the whole point is that nothing waits on this work.
    """
    if not getattr(settings, "LLM_WARMUP_ENABLED", True):
        logger.info("llm.warmup.disabled reason=LLM_WARMUP_ENABLED=False")
        return None

    global _started
    with _lock:
        if _started:
            logger.debug("llm.warmup.already_started")
            return None
        _started = True

    thread = threading.Thread(target=_run_warmup, name="llm-warmup", daemon=True)
    thread.start()
    logger.info("llm.warmup.started thread=%s", thread.name)
    return thread


def reset_warmup_state() -> None:
    """Test helper - clears the once-per-process guard.

    Production code never calls this (mirror of
    :func:`apps.llm.router.reset_router_cache`).
    """
    global _started
    with _lock:
        _started = False
