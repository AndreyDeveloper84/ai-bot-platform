"""MM5 deactivation cascade — preview / execute / reactivate.

Per ``docs/design/handoffs/2026-05-18-master-management-handoff.md``
§MM5 (~lines 679-855). This module owns the 4-step deactivation flow's
backend: take inventory of future bookings, ranked fallback masters,
atomic reassign-or-cancel execution, customer + master MAX DM dispatch
on commit, and the inverse reactivate path.

### Surface

Three pure-functions consumed by the views layer:

* :func:`preview_deactivation` — read-only inventory + fallback ranking.
  No audit-row for the preview itself BEYOND the
  ``master.deactivation_started`` low-volume trace so operators can see
  which masters were eyed for deactivation (and which flows were
  abandoned).
* :func:`execute_deactivation` — atomic reassign-and/or-cancel plan
  execution. Validates every future booking is covered, validates each
  reassign target performs the service, flips the master to inactive,
  stamps ``archive_reason``, then on commit dispatches customer + master
  MAX DMs.
* :func:`reactivate_master` — flip ``is_active=True`` + clear
  ``archived_at`` + clear ``archive_reason``. WorkingHours / MasterService
  rows are intentionally untouched (soft-archive keeps them around
  per Q-MM10 «restore as-is»).

### Atomic + on_commit pattern

Same shape as :mod:`apps.booking.services.transitions` — the DB mutations
(reassign + cancel + flip is_active) live inside ``transaction.atomic``
with row-locks; the side-effect dispatch (customer DM, master DM) is
hooked via ``transaction.on_commit`` so a failed MAX call CAN'T roll
back the lifecycle transition. Dispatch failures are logged but
non-fatal — operator can re-dispatch from the audit row if needed.

### Race detection

Between Step 1 (preview) and Step 3 (execute) a customer might book a
NEW slot on the same master. We solve this at execute time: the plan
must mention every booking_id currently in the master's future-confirmed
set. A missing id (new booking arrived) → 400 with
``plan_stale_refresh_required``. Per spec §848.

### Customer notification template

Default template lives as a module constant (spec §770-783 verbatim).
Owner can override via ``custom_template`` in the request body. We
render the template per-booking with placeholders, hash the rendered
text into the audit row so the on_commit dispatch can prove later
"this is the message they actually got".
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from apps.audit.services import write_audit
from apps.booking.models import BookingRequest
from apps.booking.services.transitions import (
    InvalidBookingTransition,
    commit_cancel,
    request_cancel,
)
from apps.catalog.models import CatalogMaster, MasterService
from apps.channels.max.outbound import MaxAPIError, send_message
from apps.events.services import emit
from apps.events.vocabulary import (
    MASTER_BOOKINGS_CANCELLED,
    MASTER_BOOKINGS_REASSIGNED,
    MASTER_DEACTIVATED,
    MASTER_DEACTIVATION_STARTED,
    MASTER_REACTIVATED,
)
from apps.identity.models import BotUser

logger = logging.getLogger(__name__)


# --- public constants -----------------------------------------------------

MAX_FALLBACK_CANDIDATES = 5
"""Cap fallback candidates per booking — keeps payload small + UI scannable."""


DEFAULT_CUSTOMER_NOTIFICATION_TEMPLATE = (
    "Здравствуйте, {client_first_name}! По вашей записи на "
    "{visit_at_human} — {old_master_first_name}, к сожалению, больше не "
    "работает в студии.\n\n"
    "[REASSIGN BRANCH]\n"
    "Вашу запись переведём к {new_master_first_name} "
    "{new_master_last_initial} — {she_he} тоже делает {service_name}.\n"
    "Если так не подходит — напишите, я предложу другие варианты. 🙏\n\n"
    "[CANCEL BRANCH]\n"
    "Запись на это время отменим. Если хотите, могу предложить другие "
    "свободные слоты — напишите."
)
"""Default per-spec §770-783. Owner can override via ``custom_template``."""


REACTIVATION_NOTIFICATION_TEXT = (
    "Вы снова активны в студии — клиенты опять смогут записываться к вам. С возвращением! 🌿"
)


# --- exceptions -----------------------------------------------------------


class DeactivationError(Exception):
    """Validation error during execute_deactivation / reactivate_master.

    Carries a stable ``slug`` for the HTTP envelope. The view maps to
    400 (validation) or 409 (race).
    """

    def __init__(self, slug: str, detail: str, status: int = 400):
        super().__init__(f"{slug}: {detail}")
        self.slug = slug
        self.detail = detail
        self.status = status


# --- dataclasses ----------------------------------------------------------


@dataclass(frozen=True)
class FallbackCandidate:
    """One ranked fallback master for a single future booking."""

    master_id: str
    name: str
    does_this_service: bool
    is_free_at_slot: bool
    match_score: int


@dataclass(frozen=True)
class FutureBookingPreview:
    """A single future-confirmed booking + its ranked fallback list."""

    booking_id: str
    visit_at: datetime
    service_name: str
    service_id: str | None
    client_first_name: str
    client_last_initial: str
    duration_min: int | None
    fallback_masters: list[FallbackCandidate]


@dataclass(frozen=True)
class DeactivationPreview:
    """Result of :func:`preview_deactivation`."""

    master_id: str
    master_name: str
    is_active: bool
    archived_at: datetime | None
    future_bookings: list[FutureBookingPreview]
    total_future_bookings: int
    bookings_with_fallback: int
    bookings_without_fallback: int


@dataclass(frozen=True)
class BookingAction:
    """One per-booking plan entry — parsed from the request body."""

    booking_id: str
    action: str  # "reassign" or "cancel"
    to_master_id: str | None = None


@dataclass(frozen=True)
class DeactivationResult:
    """Result of :func:`execute_deactivation`."""

    master_id: str
    is_active: bool
    archived_at: datetime | None
    reassigned_count: int
    cancelled_count: int
    customer_notifications_dispatched: int
    master_notifications_dispatched: int


@dataclass(frozen=True)
class ReactivationResult:
    master_id: str
    is_active: bool
    archived_at: datetime | None
    notified_master: bool


@dataclass
class _PendingNotification:
    """One customer DM to fire on commit (rendered text + hash + target)."""

    chat_id: str | None
    text: str
    hash_: str
    booking_id: str
    branch: str  # "reassign" or "cancel"


@dataclass
class _PendingMasterNotification:
    chat_id: str
    text: str
    master_id: str


# --- helpers --------------------------------------------------------------


def _first_name(full_name: str) -> str:
    parts = (full_name or "").strip().split()
    return parts[0] if parts else ""


def _last_initial(full_name: str) -> str:
    parts = (full_name or "").strip().split()
    if len(parts) < 2:
        return ""
    return f"{parts[1][:1]}." if parts[1] else ""


def _gender_pronoun(specialization: str) -> str:
    """«она» / «он» heuristic — defaults to «она» (Phase 1 salon is mostly female).

    Spec §831 says to pull from ``specialization`` text; we treat any
    male-marker keyword as «он», otherwise «она». Custom override via
    ``custom_template`` lets owner fix mis-detection.
    """
    spec = (specialization or "").lower()
    if any(token in spec for token in ("мужск", "барбер", "мужчин", "(м)")):
        return "он"
    return "она"


def _render_customer_notification(
    booking: BookingRequest,
    *,
    old_master: CatalogMaster,
    new_master: CatalogMaster | None,
    template: str | None,
) -> str:
    """Fill the template placeholders for one booking + branch.

    The default template embeds two labelled branches — we strip the
    one that doesn't apply. A custom template trusts the owner to
    pre-pick the right branch (frontend assists).
    """

    tpl = template if template is not None else DEFAULT_CUSTOMER_NOTIFICATION_TEMPLATE
    visit_at_human = ""
    if booking.visit_at is not None:
        # Tenant-local stringification: keep ISO-ish but readable.
        visit_at_human = booking.visit_at.strftime("%d.%m.%Y %H:%M")

    fields = {
        "client_first_name": _first_name(booking.client_name),
        "visit_at_human": visit_at_human,
        "old_master_first_name": _first_name(old_master.name),
        "service_name": booking.service_name or "",
        "new_master_first_name": _first_name(new_master.name) if new_master else "",
        "new_master_last_initial": _last_initial(new_master.name) if new_master else "",
        "she_he": _gender_pronoun(new_master.specialization) if new_master else "она",
    }

    rendered = tpl
    for key, val in fields.items():
        rendered = rendered.replace("{" + key + "}", val)

    # Strip the branch that doesn't apply for the DEFAULT template.
    # Custom templates are passed through verbatim (after placeholder fill).
    if template is None:
        if new_master is not None:
            rendered = _keep_branch(rendered, keep="REASSIGN")
        else:
            rendered = _keep_branch(rendered, keep="CANCEL")
    return rendered.strip()


def _keep_branch(text: str, *, keep: str) -> str:
    """Strip the un-kept ``[BRANCH BRANCH]`` block from the default template.

    The default template has::

        [REASSIGN BRANCH]
        ...reassign body...

        [CANCEL BRANCH]
        ...cancel body...

    We keep the body under the marker matching ``keep`` and drop the
    other. Marker lines are removed too.
    """
    reassign_marker = "[REASSIGN BRANCH]"
    cancel_marker = "[CANCEL BRANCH]"
    lines = text.split("\n")
    out: list[str] = []
    current_branch: str | None = None
    for line in lines:
        stripped = line.strip()
        if stripped == reassign_marker:
            current_branch = "REASSIGN"
            continue
        if stripped == cancel_marker:
            current_branch = "CANCEL"
            continue
        if current_branch is None or current_branch == keep:
            out.append(line)
    return "\n".join(out)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _query_future_confirmed_bookings(master: CatalogMaster) -> list[BookingRequest]:
    """Return the master's future-confirmed bookings, sorted by visit_at."""

    now = timezone.now()
    return list(
        BookingRequest.all_tenants.filter(
            tenant_id=master.tenant_id,
            master_id=master.id,
            status=BookingRequest.Status.CONFIRMED,
            visit_at__gte=now,
        )
        .select_related("bot_user", "service")
        .order_by("visit_at")
    )


