"""Boot-time ``worker.subscriber_audit`` emit (issue #502).

Replaces the manual ``grep -rn 'class.*TenantAwareTask' apps/`` step
in the ``STRICT_TENANT_REFUSE`` pre-flip checklist with a programmatic
live inventory query against the audit table.

Acceptance (tech lead 2026-05-22):

* One ``worker.subscriber_audit`` event per process boot.
* Payload: ``handlers`` list with ``{handler_name, requires_tenant,
  mro_chain}`` per registered subscriber.
* Operator runbook query: latest ``worker.subscriber_audit`` row →
  inventory snapshot.

Important caveats baked into this implementation:

1. **Observability, not enforcement.** The runtime defence lives in
   :mod:`apps.workers.base` (PR #496 frozen-snapshot + MRO-walk).
   This module is read-only — failure to emit MUST NOT crash boot.
2. **Skip during migrations / makemigrations / test setup** —
   running ``manage.py migrate`` boots apps before the audit table
   exists; a hard write would crash the migration. Defensive
   try/except + an early skip-on-management-command guard.
3. **One emit per process** — module-level `_AUDIT_EMITTED` guard so
   repeated AppConfig.ready() calls (extremely rare but possible
   under test reload) don't double-write.
4. **Canonical registry accessor only** — uses
   :func:`apps.workers.registry.iter_handlers` rather than poking
   ``_HANDLERS`` directly.
5. **ASGI-safe write path (DRF-1153)** — ``AppConfig.ready()`` runs
   inside a running asyncio event loop when the process is booted as
   ``uvicorn config.asgi:application``. Django's ORM refuses
   synchronous queries from such a thread
   (:exc:`django.core.exceptions.SynchronousOnlyOperation`), so the
   insert is off-loaded to a short-lived worker thread whenever a
   running loop is detected. Without this, the web process logged an
   ``events.emit_failed`` ERROR on every boot and wrote nothing, while
   still reporting success — the inventory stayed complete only
   because synchronous processes (celery worker, management commands)
   happened to emit the same event. See :func:`_emit_off_event_loop`.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import sys
import threading

logger = logging.getLogger(__name__)

# Module-level guard so the emit fires at most once per process boot.
# Reset only via explicit test seam below.
_AUDIT_EMITTED = False

# Upper bound on how long boot waits for the off-loop worker thread
# (DRF-1153). It is a single INSERT; 5s is a generous ceiling that still
# guarantees a wedged database cannot hang an ASGI process at startup.
_EMIT_THREAD_TIMEOUT_SECONDS = 5.0

# `manage.py` subcommands that boot Django apps but for which writing
# to the audit table is either unsafe (table doesn't exist yet) or
# undesirable (test runner pollutes its own DB; collectstatic is
# read-only by intent).
_SKIP_COMMANDS = frozenset(
    {
        "migrate",
        "makemigrations",
        "sqlmigrate",
        "showmigrations",
        "collectstatic",
        "createsuperuser",
        "check",
        "dbshell",
        "shell",
    }
)


def _is_management_command_that_should_skip() -> bool:
    """Detect via ``sys.argv`` whether we're running a manage.py
    command that mustn't trigger the audit emit.

    Tests (``pytest`` invocation) are detected separately — pytest
    sets ``PYTEST_CURRENT_TEST`` env var only DURING tests, but at
    import time we conservatively check ``sys.argv[0]``.
    """

    if len(sys.argv) < 2:
        # Bare interpreter, gunicorn worker, celery beat — emit allowed.
        return False
    # ``manage.py <command> ...`` → sys.argv = ['manage.py', '<cmd>', ...].
    # ``python -m apps.workers.consumer`` → sys.argv = ['.../consumer.py', ...].
    invoked = (sys.argv[0] or "").lower()
    if invoked.endswith("manage.py"):
        cmd = sys.argv[1] if len(sys.argv) > 1 else ""
        return cmd in _SKIP_COMMANDS
    # pytest direct: sys.argv[0] often ends in pytest / py.test.
    if "pytest" in invoked or "py.test" in invoked:
        return True
    return False


def _build_handler_inventory_payload() -> dict:
    """Snapshot every registered :class:`TenantAwareTask` subscriber.

    Returns the audit-event payload dict directly. Pure read — no I/O.
    """

    from apps.workers.registry import iter_handlers

    handlers_data = []
    for stream, handler_instance in iter_handlers():
        handler_cls = type(handler_instance)
        # `_RESOLVED_REQUIRES_TENANT` is the frozen snapshot stored by
        # ``TenantAwareTask.__init_subclass__`` (PR #496 B2 defence).
        # Fall back to the live attribute if missing — shouldn't happen
        # post-#496 but defensive against weird metaclass cases.
        frozen = getattr(handler_cls, "_RESOLVED_REQUIRES_TENANT", None)
        if frozen is None:
            frozen = getattr(handler_cls, "requires_tenant", True)
        # MRO chain as a list of qualified class names — operators can
        # spot mixin shadowing patterns without re-inspecting the code.
        mro_chain = [
            f"{c.__module__}.{c.__qualname__}" for c in handler_cls.__mro__ if c is not object
        ]
        handlers_data.append(
            {
                "stream": stream,
                "handler_class": handler_cls.__qualname__,
                "handler_module": handler_cls.__module__,
                "requires_tenant": bool(frozen),
                "mro_chain": mro_chain,
            }
        )

    return {
        "subscriber_count": len(handlers_data),
        "handlers": handlers_data,
    }


def _running_in_async_context() -> bool:
    """``True`` when the calling thread has a running asyncio event loop.

    Same probe Django's ``@async_unsafe`` decorator performs before it
    raises :exc:`~django.core.exceptions.SynchronousOnlyOperation` — so
    when this returns ``True``, a synchronous ORM call from this thread
    is guaranteed to fail.

    ``True`` under ``uvicorn config.asgi:application`` (staging /
    docker); ``False`` under ``gunicorn config.wsgi:application``
    (the production systemd unit), celery workers and management
    commands.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _emit_now() -> bool:
    """Build the inventory and write the audit event on THIS thread.

    Never raises — that is the whole point of the module (caveat 1).
    """

    try:
        from apps.events.services import emit

        payload = _build_handler_inventory_payload()
        inserted = emit("worker.subscriber_audit", payload=payload)
        if inserted is False:
            # DRF-1153: ``emit()`` swallows its own DB errors, so the
            # absence of an exception is NOT evidence the row landed.
            # Report the miss at WARNING — ``emit()`` has already logged
            # an ERROR with the traceback, and a second ERROR here would
            # double exactly the noise this ticket exists to remove.
            logger.warning(
                "workers.subscriber_audit.not_recorded reason=event_insert_swallowed"
                " — boot continues, inventory incomplete for this process"
            )
            return False
        logger.info(
            "workers.subscriber_audit.emitted subscriber_count=%d",
            payload["subscriber_count"],
        )
        return True
    except Exception:  # noqa: BLE001 — observability must never crash boot
        logger.exception("workers.subscriber_audit.emit_failed — boot continues")
        return False


