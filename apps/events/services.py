"""Event emission helper (DRF-429 / E0 + DRF-461 / Sprint 3 / B3).

Single entry point for the platform's structured event stream. Reads
both tenant and trace_id from :mod:`apps.tenancy.context` so callers
don't have to thread them through their signatures.

### Contract

- **Never raises.** Events are telemetry — if the DB is down, the
  request must continue. Log the swallow for Sentry.
- **Reads ContextVars implicitly.** Caller doesn't pass tenant or
  trace_id — ``current_tenant()`` and ``current_trace_id()`` do.
- **No raw PII in payload.** Caller redacts upstream.

### B3 canonical-envelope upgrade

The legacy signature ``emit(event_type, payload=None)`` is now a
back-compat alias for the canonical
``emit(event_name, *, distinct_id="", dialog_id=None,
properties=None)`` shape. Both forms write to BOTH legacy
(``event_type`` + ``payload``) AND canonical (``event_name`` +
``properties`` + ``distinct_id`` + ``dialog_id``) columns so existing
subscribers (Sprint 1 audit pipeline) keep seeing event_type+payload
while replay tooling (Sprint 5) reads canonical.

Sprint 4 drops the legacy fields. Until then, the mirror write is the
cost of a non-stop-the-world rollout across ~60 call sites.

### Validation policy

If the resolved ``event_name`` is not in :data:`CANONICAL_EVENTS`,
we **log a warning but still insert the row**. The principle:
telemetry must never drop. Out-of-vocab names are internal infra
events (``worker.handler_started``, ``ingress.webhook_received``,
…); B5 audit acknowledges them as legitimate non-canonical traffic.
Sprint 5 replay tooling ignores them.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from apps.events.models import Event
from apps.events.vocabulary import CANONICAL_EVENTS
from apps.tenancy.context import current_tenant, current_trace_id

logger = logging.getLogger(__name__)


def emit(
    event_name: str,
    payload: dict[str, Any] | None = None,
    *,
    distinct_id: str = "",
    dialog_id: uuid.UUID | None = None,
    properties: dict[str, Any] | None = None,
) -> None:
    """Insert an Event row for the current tenant + trace_id.

    Args:
      event_name: Namespaced canonical verb (preferred — see
        :mod:`apps.events.vocabulary`) OR a legacy dotted-namespace
        string for internal infra events (``worker.handler_started``).
      payload: DEPRECATED — legacy structured data. Use ``properties``.
        When given, it's used as the fallback source for ``properties``
        if the latter is None. Mirrored to the legacy ``payload``
        column for back-compat.
      distinct_id: cross-channel user identity (``BotUser.id``
        stringified). Empty for system-level events.
      dialog_id: ``Conversation.id`` for events attached to a turn.
        None for system/background events.
      properties: canonical structured props. Takes precedence over
        ``payload`` when both are passed.

    Behaviour:
      - Reads ``current_tenant()`` and ``current_trace_id()`` from ContextVar.
      - Mirrors writes to both legacy (event_type+payload) and canonical
        (event_name+properties+distinct_id+dialog_id) columns.
      - Logs a warning if ``event_name`` is not in CANONICAL_EVENTS,
        but still inserts the row — telemetry never drops.
      - Swallows all exceptions; logs for Sentry visibility.
    """

    tenant = current_tenant()
    trace_id = current_trace_id() or ""

    # Caller can pass `properties` (preferred), `payload` (legacy), or
    # nothing. Properties wins when both are non-None.
    if properties is not None:
        canonical_props = properties
        legacy_payload = properties
    elif payload is not None:
        canonical_props = payload
        legacy_payload = payload
    else:
        canonical_props = {}
        legacy_payload = {}

    if event_name not in CANONICAL_EVENTS:
        # Out-of-vocab event. Allowed for internal infra telemetry, but
        # we surface a single-line warning so casual typos don't slip
        # past. Sprint 5 replay tooling will treat these as opaque.
        logger.warning(
            "events.emit.non_canonical event=%s tenant=%s trace=%s",
            event_name,
            tenant.id if tenant else None,
            trace_id,
        )

    try:
        Event.objects.create(
            tenant=tenant,
            # Legacy mirror (Sprint 4 will drop event_type + payload).
            event_type=event_name,
            payload=legacy_payload,
            # Canonical envelope (F0.7).
            event_name=event_name,
            properties=canonical_props,
            distinct_id=distinct_id,
            dialog_id=dialog_id,
            trace_id=trace_id,
        )
    except Exception:  # noqa: BLE001 — telemetry must never break the request
        logger.exception(
            "events.emit_failed event=%s tenant=%s trace=%s",
            event_name,
            tenant.id if tenant else None,
            trace_id,
        )