def _find_fallback_masters(
    booking: BookingRequest,
    *,
    deactivating_master_id: UUID,
) -> list[FallbackCandidate]:
    """Rank fallback masters for a single future booking.

    Per spec §718-757 + scope: same-service + free-slot ranks 100,
    same-service but slot conflict ranks 50, service mismatch is
    excluded. Limit :data:`MAX_FALLBACK_CANDIDATES`; sort score desc,
    name asc.
    """

    if booking.service_id is None or booking.visit_at is None:
        return []

    # Candidate set: active masters in same tenant who perform this service.
    ms_rows = (
        MasterService.all_tenants.filter(
            tenant_id=booking.tenant_id,
            service_id=booking.service_id,
        )
        .exclude(master_id=deactivating_master_id)
        .select_related("master")
    )
    candidates: dict[UUID, CatalogMaster] = {}
    for ms in ms_rows:
        m = ms.master
        if not m.is_active or m.archived_at is not None:
            continue
        if m.invite_status != CatalogMaster.InviteStatus.ACCEPTED:
            continue
        candidates[m.id] = m

    if not candidates:
        return []

    # Build occupied lookup for the booking's slot window.
    duration_min = int(booking.duration_min or 0)
    if duration_min <= 0 and booking.service is not None and booking.service.duration_min:
        duration_min = int(booking.service.duration_min)
    if duration_min <= 0:
        # Defensive — should not happen for active bookings.
        duration_min = 60

    slot_start = booking.visit_at
    from datetime import timedelta as _td

    slot_end = slot_start + _td(minutes=duration_min)

    busy = set(
        BookingRequest.all_tenants.filter(
            tenant_id=booking.tenant_id,
            master_id__in=list(candidates.keys()),
            status__in=(
                BookingRequest.Status.CONFIRMED,
                BookingRequest.Status.CANCEL_REQUESTED,
                BookingRequest.Status.RESCHEDULE_REQUESTED,
            ),
            visit_at__lt=slot_end,
            visit_at__gte=slot_start - _td(hours=24),  # narrow scan window
        )
        .exclude(id=booking.id)
        .values_list("master_id", flat=True)
    )

    out: list[FallbackCandidate] = []
    for m in candidates.values():
        is_free = m.id not in busy
        score = 100 if is_free else 50
        out.append(
            FallbackCandidate(
                master_id=str(m.id),
                name=m.name,
                does_this_service=True,
                is_free_at_slot=is_free,
                match_score=score,
            )
        )

    out.sort(key=lambda c: (-c.match_score, c.name))
    return out[:MAX_FALLBACK_CANDIDATES]