def _emit_off_event_loop() -> bool:
    """Run :func:`_emit_now` on a short-lived worker thread (DRF-1153).

    Why a thread and not ``sync_to_async``: ``AppConfig.ready()`` is a
    synchronous method invoked by the app registry, so there is nothing
    to ``await`` a coroutine with, and ``async_to_sync`` refuses to run
    from inside an already-running loop. A plain worker thread is what
    Django's own error message prescribes ("use a thread or
    sync_to_async") and is exactly what
    ``asgiref.sync.sync_to_async(thread_sensitive=False)`` does
    internally — minus the coroutine we have no way to await.

    Why not defer the emit to a later moment (first request,
    ``request_started``, a celery task) instead: under ASGI the request
    signals fire inside the same event loop, so the problem would move
    rather than go away; and a celery task would report the *worker's*
    handler registry, not this process's — the wrong inventory. The
    registry is complete the moment ``ready()`` runs, and that is the
    only point where the snapshot is both correct and free.

    Blocking the loop for the duration of one INSERT is acceptable:
    ``ready()`` runs before the server accepts connections, and the
    join is bounded by :data:`_EMIT_THREAD_TIMEOUT_SECONDS`.
    """

    outcome: dict[str, bool] = {}

    def _runner() -> None:
        # Imported inside so a broken DB layer can't affect module import.
        from django.db import connections

        try:
            outcome["ok"] = _emit_now()
        finally:
            # The worker thread opened its OWN thread-local DB
            # connection. Close it, or it leaks for the lifetime of the
            # process. ``close_all`` is thread-scoped — it only touches
            # connections belonging to the calling thread.
            try:
                connections.close_all()
            except Exception:  # noqa: BLE001 — teardown must never crash boot
                logger.exception("workers.subscriber_audit.connection_close_failed")

    # ``threading.Thread`` does not inherit ContextVars, and ``emit()``
    # reads tenant / trace_id from them. Both are empty at boot today,
    # but copying the context keeps the emit correct for any caller that
    # is not boot.
    context = contextvars.copy_context()
    thread = threading.Thread(
        target=context.run,
        args=(_runner,),
        name="subscriber-audit-emit",
        daemon=True,
    )
    thread.start()
    thread.join(timeout=_EMIT_THREAD_TIMEOUT_SECONDS)
    if thread.is_alive():
        logger.warning(
            "workers.subscriber_audit.emit_timeout after=%.1fs — boot continues",
            _EMIT_THREAD_TIMEOUT_SECONDS,
        )
        return False
    return outcome.get("ok", False)


def emit_subscriber_audit() -> bool:
    """Emit one ``worker.subscriber_audit`` event capturing the
    inventory. Returns ``True`` if the event was recorded, ``False`` if
    skipped (already emitted this process, or running under a guarded
    management command) or if the write did not land.

    Idempotent per-process: subsequent calls within the same process
    are no-ops ONCE a write has actually landed. A failed attempt now
    leaves the guard unset so an explicit later call may retry —
    previously the guard was set even when the insert silently failed,
    which is how the ASGI boot (DRF-1153) reported «emitted» with zero
    rows written.

    Under ASGI the DB write is off-loaded to a worker thread; see
    :func:`_emit_off_event_loop`.
    """

    global _AUDIT_EMITTED
    if _AUDIT_EMITTED:
        return False
    if _is_management_command_that_should_skip():
        logger.debug("workers.subscriber_audit.skipped reason=management_command")
        return False

    try:
        if _running_in_async_context():
            emitted = _emit_off_event_loop()
        else:
            emitted = _emit_now()
    except Exception:  # noqa: BLE001 — observability must never crash boot
        # Belt and braces. ``_emit_now`` / ``_emit_off_event_loop``
        # already swallow, but thread creation itself can fail
        # (``RuntimeError: can't start new thread`` under a tight
        # RLIMIT_NPROC or the unit's MemoryMax) and boot must survive
        # that too. The defensive try/except is deliberate — see
        # caveat 1 in the module docstring.
        logger.exception("workers.subscriber_audit.emit_failed — boot continues")
        return False

    if emitted:
        _AUDIT_EMITTED = True
    return emitted


def _reset_for_tests() -> None:
    """Test-only seam — reset the per-process emit guard so test fixtures
    can simulate fresh boots. Not part of the public API.
    """

    global _AUDIT_EMITTED
    _AUDIT_EMITTED = False
