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

# --- Master M0 onboarding (master-mobile §M0 + master-management MM2) ---
# Audit slugs registered here so out-of-vocab warnings stay silent in CI.
# Payload contract: {tenant_id, master_id, bot_user_id, ...extra}.
MASTER_ONBOARDING_STARTED = "master.onboarding_started"
MASTER_ONBOARDING_ACCEPTED = "master.onboarding_accepted"
MASTER_ONBOARDING_REJECTED = "master.onboarding_rejected"
MASTER_PROFILE_INITIALIZED = "master.profile_initialized"

# --- Admin master-roster CRUD (master-management MM1-MM3 backend) --------
# Emitted from apps.admin_api when an owner/admin edits a master record
# through the Ayla Pro web dashboard or admin Mini App. Payload contract:
# {master_id, actor_role, fields_changed: [..], previous_values: {..}} for
# profile updates; {master_id, actor_role, size_bytes, mime} for photo.
MASTER_PROFILE_UPDATED_BY_ADMIN = "master.profile_updated_by_admin"
MASTER_PHOTO_UPDATED_BY_ADMIN = "master.photo_updated_by_admin"

# --- Admin master invite flow (master-management MM2 backend / PR 3) -----
# Emitted from apps.admin_api when an owner/admin issues a fresh
# CatalogMaster invite. Payload contract:
#   master.invited:
#     {master_id, actor_id, actor_role, role: "master", contact_method,
#      mode, services_count, idempotent: bool}
#   master.invite_dispatched:
#     {master_id, channel: "max", delivery: "queued"|"failed"|"skipped",
#      error?: str}
# The two events are paired: ``invited`` records the admin's intent +
# the DB row creation, ``invite_dispatched`` records the side-channel
# delivery attempt (queued post-commit via ``transaction.on_commit``).
MASTER_INVITED = "master.invited"
MASTER_INVITE_DISPATCHED = "master.invite_dispatched"

# --- Admin services ↔ masters mapping (master-management MM4 / PR 4) -----
# Emitted from apps.admin_api when an owner/admin toggles the M2M between
# CatalogMaster and CatalogService. One event PER affected master with a
# bundled diff (not per change row) to keep the audit feed readable.
# Payload contract:
#   master.services_changed:
#     {master_id, actor_id, actor_role, tenant_id,
#      services_added: [<service_uuid>...],
#      services_removed: [<service_uuid>...],
#      occurred_at: <iso>}
MASTER_SERVICES_CHANGED = "master.services_changed"

# --- Admin master deactivation cascade (master-management MM5 / PR Tier1.1) --
# Emitted from apps.admin_api.services.master_deactivation. Payload contracts:
#   master.deactivation_started (low-volume; preview endpoint):
#     {master_id, actor_id, actor_role, tenant_id,
#      future_bookings_count}
#   master.bookings_reassigned (per booking):
#     {master_id, actor_id, actor_role, tenant_id, booking_id,
#      from_master_id, to_master_id, visit_at,
#      customer_notification_message_hash}
#   master.bookings_cancelled (per booking):
#     {master_id, actor_id, actor_role, tenant_id, booking_id, visit_at,
#      customer_notification_message_hash}
#   master.deactivated (terminal):
#     {master_id, actor_id, actor_role, tenant_id, reason,
#      reassigned_count, cancelled_count}
#   master.reactivated (terminal):
#     {master_id, actor_id, actor_role, tenant_id, notified_master}
MASTER_DEACTIVATION_STARTED = "master.deactivation_started"
MASTER_BOOKINGS_REASSIGNED = "master.bookings_reassigned"
MASTER_BOOKINGS_CANCELLED = "master.bookings_cancelled"
MASTER_DEACTIVATED = "master.deactivated"
MASTER_REACTIVATED = "master.reactivated"

# --- Master schedule self-service (master-mobile §M3, PR Tier1.2) ---------
# Master taps «Помечу как недоступно» / requests an off-time window via the
# M3 schedule screen. Payload contract:
#   master.availability_change_requested:
#     {tenant_id, master_id, request_id, bot_user_id,
#      requested_start: <iso>, requested_end: <iso>,
#      reason_class: vacation|sick|personal|other}
# Note: NO PII in payload — the master's free-form reason_text is in the
# DB row only, never on the analytics bus.
MASTER_AVAILABILITY_CHANGE_REQUESTED = "master.availability_change_requested"

# --- Admin availability-request decision (M3-admin / Bundle B backend) -----
# Emitted from apps.admin_api.services.availability when an owner/admin
# resolves a master's PENDING availability request. Payload contracts:
#   admin.availability_approved:
#     {tenant_id, master_id, request_id, actor_id, actor_role,
#      requested_start: <iso>, requested_end: <iso>, reason_class,
#      materialised_dates: ["YYYY-MM-DD", ...]}
#   admin.availability_rejected:
#     {tenant_id, master_id, request_id, actor_id, actor_role,
#      requested_start: <iso>, requested_end: <iso>, reason_class,
#      rejection_reason: <str ≤500>}
# rejection_reason is free-form admin context, NOT customer PII; we keep
# it on the audit bus so forensic review can rebuild why a request was
# declined without re-joining through the DB row.
ADMIN_AVAILABILITY_APPROVED = "admin.availability_approved"
ADMIN_AVAILABILITY_REJECTED = "admin.availability_rejected"