def _build_booking_preview(
    booking: BookingRequest,
    *,
    deactivating_master_id: UUID,
) -> FutureBookingPreview:
    return FutureBookingPreview(
        booking_id=str(booking.id),
        visit_at=booking.visit_at,  # type: ignore[arg-type]
        service_name=booking.service_name or "",
        service_id=str(booking.service_id) if booking.service_id else None,
        client_first_name=_first_name(booking.client_name),
        client_last_initial=_last_initial(booking.client_name),
        duration_min=int(booking.duration_min) if booking.duration_min else None,
        fallback_masters=_find_fallback_masters(
            booking, deactivating_master_id=deactivating_master_id
        ),
    )


# --- public API: preview --------------------------------------------------


def preview_deactivation(
    master: CatalogMaster,
    *,
    actor: BotUser,
    actor_role: str,
) -> DeactivationPreview:
    """Inventory + fallback ranking — read-only.

    Emits :data:`MASTER_DEACTIVATION_STARTED` as a low-volume operator
    trace (lets us notice abandoned flows). No DB mutation.
    """

    bookings = _query_future_confirmed_bookings(master)
    previews = [_build_booking_preview(b, deactivating_master_id=master.id) for b in bookings]

    with_fb = sum(1 for p in previews if p.fallback_masters)
    without_fb = len(previews) - with_fb

    payload = {
        "master_id": str(master.id),
        "actor_id": str(actor.pk),
        "actor_role": actor_role,
        "tenant_id": str(master.tenant_id),
        "future_bookings_count": len(previews),
    }
    write_audit(
        MASTER_DEACTIVATION_STARTED,
        target="catalog.CatalogMaster",
        target_id=master.id,
        payload=payload,
        actor_id=actor.pk,
    )
    emit(MASTER_DEACTIVATION_STARTED, properties=payload)

    return DeactivationPreview(
        master_id=str(master.id),
        master_name=master.name,
        is_active=master.is_active,
        archived_at=master.archived_at,
        future_bookings=previews,
        total_future_bookings=len(previews),
        bookings_with_fallback=with_fb,
        bookings_without_fallback=without_fb,
    )


