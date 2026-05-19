"""Canonical event-name vocabulary (DRF-463 / Sprint 3 / B6).

PHASE0_DESIGN §F0.7 enumerates the canonical Phase-0 event catalogue.
The constants below are the single source of truth; ``emit()`` (B3)
validates incoming names against :data:`CANONICAL_EVENTS` and warns —
but does not drop — events with unknown names. That keeps internal
infrastructure telemetry (``worker.handler_started``,
``ingress.webhook_received``, etc.) flowing while the user-facing
analytics surface stays disciplined.

### Why frozenset, not Enum

These names cross process boundaries (Redis Streams, analytics
fanout, BigQuery export). String comparison is the lowest-common
denominator; an Enum value would need to be stringified at every
boundary anyway. The frozenset gives O(1) membership for the
``validate()`` hot path in B2.

### Adding a new vocab entry

Three steps:
  1. Add the constant here.
  2. Append to :data:`CANONICAL_EVENTS`.
  3. Document the payload contract in
     ``docs/agents/events_vocabulary.md`` (Sprint 5 deliverable).

DO NOT remove or rename existing constants without a migration plan —
old replay snapshots reference them by string value.
"""

from __future__ import annotations

# --- Dialog lifecycle ---------------------------------------------------
CONVERSATION_STARTED = "conversation_started"
MESSAGE_SENT = "message_sent"
MESSAGE_RECEIVED = "message_received"

# --- Orchestration ------------------------------------------------------
INTENT_CLASSIFIED = "intent_classified"
SKILL_DISPATCHED = "skill_dispatched"
TOOL_CALLED = "tool_called"

# --- Safety + handoff ---------------------------------------------------
SAFETY_TRIGGERED = "safety_triggered"
HANDOFF_INITIATED = "handoff_initiated"
PIPELINE_ERROR = "pipeline_error"

# --- Consent ------------------------------------------------------------
CONSENT_GRANTED = "consent_granted"
CONSENT_WITHDRAWN = "consent_withdrawn"

# --- Profile + replay ---------------------------------------------------
CLIENT_PROFILE_RECOMPUTED = "client_profile_recomputed"
REPLAY_CAPTURED = "replay_captured"

# --- Booking lifecycle (customer-cancellation-reschedule-spec) ----------
# Slugs use dotted notation (booking.cancelled etc.) to match the
# attribution + event-taxonomy conventions in
# `docs/design/policies/event-taxonomy.md` §3.1.
BOOKING_CANCEL_REQUESTED = "booking.cancel_requested"
BOOKING_CANCEL_UNDONE = "booking.cancel_undone"
BOOKING_CANCELLED = "booking.cancelled"
BOOKING_RESCHEDULE_REQUESTED = "booking.reschedule_requested"
BOOKING_RESCHEDULE_ABANDONED = "booking.reschedule_abandoned"
BOOKING_RESCHEDULED = "booking.rescheduled"


CANONICAL_EVENTS: frozenset[str] = frozenset(
    {
        CONVERSATION_STARTED,
        MESSAGE_SENT,
        MESSAGE_RECEIVED,
        INTENT_CLASSIFIED,
        SKILL_DISPATCHED,
        TOOL_CALLED,
        SAFETY_TRIGGERED,
        HANDOFF_INITIATED,
        PIPELINE_ERROR,
        CONSENT_GRANTED,
        CONSENT_WITHDRAWN,
        CLIENT_PROFILE_RECOMPUTED,
        REPLAY_CAPTURED,
        BOOKING_CANCEL_REQUESTED,
        BOOKING_CANCEL_UNDONE,
        BOOKING_CANCELLED,
        BOOKING_RESCHEDULE_REQUESTED,
        BOOKING_RESCHEDULE_ABANDONED,
        BOOKING_RESCHEDULED,
    }
)


def is_canonical(event_name: str) -> bool:
    """Return True iff `event_name` is in the F0.7 canonical vocabulary."""

    return event_name in CANONICAL_EVENTS
