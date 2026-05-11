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
from typing import Any
from uuid import UUID

from apps.events.services import emit
from apps.tenancy.context import tenant_scope, trace_id_scope

logger = logging.getLogger(__name__)


class TenantAwareTask(ABC):
    """Base class for stream-driven worker handlers.

    Subclasses MUST override ``handle(payload)``. Base class wraps every
    call in tenant + trace ContextVars and emits start/done/failed events.
    """

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

        if not tenant_id_raw:
            return None
        try:
            tenant_uuid = UUID(tenant_id_raw)
        except ValueError:
            return None
        from apps.tenancy.models import Tenant

        try:
            return Tenant.all_objects.get(id=tenant_uuid)
        except Tenant.DoesNotExist:
            return None

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
