"""Atomic, audited state transitions for the customer cancel + reschedule
state machine.

Per `docs/design/policies/customer-cancellation-reschedule-spec.md` §2:

    CONFIRMED ──► CANCEL_REQUESTED ──► CANCELLED
                        │
                        └──► CONFIRMED  (undo within 5s)

    CONFIRMED ──► RESCHEDULE_REQUESTED ──► RESCHEDULED  (+ new booking)
                        │
                        └──► CONFIRMED  (abandon)

Each public function:

* Runs inside ``transaction.atomic``.
* Locks the row with ``select_for_update`` before validating the FROM state.
* Raises :class:`InvalidBookingTransition` if the row is in an
  unexpected state — so racing taps short-circuit cleanly with no
  partial mutation.
* Writes an audit row (``write_audit``) and emits a domain event
  (``emit``) for each transition.

### Why a dedicated service module instead of a method on the model

* Multiple call sites (Mini App API + LLM tool + future bot DM intent)
  must share one execution path. A model method ties transitions to a
  single instance and hides the lock semantics.
* The audit + event emission is opinionated and must run once per
  successful transition — not on every save().
* ``BookingRequest.save()`` is already used by the create path (with
  its own attribution_metadata validator); piling lifecycle rules on
  top would couple two unrelated concerns.

### 5-second undo window

The undo window is enforced server-side: ``request_cancel`` stamps
``cancel_requested_at`` and ``commit_cancel`` /``undo_cancel`` consult
it. The 5-second TTL is a configurable constant
(:data:`UNDO_WINDOW_SECONDS`) so spec changes are one-line edits.

Client-side, the Mini App fires the matching ``confirm`` call after a
5-second timer (or the user taps "Отменить" on the snackbar to fire
``undo``). The server is the authority — if the client is slow and the
window has elapsed, the auto-confirm still succeeds; if the client
sends ``undo`` after the window, it fails with
``InvalidBookingTransition``. This split — client schedules, server
authorises — is the same pattern as ``PendingBookingAction``'s
``expires_at``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.audit.services import write_audit
from apps.booking.models import BookingReminder, BookingRequest
from apps.booking.services.attribution import (
    build_customer_attribution_metadata,
    compute_assist_score,
    compute_billable,
)
from apps.events.services import emit
from apps.identity.models import BotUser

logger = logging.getLogger(__name__)


UNDO_WINDOW_SECONDS = 5
"""5-second undo window per spec §3.4 / Q-CR1."""


# --- public event slugs ---------------------------------------------------
EVENT_CANCEL_REQUESTED = "booking.cancel_requested"
EVENT_CANCEL_UNDONE = "booking.cancel_undone"
EVENT_CANCELLED = "booking.cancelled"
EVENT_RESCHEDULE_REQUESTED = "booking.reschedule_requested"
EVENT_RESCHEDULE_ABANDONED = "booking.reschedule_abandoned"
EVENT_RESCHEDULED = "booking.rescheduled"


# --- audit action slugs ---------------------------------------------------
AUDIT_CANCEL_REQUESTED = "booking.cancel_requested"
AUDIT_CANCEL_UNDONE = "booking.cancel_undone"
AUDIT_CANCELLED = "booking.cancelled"
AUDIT_RESCHEDULE_REQUESTED = "booking.reschedule_requested"
AUDIT_RESCHEDULE_ABANDONED = "booking.reschedule_abandoned"
AUDIT_RESCHEDULED = "booking.rescheduled"


class InvalidBookingTransition(Exception):
    """Raised when a transition is attempted from an incompatible FROM state.

    Carries a stable ``slug`` for the HTTP error envelope. The
    Mini App view maps this to 409 Conflict (state changed under us).
    """

    def __init__(self, slug: str, detail: str):
        super().__init__(f"{slug}: {detail}")
        self.slug = slug
        self.detail = detail


@dataclass(frozen=True)
class RescheduleCandidate:
    """Validated reschedule candidate payload."""

    new_master_id: str
    new_service_id: str
    new_visit_at: datetime  # timezone-aware


# --- helpers --------------------------------------------------------------


def _common_event_props(booking: BookingRequest, *, actor: BotUser) -> dict[str, Any]:
    """Common envelope fields for every transition emit + audit."""
    return {
        "tenant_id": str(booking.tenant_id),
        "bot_user_id": str(actor.pk),
        "booking_id": str(booking.pk),
        "actor": "customer",
    }


def _assert_actor_owns(booking: BookingRequest, actor: BotUser) -> None:
    """Defence-in-depth: the actor (BotUser) must own the booking.

    The Mini App view already enforces this when it looks up by
    ``bot_user=request.bot_user`` — but this service is also called
    from the LLM tools where the actor is derived from the
    conversation. Both paths converge here so a single bug somewhere
    can never leak a cross-customer transition.
    """
    if booking.bot_user_id != actor.pk:
        raise InvalidBookingTransition(
            "forbidden",
            f"booking {booking.pk} does not belong to bot_user {actor.pk}",
        )


def _lock_booking(booking_id: Any) -> BookingRequest:
    """SELECT FOR UPDATE on the booking row.

    Returns the freshly-fetched row so the transition logic sees the
    latest status (no risk of acting on a stale in-memory instance).
    """
    return BookingRequest.all_tenants.select_for_update().get(pk=booking_id)


# --- transitions ----------------------------------------------------------


def request_cancel(
    booking: BookingRequest,
    *,
    actor: BotUser,
    reason_class: str | None = None,
    reason_text: str | None = None,
) -> BookingRequest:
    """CONFIRMED / RESCHEDULE_REQUESTED → CANCEL_REQUESTED.

    Stamps ``cancel_requested_at = now()`` so the undo window starts
    ticking server-side.

    Args:
      booking: target row (lookup already done by the caller; the row
        is re-fetched under-lock by this function).
      actor: BotUser performing the cancel. Must own the row.
      reason_class: spec §3.2 step 3 chip selection — one of
        ``"timing"`` / ``"plans_changed"`` / ``"not_needed"`` /
        ``"other"``. Optional.
      reason_text: free text — only persisted in
        ``attribution_metadata`` (private to customer + owner;
        masters NEVER see it per spec §11).
    """

    _assert_actor_owns(booking, actor)
    with transaction.atomic():
        row = _lock_booking(booking.pk)
        if row.status not in (
            BookingRequest.Status.CONFIRMED,
            BookingRequest.Status.RESCHEDULE_REQUESTED,
        ):
            raise InvalidBookingTransition(
                "invalid_state",
                f"cannot request_cancel from status={row.status!r}",
            )
        now = timezone.now()
        meta = dict(row.attribution_metadata or {})
        if reason_class:
            meta["cancellation_reason_class"] = reason_class
        if reason_text:
            meta["cancellation_reason_text"] = reason_text
        row.status = BookingRequest.Status.CANCEL_REQUESTED
        row.cancel_requested_at = now
        row.attribution_metadata = meta
        # Skip the attribution-metadata validator that runs on save():
        # this row already passed it at create time and we're not
        # changing booking_source / actor_type / visit_at.
        BookingRequest.all_tenants.filter(pk=row.pk).update(
            status=row.status,
            cancel_requested_at=row.cancel_requested_at,
            attribution_metadata=row.attribution_metadata,
        )

    props = _common_event_props(row, actor=actor)
    props["reason_class"] = reason_class or ""
    write_audit(AUDIT_CANCEL_REQUESTED, target="BookingRequest", target_id=row.pk, payload=props)
    emit(EVENT_CANCEL_REQUESTED, properties=props)
    logger.info("booking.cancel_requested id=%s actor=%s", row.pk, actor.pk)
    return row


def commit_cancel(booking: BookingRequest, *, actor: BotUser) -> BookingRequest:
    """CANCEL_REQUESTED → CANCELLED.

    Idempotent: if the row is already CANCELLED, returns the row
    silently (replay safety — the Mini App fires this from a 5-second
    timer; a slow network might re-deliver after the server already
    committed).
    """

    _assert_actor_owns(booking, actor)
    with transaction.atomic():
        row = _lock_booking(booking.pk)
        if row.status == BookingRequest.Status.CANCELLED:
            # Idempotent re-delivery — nothing to do.
            return row
        if row.status != BookingRequest.Status.CANCEL_REQUESTED:
            raise InvalidBookingTransition(
                "invalid_state",
                f"cannot commit_cancel from status={row.status!r}",
            )
        row.status = BookingRequest.Status.CANCELLED
        BookingRequest.all_tenants.filter(pk=row.pk).update(status=row.status)
        # Cancel any still-pending reminders so the beat doesn't
        # dispatch for a row the user already cancelled.
        BookingReminder.all_tenants.filter(
            booking_request=row,
            status=BookingReminder.Status.PENDING,
        ).update(status=BookingReminder.Status.CANCELLED)

    props = _common_event_props(row, actor=actor)
    props["reason_class"] = (row.attribution_metadata or {}).get("cancellation_reason_class", "")
    write_audit(AUDIT_CANCELLED, target="BookingRequest", target_id=row.pk, payload=props)
    emit(EVENT_CANCELLED, properties=props)
    logger.info("booking.cancelled id=%s actor=%s", row.pk, actor.pk)
    return row


def undo_cancel(booking: BookingRequest, *, actor: BotUser) -> BookingRequest:
    """CANCEL_REQUESTED → CONFIRMED.

    Only allowed within :data:`UNDO_WINDOW_SECONDS` of the original
    request_cancel. Past the window, raises ``InvalidBookingTransition``
    with slug ``undo_window_elapsed`` so the client can show the
    correct copy ("Окно отмены истекло").
    """

    _assert_actor_owns(booking, actor)
    with transaction.atomic():
        row = _lock_booking(booking.pk)
        if row.status != BookingRequest.Status.CANCEL_REQUESTED:
            raise InvalidBookingTransition(
                "invalid_state",
                f"cannot undo_cancel from status={row.status!r}",
            )
        now = timezone.now()
        requested_at = row.cancel_requested_at or now
        if now - requested_at > timedelta(seconds=UNDO_WINDOW_SECONDS):
            raise InvalidBookingTransition(
                "undo_window_elapsed",
                f"undo window elapsed ({UNDO_WINDOW_SECONDS}s)",
            )
        row.status = BookingRequest.Status.CONFIRMED
        row.cancel_requested_at = None
        # Drop reason hints — the customer reversed the cancel.
        meta = dict(row.attribution_metadata or {})
        meta.pop("cancellation_reason_class", None)
        meta.pop("cancellation_reason_text", None)
        row.attribution_metadata = meta
        BookingRequest.all_tenants.filter(pk=row.pk).update(
            status=row.status,
            cancel_requested_at=None,
            attribution_metadata=row.attribution_metadata,
        )

    props = _common_event_props(row, actor=actor)
    write_audit(AUDIT_CANCEL_UNDONE, target="BookingRequest", target_id=row.pk, payload=props)
    emit(EVENT_CANCEL_UNDONE, properties=props)
    logger.info("booking.cancel_undone id=%s actor=%s", row.pk, actor.pk)
    return row


def request_reschedule(
    booking: BookingRequest,
    *,
    actor: BotUser,
    new_master_id: str,
    new_service_id: str,
    new_visit_at: datetime,
) -> BookingRequest:
    """CONFIRMED → RESCHEDULE_REQUESTED.

    Stashes the candidate slot on the row so the subsequent
    ``commit_reschedule`` can act on it. Validation (slot still free,
    master accepts service, etc.) happens in ``commit_reschedule``
    under-lock — this preview path trusts the caller to have run the
    slot resolver. That keeps the preview lightweight.
    """

    _assert_actor_owns(booking, actor)
    with transaction.atomic():
        row = _lock_booking(booking.pk)
        if row.status != BookingRequest.Status.CONFIRMED:
            raise InvalidBookingTransition(
                "invalid_state",
                f"cannot request_reschedule from status={row.status!r}",
            )
        candidate = {
            "new_master_id": str(new_master_id),
            "new_service_id": str(new_service_id),
            "new_visit_at": new_visit_at.isoformat(),
        }
        row.status = BookingRequest.Status.RESCHEDULE_REQUESTED
        row.reschedule_candidate = candidate
        BookingRequest.all_tenants.filter(pk=row.pk).update(
            status=row.status,
            reschedule_candidate=row.reschedule_candidate,
        )

    props = _common_event_props(row, actor=actor)
    props["candidate_visit_at"] = new_visit_at.isoformat()
    write_audit(
        AUDIT_RESCHEDULE_REQUESTED,
        target="BookingRequest",
        target_id=row.pk,
        payload=props,
    )
    emit(EVENT_RESCHEDULE_REQUESTED, properties=props)
    logger.info("booking.reschedule_requested id=%s actor=%s", row.pk, actor.pk)
    return row


def abandon_reschedule(booking: BookingRequest, *, actor: BotUser) -> BookingRequest:
    """RESCHEDULE_REQUESTED → CONFIRMED. Clears the candidate."""

    _assert_actor_owns(booking, actor)
    with transaction.atomic():
        row = _lock_booking(booking.pk)
        if row.status != BookingRequest.Status.RESCHEDULE_REQUESTED:
            raise InvalidBookingTransition(
                "invalid_state",
                f"cannot abandon_reschedule from status={row.status!r}",
            )
        row.status = BookingRequest.Status.CONFIRMED
        row.reschedule_candidate = {}
        BookingRequest.all_tenants.filter(pk=row.pk).update(
            status=row.status,
            reschedule_candidate={},
        )

    props = _common_event_props(row, actor=actor)
    write_audit(
        AUDIT_RESCHEDULE_ABANDONED,
        target="BookingRequest",
        target_id=row.pk,
        payload=props,
    )
    emit(EVENT_RESCHEDULE_ABANDONED, properties=props)
    logger.info("booking.reschedule_abandoned id=%s actor=%s", row.pk, actor.pk)
    return row


def commit_reschedule(
    booking: BookingRequest,
    *,
    actor: BotUser,
) -> tuple[BookingRequest, BookingRequest]:
    """RESCHEDULE_REQUESTED → RESCHEDULED + create a fresh CONFIRMED row.

    Returns ``(old_rescheduled, new_confirmed)``.

    The new row inherits the customer (``bot_user``) and tenant from
    the old. Attribution per spec §5.5: the new row is NOT billable
    (reschedule is retention not acquisition — Q12-α), with
    ``attribution_metadata.rescheduled_from`` set to the old UUID.

    Slot collision is handled by the partial unique constraint on
    ``(master, visit_at)`` for active statuses — if another customer
    grabbed the same slot between request_reschedule and commit, the
    insert fails and we raise ``slot_unavailable`` so the caller can
    roll back the old row to CONFIRMED.
    """

    from apps.catalog.models import CatalogMaster, CatalogService, MasterService
    from django.db import IntegrityError

    _assert_actor_owns(booking, actor)
    with transaction.atomic():
        row = _lock_booking(booking.pk)
        if row.status != BookingRequest.Status.RESCHEDULE_REQUESTED:
            raise InvalidBookingTransition(
                "invalid_state",
                f"cannot commit_reschedule from status={row.status!r}",
            )
        candidate = row.reschedule_candidate or {}
        new_master_id = candidate.get("new_master_id") or ""
        new_service_id = candidate.get("new_service_id") or ""
        new_visit_at_raw = candidate.get("new_visit_at") or ""
        if not new_master_id or not new_service_id or not new_visit_at_raw:
            raise InvalidBookingTransition("invalid_state", "reschedule candidate is missing")
        new_visit_at = datetime.fromisoformat(new_visit_at_raw)

        # Resolve catalog rows under-lock — master may have been
        # archived between request and commit.
        try:
            new_master = CatalogMaster.all_tenants.select_for_update().get(
                id=new_master_id, tenant_id=row.tenant_id
            )
        except CatalogMaster.DoesNotExist:
            raise InvalidBookingTransition("master_not_found", "master not bookable")
        if not new_master.is_active:
            raise InvalidBookingTransition("master_archived", "master deactivated")
        if new_master.invite_status != CatalogMaster.InviteStatus.ACCEPTED:
            raise InvalidBookingTransition(
                "master_not_bookable",
                f"master invite_status={new_master.invite_status}",
            )

        try:
            new_service = CatalogService.all_tenants.get(
                id=new_service_id, tenant_id=row.tenant_id, is_active=True
            )
        except CatalogService.DoesNotExist:
            raise InvalidBookingTransition("service_not_found", "service not found")
        if not new_service.duration_min:
            raise InvalidBookingTransition("service_unbookable", "service has no duration")
        if not MasterService.all_tenants.filter(
            tenant_id=row.tenant_id,
            master_id=new_master.id,
            service_id=new_service.id,
        ).exists():
            raise InvalidBookingTransition(
                "service_not_offered", "master does not perform this service"
            )

        # Build attribution metadata. The NEW row carries
        # rescheduled_from pointing to the old row's pk.
        meta = build_customer_attribution_metadata(
            booking_created_at=timezone.now().isoformat(),
            test_mode=False,
        )
        meta["rescheduled_from"] = str(row.pk)
        meta["reschedule_count"] = (
            int((row.attribution_metadata or {}).get("reschedule_count", 0)) + 1
        )
        # Q12-α: reschedule never bills. compute_billable correctly
        # returns False for the (reschedule path) because we override
        # billing_reason below — but use compute_billable for
        # consistency; the locked rule already disqualifies any
        # status != confirmed, and we DO write CONFIRMED here, so we
        # override billing manually per spec.
        ai_assist_score = compute_assist_score(booking_source="ai_direct")
        billable = False
        billing_reason = "NOT billable: execute_reschedule (retention not acquisition, Q12-α)"
        _ = compute_billable  # imported for future symmetry

        try:
            new_row = BookingRequest.objects.create(
                tenant=row.tenant,
                bot_user=row.bot_user,
                service=new_service,
                master=new_master,
                service_name=new_service.name,
                master_name=new_master.name,
                client_name=row.client_name,
                client_phone=row.client_phone,
                visit_at=new_visit_at,
                duration_min=int(new_service.duration_min),
                status=BookingRequest.Status.CONFIRMED,
                source="bot",
                booking_source="ai_direct",
                ai_assist_score=ai_assist_score,
                billable=billable,
                billing_reason=billing_reason,
                attribution_metadata=meta,
                rescheduled_from=row,
            )
        except IntegrityError as exc:
            # Partial unique constraint violation — someone grabbed
            # the slot between request and commit. Caller rolls the
            # old row back to CONFIRMED.
            logger.info("booking.reschedule.slot_collision booking=%s err=%s", row.pk, exc)
            raise InvalidBookingTransition("slot_unavailable", "slot was taken by another customer")

        # Flip the old row to RESCHEDULED + clear candidate + cancel
        # its pending reminders.
        BookingRequest.all_tenants.filter(pk=row.pk).update(
            status=BookingRequest.Status.RESCHEDULED,
            reschedule_candidate={},
        )
        BookingReminder.all_tenants.filter(
            booking_request=row,
            status=BookingReminder.Status.PENDING,
        ).update(status=BookingReminder.Status.CANCELLED)
        row.refresh_from_db()

    props = _common_event_props(row, actor=actor)
    props["rescheduled_from"] = str(row.pk)
    props["new_booking_id"] = str(new_row.pk)
    props["old_slot_start"] = row.visit_at.isoformat() if row.visit_at else ""
    props["new_slot_start"] = new_row.visit_at.isoformat() if new_row.visit_at else ""
    write_audit(
        AUDIT_RESCHEDULED,
        target="BookingRequest",
        target_id=row.pk,
        payload=props,
    )
    emit(EVENT_RESCHEDULED, properties=props)
    logger.info("booking.rescheduled old=%s new=%s actor=%s", row.pk, new_row.pk, actor.pk)
    return row, new_row


__all__ = [
    "EVENT_CANCEL_REQUESTED",
    "EVENT_CANCEL_UNDONE",
    "EVENT_CANCELLED",
    "EVENT_RESCHEDULE_REQUESTED",
    "EVENT_RESCHEDULE_ABANDONED",
    "EVENT_RESCHEDULED",
    "InvalidBookingTransition",
    "RescheduleCandidate",
    "UNDO_WINDOW_SECONDS",
    "abandon_reschedule",
    "commit_cancel",
    "commit_reschedule",
    "request_cancel",
    "request_reschedule",
    "undo_cancel",
]
