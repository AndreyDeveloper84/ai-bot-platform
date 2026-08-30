"""Phase 1 canonical event names — event-taxonomy.md §3 catalog.

Locks the dot.notation names + required payload keys for the three
Phase 1 domains: booking (§3.1), customer (§3.2), master (§3.3).

Adding a new event:
  1. Add the constant + payload schema here.
  2. Add the catalog row to event-taxonomy.md §3.
  3. Emit it; subscribers expecting it should be informed.

DO NOT remove or rename existing constants — replay snapshots and
analytics queries reference them by string.
"""

from __future__ import annotations

# ── §3.1 Booking domain ───────────────────────────────────────────────
BOOKING_CREATED = "booking.created"
BOOKING_ATTRIBUTION_ASSIGNED = "booking.attribution.assigned"
BOOKING_CONFIRMED = "booking.confirmed"
BOOKING_CANCELLED = "booking.cancelled"
BOOKING_COMPLETED = "booking.completed"
BOOKING_NO_SHOW = "booking.no_show"
BOOKING_RESCHEDULED = "booking.rescheduled"
BOOKING_REFUNDED = "booking.refunded"

# ── §3.2 Customer domain ──────────────────────────────────────────────
CUSTOMER_CREATED = "customer.created"
CUSTOMER_STATE_CHANGED = "customer.state.changed"
CUSTOMER_PROFILE_LAYER_UPDATED = "customer.profile.layer.updated"
CUSTOMER_CONSENT_CHANGED = "customer.consent.changed"
CUSTOMER_OPTED_OUT = "customer.opted_out"
CUSTOMER_DELETED_REQUEST = "customer.deleted_request"
# Phase 2.a — Loyalty tier transition. Fires only when the tier value
# actually changes (no-op recomputes do not emit).
CUSTOMER_TIER_CHANGED = "customer.tier.changed"

# ── §3.10 Admin / System domain (Phase 2.2 — DLQ + health) ────────────
SYSTEM_MODULE_HEALTH_DEGRADED = "system.module.health.degraded"

# ── §3.3 Master domain ────────────────────────────────────────────────
MASTER_INVITED = "master.invited"
MASTER_INVITE_ACCEPTED = "master.invite.accepted"
MASTER_INVITE_EXPIRED = "master.invite.expired"
MASTER_INVITE_CANCELLED = "master.invite.cancelled"
MASTER_ARCHIVED = "master.archived"
MASTER_UNARCHIVED = "master.unarchived"
MASTER_SERVICE_ADDED = "master.service.added"
MASTER_SERVICE_REMOVED = "master.service.removed"


# Required payload keys per event-taxonomy.md §3 catalog.
# Producer validation: required key missing → reject.
# Optional keys may appear; unknown keys are allowed (forward-compat).
PAYLOAD_REQUIRED_KEYS: dict[str, frozenset[str]] = {
    # §3.1 Booking
    BOOKING_CREATED: frozenset(
        {"booking_id", "customer_id", "service_id", "slot_start", "booking_source"}
    ),
    BOOKING_ATTRIBUTION_ASSIGNED: frozenset(
        {"booking_id", "booking_source", "ai_assist_score", "billable"}
    ),
    BOOKING_CONFIRMED: frozenset({"booking_id", "confirmed_at", "confirmation_method"}),
    BOOKING_CANCELLED: frozenset(
        {"booking_id", "cancelled_by", "cancellation_reason", "cancelled_at"}
    ),
    # ``completed_by`` — кто закрыл визит (решение владельца 30.08).
    # Обязателен: подписчики гейтят по нему последствия, а конверт без
    # актора по правилу default-deny читается как закрытие автоматом —
    # то есть забытое поле тихо гасит начисление за подтверждённый визит.
    # ``marked_by`` остаётся рядом: это имя из таксономии §3.1, под
    # которым поле ездило до 30.08, и ломать его перед пилотом нечем
    # оправдать.
    BOOKING_COMPLETED: frozenset({"booking_id", "completed_at", "marked_by", "completed_by"}),
    BOOKING_NO_SHOW: frozenset({"booking_id", "detected_at"}),
    BOOKING_RESCHEDULED: frozenset(
        {"booking_id", "old_slot_start", "new_slot_start", "rescheduled_by"}
    ),
    BOOKING_REFUNDED: frozenset({"booking_id", "refund_amount", "refunded_at", "refund_reason"}),
    # §3.2 Customer
    CUSTOMER_CREATED: frozenset({"customer_id", "tenant_id", "acquisition_channel"}),
    CUSTOMER_STATE_CHANGED: frozenset({"customer_id", "old_state", "new_state", "reason"}),
    CUSTOMER_PROFILE_LAYER_UPDATED: frozenset(
        {"customer_id", "layer_name", "confidence", "source"}
    ),
    CUSTOMER_CONSENT_CHANGED: frozenset({"customer_id", "consent_type", "granted", "granted_at"}),
    CUSTOMER_OPTED_OUT: frozenset({"customer_id", "opt_out_scope", "reason"}),
    CUSTOMER_DELETED_REQUEST: frozenset({"customer_id", "requested_at"}),
    CUSTOMER_TIER_CHANGED: frozenset({"customer_id", "old_tier", "new_tier", "reason"}),
    # §3.3 Master
    MASTER_INVITED: frozenset({"master_id", "invited_by", "invite_method"}),
    MASTER_INVITE_ACCEPTED: frozenset({"master_id", "accepted_at"}),
    MASTER_INVITE_EXPIRED: frozenset({"master_id", "expired_at"}),
    MASTER_INVITE_CANCELLED: frozenset({"master_id", "cancelled_by"}),
    MASTER_ARCHIVED: frozenset({"master_id", "archived_by", "archive_reason"}),
    MASTER_UNARCHIVED: frozenset({"master_id", "unarchived_by"}),
    MASTER_SERVICE_ADDED: frozenset({"master_id", "service_id", "added_by"}),
    MASTER_SERVICE_REMOVED: frozenset({"master_id", "service_id", "removed_by"}),
    # §3.10 System
    SYSTEM_MODULE_HEALTH_DEGRADED: frozenset({"module_name", "severity", "metric"}),
}


CANONICAL_EVENTS: frozenset[str] = frozenset(PAYLOAD_REQUIRED_KEYS.keys())


def is_canonical(event_name: str) -> bool:
    """Return True iff the name is in the Phase 1 canonical catalog."""

    return event_name in CANONICAL_EVENTS


def required_keys(event_name: str) -> frozenset[str]:
    """Required payload keys for ``event_name``. Empty frozenset if unknown."""

    return PAYLOAD_REQUIRED_KEYS.get(event_name, frozenset())
