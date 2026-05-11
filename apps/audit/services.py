"""Audit-trail write helper (DRF-426 / B1).

The single entry point for *every* audit event. Reads tenant context
from ``apps.tenancy.context`` so callers don't have to thread tenant
through their signatures.

Design contract:
- **Never raises.** Audit is forensic infrastructure. If the DB is
  unavailable or the row violates a constraint, the surrounding request
  must continue. Caller logic is the source of truth; audit is
  observational. We log the swallow path so the failure is visible in
  Sentry without breaking the request.
- **Tenant-aware via ContextVar.** Caller doesn't pass ``tenant`` —
  ``current_tenant()`` does. If no tenant is in scope (system events,
  cleanup tasks), the row is written with ``tenant=None``.
- **No raw PII in payload.** Callers must scrub. The audit table is the
  one place where redaction must already have happened upstream.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from apps.tenancy.context import current_tenant

logger = logging.getLogger(__name__)


def write_audit(
    action: str,
    *,
    target: str = "",
    target_id: UUID | str | None = None,
    payload: dict[str, Any] | None = None,
    actor_id: UUID | str | None = None,
) -> None:
    """Insert an AuditLog row for the current tenant.

    Args:
      action: Namespaced verb (``tenant.created``, ``cross_tenant.attempt``).
      target: Optional human-readable model name.
      target_id: Optional UUID of the affected row.
      payload: Optional dict of extra structured data. Must not contain
               raw PII; callers redact upstream.
      actor_id: Optional UUID of the human actor. None for system events.

    Behaviour:
      - Reads ``current_tenant()`` for the tenant FK (may be None).
      - Swallows all exceptions; logs them so Sentry can pick them up.
      - Returns None — caller never needs the AuditLog row back. If they
        do, that's a smell: audit is observational, not transactional.
    """

    # Local import to avoid Django app-loading order issues.
    from apps.audit.models import AuditLog

    tenant = current_tenant()
    try:
        AuditLog.all_tenants.create(
            tenant=tenant,
            actor_id=actor_id,
            action=action,
            target=target or "",
            target_id=target_id,
            payload=payload or {},
        )
    except Exception:  # noqa: BLE001 — audit must never break the request
        logger.exception(
            "audit.write_failed action=%s tenant=%s target=%s:%s",
            action,
            tenant.id if tenant else None,
            target,
            target_id,
        )
