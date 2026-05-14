"""Audit-writer callback registry — breaks the audit↔tenancy load cycle.

Sprint 8 code review P1 (cycle 1). Background:

  * ``apps/audit`` imports ``apps.tenancy`` — its ``AuditLog`` model
    has a ``tenant = ForeignKey(Tenant)`` field.
  * ``apps/tenancy/managers.py`` wants to write an audit row when it
    sees a scope violation under ``STRICT_TENANT_SCOPE=audit``.

The old solution was a function-local ``from apps.audit.services
import write_audit`` inside ``_audit_log`` — defers the import to
runtime so the load order doesn't crash, but leaves a logical cycle:
foundation (tenancy) depends on domain (audit).

The clean fix is dependency inversion. ``apps.tenancy`` declares THIS
registry interface; ``apps.audit`` registers its writer at AppConfig
``ready()`` time. The cycle disappears at both the module-load level
AND the architectural-rule level (foundation no longer depends on
domain).

### Contract

* Audit writer signature: ``(action: str, payload: dict[str, Any]) -> None``
* Writer MUST NOT raise (audit is observational; raising would crash
  the request path).
* Writer MUST NOT be called from a thread without ``current_tenant()``
  scope unless it tolerates ``tenant=None`` rows (write_audit does).
* If no writer is registered (early boot, tests without ``apps.audit``
  in INSTALLED_APPS), tenancy falls back to its own logger.warning.

### Why a module-level callable, not a Django signal

Signals add receiver-list ordering ambiguity and force every consumer
through Django's signal machinery for a one-writer-one-reader contract.
A module-level callable is the smaller surface and exactly fits the
"exactly one audit backend at a time" reality.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


AuditWriter = Callable[[str, dict[str, Any]], None]


_writer: AuditWriter | None = None


def register_audit_writer(writer: AuditWriter | None) -> None:
    """Install the audit writer callback.

    Called from :class:`apps.audit.apps.AuditConfig.ready` at Django
    startup. Idempotent — re-registering is a no-op when the same
    callable is supplied; replacing with a different callable logs at
    INFO so test fixtures can audit the swap.

    Pass ``None`` to clear the registry (test cleanup).
    """
    global _writer
    if _writer is writer:
        return
    if _writer is not None and writer is not None:
        logger.info("tenancy.audit_writer.replaced previous=%r new=%r", _writer, writer)
    _writer = writer


def write(action: str, payload: dict[str, Any]) -> bool:
    """Call the registered audit writer. Returns whether a writer ran.

    Returns ``False`` when no writer is registered (early boot, tests
    without apps.audit). Callers should fall back to plain logging in
    that case — see :func:`apps.tenancy.managers._audit_log`.

    Defensive: a writer raising will be logged + swallowed, and the
    function returns ``True`` (writer ran, even if it failed) so the
    caller doesn't double-log.
    """
    if _writer is None:
        return False
    try:
        _writer(action, payload)
    except Exception:  # noqa: BLE001 — audit must not crash callers
        logger.exception("tenancy.audit_writer.failed action=%s", action)
    return True


def get_audit_writer() -> AuditWriter | None:
    """Public read access — used by tests / health probes."""
    return _writer
