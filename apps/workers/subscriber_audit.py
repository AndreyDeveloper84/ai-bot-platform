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
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

# Module-level guard so the emit fires at most once per process boot.
# Reset only via explicit test seam below.
_AUDIT_EMITTED = False

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


def emit_subscriber_audit() -> bool:
    """Emit one ``worker.subscriber_audit`` event capturing the
    inventory. Returns ``True`` if the event was emitted, ``False`` if
    skipped (already emitted this process, or running under a guarded
    management command, or emit failed defensively).

    Idempotent per-process: subsequent calls within the same process
    are no-ops.
    """

    global _AUDIT_EMITTED
    if _AUDIT_EMITTED:
        return False
    if _is_management_command_that_should_skip():
        logger.debug("workers.subscriber_audit.skipped reason=management_command")
        return False

    try:
        from apps.events.services import emit

        payload = _build_handler_inventory_payload()
        emit("worker.subscriber_audit", payload=payload)
        _AUDIT_EMITTED = True
        logger.info(
            "workers.subscriber_audit.emitted subscriber_count=%d",
            payload["subscriber_count"],
        )
        return True
    except Exception:  # noqa: BLE001 — observability must never crash boot
        logger.exception("workers.subscriber_audit.emit_failed — boot continues")
        return False


def _reset_for_tests() -> None:
    """Test-only seam — reset the per-process emit guard so test fixtures
    can simulate fresh boots. Not part of the public API.
    """

    global _AUDIT_EMITTED
    _AUDIT_EMITTED = False
