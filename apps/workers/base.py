"""TenantAwareTask base class (DRF-425 / C3).

Pattern: every worker handler subclasses ``TenantAwareTask`` and overrides
``handle(payload)``. The base class does the rest:

  ┌──────────────────────────────────────────────────────────────────┐
  │ consumer.py reads entry from Redis Stream                        │
  │      payload = {"data": {...}, "trace_id": "...",                 │
  │                  "resolved_tenant_id": "..."}                    │
  │                                  │                               │
  │                                  ▼                               │
  │             TenantAwareTask.__call__(payload)                    │
  │                                                                  │
  │             1. Resolve tenant from payload["resolved_tenant_id"] │
  │             2. Enter tenant_scope(tenant)                        │
  │             3. Enter trace_id_scope(payload["trace_id"])         │
  │             4. emit("worker.handler_started")                    │
  │             5. self.handle(payload["data"])                      │
  │             6. emit("worker.handler_completed")                  │
  │             7. (or "worker.handler_failed" + re-raise)           │
  │                                  │                               │
  │                                  ▼                               │
  │  consumer.py XACKs on success; PEL retains on exception.         │
  └──────────────────────────────────────────────────────────────────┘

Subclasses just override ``handle(payload)`` — no boilerplate.
Test-only helper: instantiate the class and call it directly with a
synthesised payload to exercise the handler in isolation.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar
from uuid import UUID

from django.conf import settings

from apps.events.services import emit
from apps.tenancy.context import tenant_scope, trace_id_scope
from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


def resolve_tenant_by_id_string(tenant_id_raw: str):
    """Look up Tenant by UUID string, or None if empty/invalid/unknown.

    Module-level so the consumer loop (`apps.workers.consumer`) and
    every TenantAwareTask subclass share exactly one resolver
    implementation. Per Sprint 2.5 review M3: prevents divergence
    between consumer.py's inline lookup and `TenantAwareTask._resolve_tenant`.

    Uses ``all_objects`` (not the active-filtered ``objects``) so a
    queued webhook for a tenant that was deactivated between enqueue
    and consume still resolves — the consumer drains the queue then
    the handler can decide whether to act based on tenant state. The
    middleware uses ``objects`` for HTTP requests (strict ingress
    gate); this two-tier visibility is intentional, see docstring at
    bottom of `apps/tenancy/models.py`.
    """

    if not tenant_id_raw:
        return None
    try:
        tenant_uuid = UUID(tenant_id_raw)
    except (ValueError, AttributeError):
        return None

    try:
        return Tenant.all_objects.get(id=tenant_uuid)
    except Tenant.DoesNotExist:
        return None


class TenantRequiredButMissing(Exception):
    """Raised by ``TenantAwareTask.__call__`` when a handler declared
    ``requires_tenant = True`` but the stream entry's
    ``resolved_tenant_id`` is empty/invalid/unknown.

    Tenancy retro B4: pre-fix workers silently entered ``tenant_scope(None)``
    for tenant-required handlers; reads returned empty + audit warn but
    handlers proceeded. This exception lets the consumer DLQ the entry
    explicitly instead of running on a phantom-tenant context.

    Gated by ``settings.STRICT_TENANT_REFUSE`` (default False during
    Phase 0 rollout — log-only mode; flip to True after the dev-side
    soak proves no legitimate handler misses its tenant).
    """


class TenantAwareTask(ABC):
    """Base class for stream-driven worker handlers.

    Subclasses MUST override ``handle(payload)``. Base class wraps every
    call in tenant + trace ContextVars and emits start/done/failed events.

    Tenancy retro B4 — tenant-required tag:

      ``requires_tenant: ClassVar[bool] = True``  (default)

    The default is conservative: every handler is presumed to need a
    tenant in scope. Handlers that legitimately run without one (system
    tasks: audit sweep, outbox dispatcher, health check, migration
    runner — none currently subclass TenantAwareTask but the pattern
    is here for future use) MUST override this to ``False`` with a
    docstring note explaining why.

    Enforcement is gated by ``settings.STRICT_TENANT_REFUSE``:

      * False (default — Phase 0 rollout): missing-tenant ON a
        ``requires_tenant=True`` handler logs ERROR but proceeds with
        ``tenant_scope(None)``. Same as pre-B4 behaviour, but loud.
      * True (post-soak):  missing-tenant raises
        :class:`TenantRequiredButMissing` → consumer DLQs the entry.
    """

    requires_tenant: ClassVar[bool] = True

    @abstractmethod
    def handle(self, payload: dict[str, Any]) -> None:
        """User-defined handler. Override in subclass."""

    def __call__(self, raw_entry: dict[str, Any]) -> None:
        """Process one stream entry. Called by the consumer loop.

        Args:
          raw_entry: The full stream entry dict from Redis Streams.
            Expected keys: ``data`` (JSON-stringified payload),
            ``trace_id`` (top-level), ``resolved_tenant_id`` (top-level,
            optional — empty string when unknown).

        Behaviour:
          - Resolves tenant from ``resolved_tenant_id`` (or None).
          - Tenancy retro B4: enforces ``requires_tenant`` via the
            ``STRICT_TENANT_REFUSE`` settings flag — when strict + no
            tenant + requires_tenant=True, raises
            :class:`TenantRequiredButMissing` so the consumer DLQs.
          - Enters tenant_scope + trace_id_scope for the handler's
            entire execution.
          - Emits ``worker.handler_started`` then
            ``worker.handler_completed`` on success, or
            ``worker.handler_failed`` on exception + re-raise.
          - Re-raises any handler exception so the consumer doesn't
            XACK; the message stays in the PEL for retry/escalation.
        """

        tenant = self._resolve_tenant(raw_entry.get("resolved_tenant_id", ""))
        trace_id = raw_entry.get("trace_id", "")
        payload = self._extract_payload(raw_entry)

        # Tenancy retro B4: enforce or log the requires_tenant tag.
        if self.requires_tenant and tenant is None:
            strict = bool(getattr(settings, "STRICT_TENANT_REFUSE", False))
            if strict:
                logger.error(
                    "worker.tenant_required_missing handler=%s trace=%s "
                    "resolved_tenant_id=%r — refusing dispatch (DLQ)",
                    type(self).__name__,
                    trace_id,
                    raw_entry.get("resolved_tenant_id", ""),
                )
                emit(
                    "worker.tenant_required_missing",
                    payload={
                        "handler": type(self).__name__,
                        "strict_mode": True,
                    },
                )
                raise TenantRequiredButMissing(
                    f"{type(self).__name__} requires a tenant but "
                    f"resolved_tenant_id is empty/invalid (trace={trace_id})"
                )
            # Log-only rollout mode: loud ERROR but proceed.
            logger.error(
                "worker.tenant_required_missing handler=%s trace=%s "
                "resolved_tenant_id=%r — proceeding in log-only mode "
                "(STRICT_TENANT_REFUSE=False)",
                type(self).__name__,
                trace_id,
                raw_entry.get("resolved_tenant_id", ""),
            )
            emit(
                "worker.tenant_required_missing",
                payload={
                    "handler": type(self).__name__,
                    "strict_mode": False,
                },
            )
        elif not self.requires_tenant and tenant is None:
            # Tenant-optional handler running without scope — INFO level.
            logger.info(
                "worker.tenantless_handler handler=%s trace=%s",
                type(self).__name__,
                trace_id,
            )

        with tenant_scope(tenant), trace_id_scope(trace_id or None):
            emit(
                "worker.handler_started",
                payload={"handler": type(self).__name__},
            )
            try:
                self.handle(payload)
            except Exception as exc:  # noqa: BLE001 — emit then re-raise
                logger.exception(
                    "worker.handler_failed handler=%s trace=%s",
                    type(self).__name__,
                    trace_id,
                )
                emit(
                    "worker.handler_failed",
                    payload={
                        "handler": type(self).__name__,
                        "error_class": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
                raise
            emit(
                "worker.handler_completed",
                payload={"handler": type(self).__name__},
            )

    @staticmethod
    def _resolve_tenant(tenant_id_raw: str):
        """Look up Tenant by UUID string, or None if empty/invalid/unknown."""

        return resolve_tenant_by_id_string(tenant_id_raw)

    @staticmethod
    def _extract_payload(raw_entry: dict[str, Any]) -> dict[str, Any]:
        """Pull the JSON payload from the stream entry's ``data`` field."""

        import json

        data = raw_entry.get("data")
        if not data:
            return {}
        if isinstance(data, dict):
            return data
        return json.loads(data)
