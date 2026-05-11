"""Event emission helper (DRF-429 / E0).

Single entry point for the platform's structured event stream. Reads
both tenant and trace_id from ``apps.tenancy.context`` so callers
don't have to thread them through their signatures.

Contract:
- **Never raises.** Events are telemetry — if the DB is down, the
  request must continue. Log the swallow for Sentry.
- **Reads ContextVars implicitly.** Caller doesn't pass tenant or
  trace_id — `current_tenant()` and `current_trace_id()` do.
- **No raw PII in payload.** Caller redacts upstream.
"""

from __future__ import annotations

import logging
from typing import Any

from apps.events.models import Event
from apps.tenancy.context import current_tenant, current_trace_id

logger = logging.getLogger(__name__)


def emit(event_type: str, payload: dict[str, Any] | None = None) -> None:
    """Insert an Event row for the current tenant + trace_id.

    Args:
      event_type: Namespaced verb. Examples in
        :class:`apps.events.models.Event` docstring.
      payload: Optional dict of extra structured data. Must not contain
               raw PII; redact upstream.

    Behaviour:
      - Reads ``current_tenant()`` and ``current_trace_id()`` from ContextVar.
      - Swallows all exceptions; logs for Sentry visibility.
    """

    tenant = current_tenant()
    trace_id = current_trace_id() or ""
    try:
        Event.objects.create(
            tenant=tenant,
            event_type=event_type,
            payload=payload or {},
            trace_id=trace_id,
        )
    except Exception:  # noqa: BLE001 — telemetry must never break the request
        logger.exception(
            "events.emit_failed event=%s tenant=%s trace=%s",
            event_type,
            tenant.id if tenant else None,
            trace_id,
        )
