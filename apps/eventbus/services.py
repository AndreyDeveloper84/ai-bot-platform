"""emit() — single entry point for the domain event bus.

Inserts an outbox row (DomainEvent) inside the caller's transaction
when one exists; otherwise a standalone INSERT.

Contract:
  - PII §6 violations REJECT — emit raises EventbusPiiViolation and
    no row is written. Distinct from analytics emit which only logs.
  - §2 envelope / §3 payload errors warn-and-still-insert — keep
    telemetry flowing during catalog evolution.
  - Reads ``current_tenant()`` from ContextVar (callers may override
    via ``tenant=`` for system-context emits).
  - Never raises for transport errors — dispatcher handles retry.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from apps.eventbus import vocabulary as V
from apps.eventbus.envelope import Actor, ActorRole, ActorType, Envelope
from apps.eventbus.models import DomainEvent
from apps.eventbus.ulid import new_ulid
from apps.eventbus.validation import lint_pii, validate_envelope, validate_payload
from apps.tenancy.context import current_tenant

logger = logging.getLogger(__name__)


class EventbusPiiViolation(ValueError):
    """Raised when emit() refuses to write a row due to §6 PII content."""


def emit(
    event_name: str,
    data: dict[str, Any],
    *,
    actor_type: ActorType,
    actor_id: str | None = None,
    actor_role: ActorRole | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    event_version: str = "1.0",
    metadata: dict[str, Any] | None = None,
    tenant: Any = None,
    occurred_at: dt.datetime | None = None,
) -> Envelope:
    """Persist one domain event to the outbox.

    Args:
      event_name: dot.notation name from taxonomy §3.
      data: per-event payload (PII §6 enforced).
      actor_type: required — taxonomy §2.
      actor_id / actor_role: optional, present when known.
      correlation_id: ULID linking events in one flow.
      causation_id: ULID of the event that caused this one.
      event_version: SemVer (default "1.0").
      metadata: implementation tags. PII rules apply.
      tenant: override for system-context emits. Defaults to ``current_tenant()``.
      occurred_at: producer time. Defaults to ``now(UTC)``.

    Returns:
      The Envelope written. Useful for caller logs / tests.

    Raises:
      EventbusPiiViolation: §6 forbidden field detected in data/metadata.
    """

    metadata = metadata or {}
    pii_errors = lint_pii(data) + lint_pii(metadata)
    if pii_errors:
        # REJECT: §6 violations are a legal-grade issue, not a soft warning.
        msg = f"PII violation in {event_name!r}: {pii_errors}"
        logger.error("eventbus.emit.pii_reject event=%s violations=%s", event_name, pii_errors)
        raise EventbusPiiViolation(msg)

    tenant_obj = tenant if tenant is not None else current_tenant()

    env = Envelope(
        event_id=new_ulid(),
        event_name=event_name,
        event_version=event_version,
        occurred_at=occurred_at or dt.datetime.now(dt.UTC),
        actor=Actor(type=actor_type, id=actor_id, role=actor_role),
        data=data,
        tenant_id=tenant_obj.id if tenant_obj is not None else None,
        correlation_id=correlation_id,
        causation_id=causation_id,
        metadata=metadata,
    )

    # Soft validation — log on mismatch, still insert.
    env_errs = validate_envelope(env)
    payload_errs = validate_payload(event_name, data)
    for err in env_errs + payload_errs:
        logger.warning("eventbus.emit.soft_violation event=%s err=%s", event_name, err)

    try:
        DomainEvent.objects.create(
            event_id=env.event_id,
            event_name=env.event_name,
            event_version=env.event_version,
            occurred_at=env.occurred_at,
            tenant=tenant_obj,
            actor=env.actor.to_dict(),
            correlation_id=env.correlation_id or "",
            causation_id=env.causation_id or "",
            data=env.data,
            metadata=env.metadata,
        )
    except Exception:  # noqa: BLE001 — telemetry must not break the request
        logger.exception(
            "eventbus.emit.db_insert_failed event=%s tenant=%s",
            event_name,
            tenant_obj.id if tenant_obj else None,
        )

    return env


# ── Typed Phase 1 emit helpers ───────────────────────────────────────
# Thin wrappers around emit() that hard-wire the event_name and require
# the catalog's payload keys at the call site. Catches typos at IDE
# autocomplete time — better than a stringly-typed `emit()` everywhere.


def emit_booking_created(
    *,
    booking_id: str,
    customer_id: str,
    service_id: str,
    slot_start: str,
    booking_source: str,
    master_id: str = "",
    slot_end: str = "",
    actor_type: ActorType = "system",
    actor_id: str | None = None,
    correlation_id: str | None = None,
    tenant: Any = None,
    occurred_at: dt.datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> Envelope:
    """Emit ``booking.created`` — taxonomy §3.1."""

    return emit(
        V.BOOKING_CREATED,
        {
            "booking_id": booking_id,
            "customer_id": customer_id,
            "service_id": service_id,
            "slot_start": slot_start,
            "slot_end": slot_end,
            "booking_source": booking_source,
            "master_id": master_id,
        },
        actor_type=actor_type,
        actor_id=actor_id,
        correlation_id=correlation_id,
        tenant=tenant,
        occurred_at=occurred_at,
        metadata=metadata,
    )


def emit_booking_attribution_assigned(
    *,
    booking_id: str,
    booking_source: str,
    ai_assist_score: float,
    billable: bool,
    billing_reason: str = "",
    attribution_metadata: dict[str, Any] | None = None,
    actor_type: ActorType = "system",
    correlation_id: str | None = None,
    tenant: Any = None,
    occurred_at: dt.datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> Envelope:
    """Emit ``booking.attribution.assigned`` — taxonomy §3.1."""

    return emit(
        V.BOOKING_ATTRIBUTION_ASSIGNED,
        {
            "booking_id": booking_id,
            "booking_source": booking_source,
            "ai_assist_score": ai_assist_score,
            "billable": billable,
            "billing_reason": billing_reason,
            "attribution_metadata": attribution_metadata or {},
        },
        actor_type=actor_type,
        correlation_id=correlation_id,
        tenant=tenant,
        occurred_at=occurred_at,
        metadata=metadata,
    )


def emit_booking_cancelled(
    *,
    booking_id: str,
    cancelled_by: str,
    cancellation_reason: str,
    cancelled_at: str,
    actor_type: ActorType = "human",
    actor_id: str | None = None,
    actor_role: ActorRole | None = None,
    correlation_id: str | None = None,
    tenant: Any = None,
) -> Envelope:
    """Emit ``booking.cancelled`` — taxonomy §3.1.

    ``cancelled_by`` is a free-text descriptor of the actor for the
    audit payload (e.g., "customer", "admin", "system"). The typed
    ``actor.*`` fields on the envelope carry the canonical identity.
    """

    return emit(
        V.BOOKING_CANCELLED,
        {
            "booking_id": booking_id,
            "cancelled_by": cancelled_by,
            "cancellation_reason": cancellation_reason,
            "cancelled_at": cancelled_at,
        },
        actor_type=actor_type,
        actor_id=actor_id,
        actor_role=actor_role,
        correlation_id=correlation_id,
        tenant=tenant,
    )


def emit_booking_rescheduled(
    *,
    booking_id: str,
    old_slot_start: str,
    new_slot_start: str,
    rescheduled_by: str,
    actor_type: ActorType = "human",
    actor_id: str | None = None,
    actor_role: ActorRole | None = None,
    correlation_id: str | None = None,
    tenant: Any = None,
) -> Envelope:
    """Emit ``booking.rescheduled`` — taxonomy §3.1."""

    return emit(
        V.BOOKING_RESCHEDULED,
        {
            "booking_id": booking_id,
            "old_slot_start": old_slot_start,
            "new_slot_start": new_slot_start,
            "rescheduled_by": rescheduled_by,
        },
        actor_type=actor_type,
        actor_id=actor_id,
        actor_role=actor_role,
        correlation_id=correlation_id,
        tenant=tenant,
    )


def emit_customer_state_changed(
    *,
    customer_id: str,
    old_state: str,
    new_state: str,
    reason: str,
    triggered_by: str = "",
    actor_type: ActorType = "system",
    correlation_id: str | None = None,
    tenant: Any = None,
) -> Envelope:
    """Emit ``customer.state.changed`` — taxonomy §3.2."""

    return emit(
        V.CUSTOMER_STATE_CHANGED,
        {
            "customer_id": customer_id,
            "old_state": old_state,
            "new_state": new_state,
            "reason": reason,
            "triggered_by": triggered_by,
        },
        actor_type=actor_type,
        correlation_id=correlation_id,
        tenant=tenant,
    )


def emit_customer_tier_changed(
    *,
    customer_id: str,
    old_tier: str,
    new_tier: str,
    reason: str,
    actor_type: ActorType = "system",
    correlation_id: str | None = None,
    tenant: Any = None,
) -> Envelope:
    """Emit ``customer.tier.changed`` — taxonomy §3.2.

    Phase 2.a Loyalty: fires when LoyaltyAccount.tier flips. ``reason``
    is a free-form descriptor of why the recompute crossed the threshold
    (e.g. "earn_visit count=4 → regular").
    """

    return emit(
        V.CUSTOMER_TIER_CHANGED,
        {
            "customer_id": customer_id,
            "old_tier": old_tier,
            "new_tier": new_tier,
            "reason": reason,
        },
        actor_type=actor_type,
        correlation_id=correlation_id,
        tenant=tenant,
    )


def emit_customer_consent_changed(
    *,
    customer_id: str,
    consent_type: str,
    granted: bool,
    granted_at: str,
    granted_via: str = "",
    actor_type: ActorType = "human",
    actor_id: str | None = None,
    actor_role: ActorRole | None = "customer",
    correlation_id: str | None = None,
    tenant: Any = None,
) -> Envelope:
    """Emit ``customer.consent.changed`` — taxonomy §3.2."""

    return emit(
        V.CUSTOMER_CONSENT_CHANGED,
        {
            "customer_id": customer_id,
            "consent_type": consent_type,
            "granted": granted,
            "granted_at": granted_at,
            "granted_via": granted_via,
        },
        actor_type=actor_type,
        actor_id=actor_id,
        actor_role=actor_role,
        correlation_id=correlation_id,
        tenant=tenant,
    )


def emit_master_invited(
    *,
    master_id: str,
    invited_by: str,
    invite_method: str,
    actor_type: ActorType = "human",
    actor_id: str | None = None,
    actor_role: ActorRole | None = "owner",
    correlation_id: str | None = None,
    tenant: Any = None,
) -> Envelope:
    """Emit ``master.invited`` — taxonomy §3.3."""

    return emit(
        V.MASTER_INVITED,
        {
            "master_id": master_id,
            "invited_by": invited_by,
            "invite_method": invite_method,
        },
        actor_type=actor_type,
        actor_id=actor_id,
        actor_role=actor_role,
        correlation_id=correlation_id,
        tenant=tenant,
    )


def emit_master_invite_accepted(
    *,
    master_id: str,
    accepted_at: str,
    actor_type: ActorType = "human",
    actor_id: str | None = None,
    actor_role: ActorRole | None = "master",
    correlation_id: str | None = None,
    tenant: Any = None,
) -> Envelope:
    """Emit ``master.invite.accepted`` — taxonomy §3.3."""

    return emit(
        V.MASTER_INVITE_ACCEPTED,
        {"master_id": master_id, "accepted_at": accepted_at},
        actor_type=actor_type,
        actor_id=actor_id,
        actor_role=actor_role,
        correlation_id=correlation_id,
        tenant=tenant,
    )


def new_correlation_id() -> str:
    """Fresh ULID for cross-bus correlation (Q-EV-IMPL5)."""

    return new_ulid()