# --- public API: execute --------------------------------------------------


def _validate_plan_covers_bookings(
    plan: list[BookingAction], current_bookings: list[BookingRequest]
) -> None:
    """Race detection — plan must mention every current future-CONFIRMED id exactly once."""

    plan_ids = [a.booking_id for a in plan]
    plan_set = set(plan_ids)
    if len(plan_ids) != len(plan_set):
        raise DeactivationError("plan_invalid", "plan contains duplicate booking_id entries")

    current_ids = {str(b.id) for b in current_bookings}

    missing = current_ids - plan_set
    if missing:
        raise DeactivationError(
            "plan_stale_refresh_required",
            f"plan missing booking_ids {sorted(missing)} — refresh and retry",
            status=409,
        )
    extra = plan_set - current_ids
    if extra:
        raise DeactivationError(
            "plan_invalid",
            f"plan contains unknown booking_ids {sorted(extra)}",
        )


def _validate_action_shape(actions: list[BookingAction]) -> None:
    for a in actions:
        if a.action not in ("reassign", "cancel"):
            raise DeactivationError(
                "plan_invalid",
                f"booking {a.booking_id}: action must be 'reassign' or 'cancel'",
            )
        if a.action == "reassign" and not a.to_master_id:
            raise DeactivationError(
                "plan_invalid",
                f"booking {a.booking_id}: reassign requires to_master_id",
            )