# --- Master conversation detail (master-mobile §M6, PR M6.1) ---------------
# Master reads/sends/promotes in their own conversation. Payload contracts:
#   conversation.master_replied:
#     {tenant_id, conversation_id, master_id, message_id}
#   conversation.marked_read_by_master:
#     {tenant_id, conversation_id, master_id, marked_count}
#   conversation.tier_promoted_to_human_locked:
#     {tenant_id, conversation_id, master_id, reason_class, reason_text}
# Reason_text is free-form forensic context; never PII per master's
# own PII gating (the master themselves never sees customer phone/LTV).
CONVERSATION_MASTER_REPLIED = "conversation.master_replied"
CONVERSATION_MARKED_READ_BY_MASTER = "conversation.marked_read_by_master"
CONVERSATION_TIER_PROMOTED_TO_HUMAN_LOCKED = "conversation.tier_promoted_to_human_locked"

# --- Master notification preferences (master-mobile §M7, Bundle B / 3) -----
# Emitted from apps.master_api.services.notification_prefs.update_prefs
# whenever a master toggles any setting on the M7 screen. Payload contract:
#   master.notification_prefs_updated:
#     {tenant_id, master_id, bot_user_id,
#      changes: {<field>: {before: <val>, after: <val>}, ...}}
# No customer PII in payload — the diff carries only boolean toggles + the
# master's own quiet-hours times (HH:MM strings).
MASTER_NOTIFICATION_PREFS_UPDATED = "master.notification_prefs_updated"

# --- Master-Admin internal chat (handoff 2026-05-19, PR 6) ----------------
# The handoff §10 ships 6 events; PR 6 registers the matching audit slugs
# (analytics-bus event names — snake_case dotted notation aligned with
# event-taxonomy.md §3.12). The SLA-breach + auto-close slugs land
# alongside the Celery beat that detects them (separate PR); kept out of
# the canonical set here so an out-of-vocab warning doesn't fire from
# stub code that does not yet emit them.
#
# Payload contracts (consumed by event-taxonomy.md §3.12):
#   internal_chat.thread_created:
#     {tenant_id, thread_id, master_id, topic, linked_artifact_type,
#      linked_artifact_id, actor_id, is_sensitive}
#   internal_chat.message_sent:
#     {tenant_id, thread_id, message_id, sender_role, sender_user_id,
#      has_attachments}     # NOTE: body content NEVER in payload
#   internal_chat.thread_status_changed:
#     {tenant_id, thread_id, from_status, to_status, actor_id}
#   internal_chat.thread_assigned:
#     {tenant_id, thread_id, assigned_admin_id, actor_id}
#   internal_chat.escalated_to_founder:
#     {tenant_id, thread_id, master_id, reason_class}
#   internal_chat.marked_read:
#     {tenant_id, thread_id, reader_role, reader_user_id, count}
INTERNAL_CHAT_THREAD_CREATED = "internal_chat.thread_created"
INTERNAL_CHAT_MESSAGE_SENT = "internal_chat.message_sent"
INTERNAL_CHAT_THREAD_STATUS_CHANGED = "internal_chat.thread_status_changed"
INTERNAL_CHAT_THREAD_ASSIGNED = "internal_chat.thread_assigned"
INTERNAL_CHAT_ESCALATED_TO_FOUNDER = "internal_chat.escalated_to_founder"
INTERNAL_CHAT_MARKED_READ = "internal_chat.marked_read"


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
        MASTER_ONBOARDING_STARTED,
        MASTER_ONBOARDING_ACCEPTED,
        MASTER_ONBOARDING_REJECTED,
        MASTER_PROFILE_INITIALIZED,
        MASTER_PROFILE_UPDATED_BY_ADMIN,
        MASTER_PHOTO_UPDATED_BY_ADMIN,
        MASTER_INVITED,
        MASTER_INVITE_DISPATCHED,
        MASTER_SERVICES_CHANGED,
        INTERNAL_CHAT_THREAD_CREATED,
        INTERNAL_CHAT_MESSAGE_SENT,
        INTERNAL_CHAT_THREAD_STATUS_CHANGED,
        INTERNAL_CHAT_THREAD_ASSIGNED,
        INTERNAL_CHAT_ESCALATED_TO_FOUNDER,
        INTERNAL_CHAT_MARKED_READ,
        MASTER_DEACTIVATION_STARTED,
        MASTER_BOOKINGS_REASSIGNED,
        MASTER_BOOKINGS_CANCELLED,
        MASTER_DEACTIVATED,
        MASTER_REACTIVATED,
        MASTER_AVAILABILITY_CHANGE_REQUESTED,
        ADMIN_AVAILABILITY_APPROVED,
        ADMIN_AVAILABILITY_REJECTED,
        CONVERSATION_MASTER_REPLIED,
        CONVERSATION_MARKED_READ_BY_MASTER,
        CONVERSATION_TIER_PROMOTED_TO_HUMAN_LOCKED,
        MASTER_NOTIFICATION_PREFS_UPDATED,
    }
)


def is_canonical(event_name: str) -> bool:
    """Return True iff `event_name` is in the F0.7 canonical vocabulary."""

    return event_name in CANONICAL_EVENTS
