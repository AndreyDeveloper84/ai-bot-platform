"""Canonical event envelope (DRF-460 / Sprint 3 / B2).

PHASE0_DESIGN §F0.7 specifies the canonical event shape that crosses
process boundaries (Redis Streams payloads → workers → fanout
adapters → analytics warehouse):

    event_name + distinct_id + tenant_id + dialog_id + properties +
    trace_id + ts

The :class:`EventEnvelope` dataclass is the in-process representation
of that shape. The DB row (``apps.events.models.Event``) is a
projection — readers can lift it into an envelope via
:meth:`EventEnvelope.from_event_row`. The reverse direction (envelope
→ row) lives in B3 ``emit()``.

### Why a frozen dataclass

- Crosses module boundaries — events flow from emit() to fanout
  adapters to test fixtures. A mutable dict would let any of those
  scribble on it.
- Cheap to construct (no metaclass machinery), easy to pickle for
  Sprint 5 replay snapshots.
- ``validate()`` is a pure function over the envelope; no global state.

### validate() contract

Returns a list of error strings. Empty list = valid. Caller decides
how to react — emit() logs warning + still inserts; ingestion
pipelines may reject. The function intentionally does NOT raise;
collection of errors lets a single call surface every problem at once
rather than die on the first.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from apps.events.vocabulary import CANONICAL_EVENTS

if TYPE_CHECKING:
    from apps.events.models import Event


@dataclass(frozen=True)
class EventEnvelope:
    """Canonical event payload (F0.7).

    Attributes:
      event_name: namespaced verb from :mod:`apps.events.vocabulary`.
      distinct_id: cross-channel user identity. Empty string when the
                   event is system-level (worker boot, breaker rotation).
      tenant_id: tenant UUID. None for system events that escape any
                 single tenant scope.
      dialog_id: Conversation UUID. None for events not attached to a
                 turn (webhook ingestion, breaker rotation).
      properties: structured payload (must be JSON-serialisable). PII
                  redacted upstream.
      trace_id: correlation ID across one pipeline turn. Empty string
                when not set (rare — every ingress path sets it).
      ts: emission timestamp. Defaults to now() but callers can override
          for replay reconstruction.
    """

    event_name: str
    distinct_id: str = ""
    tenant_id: uuid.UUID | None = None
    dialog_id: uuid.UUID | None = None
    properties: dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""
    ts: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))

    @classmethod
    def from_event_row(cls, row: "Event") -> "EventEnvelope":
        """Lift a DB row into an envelope.

        Useful for replay tooling and tests. The row's `event_name`
        wins if non-empty; legacy rows fall back to `event_type` to
        survive the back-compat window.
        """

        return cls(
            event_name=row.event_name or row.event_type,
            distinct_id=row.distinct_id or "",
            tenant_id=row.tenant_id if row.tenant_id is not None else None,
            dialog_id=row.dialog_id,
            properties=row.properties or row.payload or {},
            trace_id=row.trace_id or "",
            ts=row.created_at,
        )


def validate(envelope: EventEnvelope) -> list[str]:
    """Return validation errors for `envelope`. Empty list = valid.

    Rules:
      1. `event_name` must be in CANONICAL_EVENTS (else "unknown event_name").
      2. `properties` must be JSON-serialisable (json.dumps round-trips).
      3. `properties` must be a dict (defensive — dataclass type hint is
         not enforced at runtime).
      4. `tenant_id`, `dialog_id` if set must be UUID instances.
      5. `distinct_id` and `trace_id` must be strings (defensive).

    Does NOT raise — returns the list. Callers decide how to react.
    """

    errors: list[str] = []

    if envelope.event_name not in CANONICAL_EVENTS:
        errors.append(f"unknown event_name: {envelope.event_name!r}")

    if not isinstance(envelope.properties, dict):
        errors.append(f"properties must be a dict, got {type(envelope.properties).__name__}")
    else:
        try:
            json.dumps(envelope.properties)
        except (TypeError, ValueError) as exc:
            errors.append(f"properties not JSON-serialisable: {exc}")

    if envelope.tenant_id is not None and not isinstance(envelope.tenant_id, uuid.UUID):
        errors.append(f"tenant_id must be UUID or None, got {type(envelope.tenant_id).__name__}")

    if envelope.dialog_id is not None and not isinstance(envelope.dialog_id, uuid.UUID):
        errors.append(f"dialog_id must be UUID or None, got {type(envelope.dialog_id).__name__}")

    if not isinstance(envelope.distinct_id, str):
        errors.append(f"distinct_id must be a string, got {type(envelope.distinct_id).__name__}")

    if not isinstance(envelope.trace_id, str):
        errors.append(f"trace_id must be a string, got {type(envelope.trace_id).__name__}")

    return errors