def execute_deactivation(
    master: CatalogMaster,
    *,
    plan: list[BookingAction],
    reason: str,
    notify_reassigned_masters: bool,
    custom_template: str | None,
    actor: BotUser,
    actor_role: str,
) -> DeactivationResult:
    """Atomic reassign / cancel / archive — dispatch on commit.

    Per spec §MM5 Step 3 commit. Validates plan coverage, validates
    each reassign target performs the service, then inside a single
    transaction:

    1. ``BookingRequest.master_id = to_master_id`` for each reassign.
    2. Customer-side cancel chain for each cancel (request_cancel +
       commit_cancel — preserves the existing audit trail).
    3. Flip ``master.is_active=False`` + ``archived_at=now()`` +
       ``archive_reason=reason``.
    4. Audit + emit one row per booking + one terminal
       ``master.deactivated`` row.

    On commit, dispatch one customer DM per reassign/cancel + one DM
    per unique reassign-target master (if ``notify_reassigned_masters``).
    Dispatch failures log + emit but do NOT roll back.
    """

    if master.archived_at is not None or not master.is_active:
        raise DeactivationError(
            "already_deactivated",
            "master is already deactivated",
        )

    _validate_action_shape(plan)

    # Single tx for the entire cascade.
    pending_customer_notifications: list[_PendingNotification] = []
    pending_master_notifications: list[_PendingMasterNotification] = []

    with transaction.atomic():
        # Re-fetch under-lock so a parallel writer can't beat us.
        master = CatalogMaster.all_tenants.select_for_update().get(pk=master.pk)
        if master.archived_at is not None or not master.is_active:
            raise DeactivationError(
                "already_deactivated",
                "master is already deactivated",
            )

        # Snapshot current future-confirmed bookings for race detection.
        current_bookings = _query_future_confirmed_bookings(master)
        _validate_plan_covers_bookings(plan, current_bookings)
        by_id: dict[str, BookingRequest] = {str(b.id): b for b in current_bookings}

        # Pre-resolve each reassign target — under-lock service check.
        reassign_targets: dict[str, CatalogMaster] = {}
        for action in plan:
            if action.action != "reassign":
                continue
            assert action.to_master_id is not None
            target_id = action.to_master_id
            if target_id in reassign_targets:
                continue
            target = (
                CatalogMaster.all_tenants.select_for_update()
                .filter(tenant_id=master.tenant_id, id=target_id)
                .first()
            )
            if target is None:
                raise DeactivationError(
                    "fallback_not_found",
                    f"target master {target_id} not found in tenant",
                )
            if not target.is_active or target.archived_at is not None:
                raise DeactivationError(
                    "fallback_not_active",
                    f"target master {target_id} is archived/inactive",
                )
            if target.id == master.id:
                raise DeactivationError(
                    "fallback_invalid",
                    "cannot reassign to the master being deactivated",
                )
            reassign_targets[target_id] = target

        # Validate each reassign: target performs the service.
        for action in plan:
            if action.action != "reassign":
                continue
            booking = by_id[action.booking_id]
            assert action.to_master_id is not None
            target = reassign_targets[action.to_master_id]
            if booking.service_id is None:
                raise DeactivationError(
                    "service_missing",
                    f"booking {booking.id} has no service_id — cannot reassign",
                )
            if not MasterService.all_tenants.filter(
                tenant_id=master.tenant_id,
                master_id=target.id,
                service_id=booking.service_id,
            ).exists():
                raise DeactivationError(
                    "service_not_offered_by_target",
                    f"target master {target.id} does not perform service {booking.service_id}",
                )

        reassigned_count = 0
        cancelled_count = 0

        # Apply each action.
        for action in plan:
            booking = by_id[action.booking_id]
            if action.action == "reassign":
                assert action.to_master_id is not None
                target = reassign_targets[action.to_master_id]
                rendered = _render_customer_notification(
                    booking,
                    old_master=master,
                    new_master=target,
                    template=custom_template,
                )
                msg_hash = _hash_text(rendered)

                # Use raw UPDATE so we don't trip the save() attribution
                # validator (the row's booking_source might be 'external'
                # already — same pattern as transitions.py).
                BookingRequest.all_tenants.filter(pk=booking.pk).update(
                    master_id=target.id,
                    master_name=target.name,
                )

                payload = {
                    "master_id": str(master.id),
                    "actor_id": str(actor.pk),
                    "actor_role": actor_role,
                    "tenant_id": str(master.tenant_id),
                    "booking_id": str(booking.id),
                    "from_master_id": str(master.id),
                    "to_master_id": str(target.id),
                    "visit_at": booking.visit_at.isoformat() if booking.visit_at else "",
                    "customer_notification_message_hash": msg_hash,
                }
                write_audit(
                    MASTER_BOOKINGS_REASSIGNED,
                    target="booking.BookingRequest",
                    target_id=booking.id,
                    payload=payload,
                    actor_id=actor.pk,
                )
                emit(MASTER_BOOKINGS_REASSIGNED, properties=payload)

                bu = booking.bot_user
                pending_customer_notifications.append(
                    _PendingNotification(
                        chat_id=bu.chat_id if bu else None,
                        text=rendered,
                        hash_=msg_hash,
                        booking_id=str(booking.id),
                        branch="reassign",
                    )
                )
                reassigned_count += 1

            else:  # cancel
                rendered = _render_customer_notification(
                    booking,
                    old_master=master,
                    new_master=None,
                    template=custom_template,
                )
                msg_hash = _hash_text(rendered)

                # Cancel via the established transition chain. The
                # actor is the booking's customer (bot_user) — the
                # transitions module enforces ownership, so we pass
                # the customer's BotUser, not the admin actor. The
                # MM5 audit slug below records WHO triggered the
                # cascade.
                if booking.bot_user is None:
                    # Defensive — webhook-only bookings have no bot_user.
                    # Skip the transition chain and flip the row directly.
                    BookingRequest.all_tenants.filter(pk=booking.pk).update(
                        status=BookingRequest.Status.CANCELLED,
                    )
                else:
                    try:
                        request_cancel(
                            booking,
                            actor=booking.bot_user,
                            reason_class="master_deactivated",
                            reason_text=f"master {master.id} deactivated",
                        )
                        booking.refresh_from_db()
                        commit_cancel(booking, actor=booking.bot_user)
                    except InvalidBookingTransition as exc:
                        raise DeactivationError(
                            "booking_transition_failed",
                            f"booking {booking.id}: {exc.detail}",
                            status=409,
                        ) from exc

                payload = {
                    "master_id": str(master.id),
                    "actor_id": str(actor.pk),
                    "actor_role": actor_role,
                    "tenant_id": str(master.tenant_id),
                    "booking_id": str(booking.id),
                    "visit_at": booking.visit_at.isoformat() if booking.visit_at else "",
                    "customer_notification_message_hash": msg_hash,
                }
                write_audit(
                    MASTER_BOOKINGS_CANCELLED,
                    target="booking.BookingRequest",
                    target_id=booking.id,
                    payload=payload,
                    actor_id=actor.pk,
                )
                emit(MASTER_BOOKINGS_CANCELLED, properties=payload)

                bu = booking.bot_user
                pending_customer_notifications.append(
                    _PendingNotification(
                        chat_id=bu.chat_id if bu else None,
                        text=rendered,
                        hash_=msg_hash,
                        booking_id=str(booking.id),
                        branch="cancel",
                    )
                )
                cancelled_count += 1

        # Flip the master itself.
        now = timezone.now()
        master.is_active = False
        master.archived_at = now
        master.archive_reason = reason or ""
        master.save(update_fields=["is_active", "archived_at", "archive_reason"])

        # Terminal audit + emit.
        terminal_payload = {
            "master_id": str(master.id),
            "actor_id": str(actor.pk),
            "actor_role": actor_role,
            "tenant_id": str(master.tenant_id),
            "reason": reason or "",
            "reassigned_count": reassigned_count,
            "cancelled_count": cancelled_count,
        }
        write_audit(
            MASTER_DEACTIVATED,
            target="catalog.CatalogMaster",
            target_id=master.id,
            payload=terminal_payload,
            actor_id=actor.pk,
        )
        emit(MASTER_DEACTIVATED, properties=terminal_payload)

        # Prepare master-side DMs (one per unique reassign target).
        if notify_reassigned_masters and reassign_targets:
            # Count bookings per target for the DM body.
            counts: dict[str, int] = {}
            for action in plan:
                if action.action == "reassign" and action.to_master_id:
                    counts[action.to_master_id] = counts.get(action.to_master_id, 0) + 1
            for target_id, target in reassign_targets.items():
                if target.linked_bot_user_id is None:
                    continue
                bu = BotUser.all_tenants.filter(pk=target.linked_bot_user_id).first()
                if bu is None or not bu.chat_id:
                    continue
                n_inherited = counts.get(target_id, 0)
                text = (
                    f"Здравствуйте! К вам перевели {n_inherited} "
                    f"запис(и/ей) от {_first_name(master.name)} — "
                    "она/он больше не работает в студии. "
                    "Полный список в Mini App."
                )
                pending_master_notifications.append(
                    _PendingMasterNotification(
                        chat_id=bu.chat_id,
                        text=text,
                        master_id=target_id,
                    )
                )

    # Dispatch on commit — failures non-fatal.
    customer_dispatched = 0
    master_dispatched = 0

    def _dispatch_customer_notifications() -> None:
        nonlocal customer_dispatched
        for pn in pending_customer_notifications:
            if not pn.chat_id:
                logger.info("mm5.notify.skip booking=%s reason=no_chat_id", pn.booking_id)
                continue
            try:
                send_message(chat_id=pn.chat_id, text=pn.text)
                customer_dispatched += 1
            except MaxAPIError as exc:
                logger.warning(
                    "mm5.notify.customer_failed booking=%s status=%s",
                    pn.booking_id,
                    exc.status_code,
                )

    def _dispatch_master_notifications() -> None:
        nonlocal master_dispatched
        for pn in pending_master_notifications:
            try:
                send_message(chat_id=pn.chat_id, text=pn.text)
                master_dispatched += 1
            except MaxAPIError as exc:
                logger.warning(
                    "mm5.notify.master_failed master=%s status=%s",
                    pn.master_id,
                    exc.status_code,
                )

    # Hook AFTER the transaction is committed. For tests not in a
    # transaction-spanning fixture (the default Django TestCase), this
    # fires immediately at exit of the atomic block above.
    transaction.on_commit(_dispatch_customer_notifications)
    transaction.on_commit(_dispatch_master_notifications)

    # After on_commit hooks complete, the counters are accurate (in
    # tests with on_commit fired immediately). In a request that
    # remains in an outer transaction, the counts may be 0 here — the
    # response is best-effort.
    return DeactivationResult(
        master_id=str(master.id),
        is_active=master.is_active,
        archived_at=master.archived_at,
        reassigned_count=reassigned_count,
        cancelled_count=cancelled_count,
        customer_notifications_dispatched=customer_dispatched,
        master_notifications_dispatched=master_dispatched,
    )


# --- public API: reactivate -----------------------------------------------


def reactivate_master(
    master: CatalogMaster,
    *,
    notify_master: bool,
    actor: BotUser,
    actor_role: str,
) -> ReactivationResult:
    """Flip ``is_active=False, archived_at!=None`` → active again.

    Per spec §819-837 reactivation flow. WorkingHours / MasterService
    rows are intentionally untouched — they were never cascade-deleted
    at deactivation time, so reactivation is a pure flag flip.
    """

    if master.is_active and master.archived_at is None:
        raise DeactivationError("already_active", "master is already active")

    pending_master_dm: _PendingMasterNotification | None = None

    with transaction.atomic():
        master = CatalogMaster.all_tenants.select_for_update().get(pk=master.pk)
        if master.is_active and master.archived_at is None:
            raise DeactivationError("already_active", "master is already active")

        master.is_active = True
        master.archived_at = None
        master.archive_reason = ""
        master.save(update_fields=["is_active", "archived_at", "archive_reason"])

        payload = {
            "master_id": str(master.id),
            "actor_id": str(actor.pk),
            "actor_role": actor_role,
            "tenant_id": str(master.tenant_id),
            "notified_master": False,  # flipped below if we queue a DM
        }
        if notify_master and master.linked_bot_user_id is not None:
            bu = BotUser.all_tenants.filter(pk=master.linked_bot_user_id).first()
            if bu is not None and bu.chat_id:
                pending_master_dm = _PendingMasterNotification(
                    chat_id=bu.chat_id,
                    text=REACTIVATION_NOTIFICATION_TEXT,
                    master_id=str(master.id),
                )
                payload["notified_master"] = True

        write_audit(
            MASTER_REACTIVATED,
            target="catalog.CatalogMaster",
            target_id=master.id,
            payload=payload,
            actor_id=actor.pk,
        )
        emit(MASTER_REACTIVATED, properties=payload)

    notified = False

    def _dispatch() -> None:
        nonlocal notified
        if pending_master_dm is None:
            return
        try:
            send_message(chat_id=pending_master_dm.chat_id, text=pending_master_dm.text)
            notified = True
        except MaxAPIError as exc:
            logger.warning(
                "mm5.reactivate.notify_failed master=%s status=%s",
                pending_master_dm.master_id,
                exc.status_code,
            )

    transaction.on_commit(_dispatch)

    return ReactivationResult(
        master_id=str(master.id),
        is_active=master.is_active,
        archived_at=master.archived_at,
        notified_master=notified,
    )


__all__ = [
    "BookingAction",
    "DEFAULT_CUSTOMER_NOTIFICATION_TEMPLATE",
    "DeactivationError",
    "DeactivationPreview",
    "DeactivationResult",
    "FallbackCandidate",
    "FutureBookingPreview",
    "ReactivationResult",
    "execute_deactivation",
    "preview_deactivation",
    "reactivate_master",
]
# Hint to silence unused-field warnings when callers don't need field()
_ = field
