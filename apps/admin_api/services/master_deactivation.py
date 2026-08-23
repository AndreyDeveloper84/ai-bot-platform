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
from apps.booking.models import BookingRequest, RemoteBookingProxy
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
from apps.notifications.proactive import consent_blocker, vet_outbound

logger = logging.getLogger(__name__)


# --- public constants -----------------------------------------------------

MAX_FALLBACK_CANDIDATES = 5
"""Cap fallback candidates per booking — keeps payload small + UI scannable."""


MIRROR_LIVE_STATUSES = (
    RemoteBookingProxy.Status.CONFIRMED,
    RemoteBookingProxy.Status.PENDING_PAYMENT,
    RemoteBookingProxy.Status.TENTATIVE,
)
"""Mirror statuses that still represent a visit somebody expects to happen.

``cancelled`` / ``completed`` / ``no_show`` are settled — deactivating a
master does not disturb them.
"""


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
    """Result of :func:`preview_deactivation`.

    ``future_bookings`` is the **actionable** set: rows the cascade can
    reassign or cancel. ``mirror_future_bookings`` is what the Ayla
    mirror says the master actually has coming up. When the second
    exceeds the first, this screen cannot see everything it is about to
    destroy — see :attr:`inventory_complete`.
    """

    master_id: str
    master_name: str
    is_active: bool
    archived_at: datetime | None
    future_bookings: list[FutureBookingPreview]
    total_future_bookings: int
    bookings_with_fallback: int
    bookings_without_fallback: int
    #: Live future visits for this master according to the Ayla mirror
    #: (:class:`RemoteBookingProxy`), independent of what the cascade can act on.
    mirror_future_bookings: int = 0
    #: False when the mirror knows about more live future visits than the
    #: actionable set contains. Deactivation is refused while False.
    inventory_complete: bool = True


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
    #: Clients whose booking was moved or cancelled but who were NOT
    #: written to, because the consent gate or the outbound safety check
    #: refused (DRF-1307). Non-zero means somebody's visit changed and
    #: only a human can tell them — the per-booking audit rows carry the
    #: reason and the booking id.
    customer_notifications_blocked: int = 0


@dataclass(frozen=True)
class ReactivationResult:
    master_id: str
    is_active: bool
    archived_at: datetime | None
    notified_master: bool


@dataclass
class _PendingNotification:
    """One customer DM to fire on commit (rendered text + hash + target).

    ``blocked_reason`` is decided at PLAN time, inside the same
    transaction that reassigns or cancels the booking, and lands in the
    per-booking audit row (DRF-1307). Deciding it here rather than in
    the ``on_commit`` dispatcher is what makes the decision auditable: a
    client whose visit was moved or cancelled and who was *not* told
    still needs telling, by a human, and the audit row is where an
    operator finds out who that is and why.
    """

    chat_id: str | None
    text: str
    hash_: str
    booking_id: str
    branch: str  # "reassign" or "cancel"
    blocked_reason: str | None = None


@dataclass
class _PendingMasterNotification:
    chat_id: str
    text: str
    master_id: str
    blocked_reason: str | None = None


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


def _customer_notification_blocker(bot_user, rendered: str) -> str | None:
    """Why this client must not be written to, or ``None`` when they may be.

    DRF-1307. Four conditions live in
    :mod:`apps.notifications.proactive` — the person's global "do not
    write to me first", a GDPR erasure, the 152-ФЗ welcome consent, and
    an *active* ``ConsentRecord`` rather than the denormalised
    ``consent_at`` column that a withdrawal never clears. Two more are
    local to this cascade and are checked first because they are cheaper
    and because "we have no address for them" is a different operator
    problem from "they said no".

    The text is vetted last, once the recipient has cleared. The order
    matters for the audit trail: a row that reads ``opt_out`` says the
    client refused, a row that reads ``outbound_safety_contact`` says
    the administrator's own text was the problem — and only one of those
    is fixable by rewriting the message.

    That last one is not hypothetical here. The message body is written
    by an administrator, free-form, up to 4000 characters, and is sent
    verbatim to every affected client. It is the only bot-initiated text
    in the platform that no engineer has read. It also interpolates
    ``master.name`` from the catalogue, which is staff-edited mirror
    text — the same untrusted-name path DRF-1301 found in the follow-up
    beat.
    """

    if bot_user is None:
        return "no_bot_user"
    if not (getattr(bot_user, "chat_id", "") or "").strip():
        return "no_chat_id"
    blocked = consent_blocker(bot_user)
    if blocked:
        return blocked
    _, text_blocked = vet_outbound(rendered)
    return text_blocked


def _master_notification_blocker(bot_user, text: str) -> str | None:
    """Why a staff DM must not go out, or ``None`` when it may.

    Deliberately **not** the customer gate (DRF-1307). Two of the four
    customer conditions do not transfer:

    * ``consent_at`` / ``ConsentRecord`` — the 152-ФЗ welcome consent is
      the basis for talking to a *client*. A master being told that N of
      somebody else's bookings just landed on their calendar is an
      operational notice under a working relationship, and gating it on
      a consent nobody collects from staff would silently stop masters
      finding out about work they are now responsible for. That failure
      is worse than the one it prevents.
    * ``proactive_messages_opt_out`` — same argument, and it is a
      customer-facing switch set from a customer-facing skill.

    Both are raised as open questions in ``docs/REPORT_DRF1307.md``
    rather than decided here: whether staff DMs need a consent basis of
    their own is an owner call, not something to settle inside a bug fix.

    What does transfer:

    * ``deleted_at`` — an erasure request is unambiguous whoever made it,
      and ``soft_delete_user()`` leaves ``chat_id`` in place, so without
      this an erased row stays a recipient.
    * the outbound safety check — this text interpolates
      ``master.name`` from the catalogue, which is staff-edited mirror
      text. A master name carrying a phone number trips the ``contact``
      shape, which is the check doing its job rather than a false
      positive (DRF-1039).
    """

    if bot_user is None:
        return "no_bot_user"
    if getattr(bot_user, "deleted_at", None) is not None:
        return "deleted"
    _, text_blocked = vet_outbound(text)
    return text_blocked


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


def _count_future_mirror_bookings(master: CatalogMaster) -> int:
    """Live future visits for this master per the Ayla mirror.

    ``RemoteBookingProxy`` is bot-platform's read-cache of Ayla's
    canonical ``Appointment`` (ADR-0009). It is the only local table that
    reliably carries the master link for bookings that came from Ayla,
    which is what makes it the honest answer to «does this master have
    anything coming up».
    """

    return RemoteBookingProxy.all_tenants.filter(
        tenant_id=master.tenant_id,
        specialist_id=master.id,
        status__in=MIRROR_LIVE_STATUSES,
        start_at__gte=timezone.now(),
    ).count()


def _assess_inventory(master: CatalogMaster, actionable_count: int) -> tuple[int, bool]:
    """Return ``(mirror_future_count, inventory_complete)``.

    Complete means the actionable set is at least as large as what the
    mirror reports, so acting on it cannot leave a live visit stranded on
    an archived master. A mirror that lags behind (fewer rows than
    actionable) is safe in the other direction — we would move more than
    strictly necessary, never fewer.

    There is deliberately no per-row reconciliation: ``BookingRequest``
    carries no Ayla appointment id, so the two sets cannot be joined by
    key. Counting is the strongest claim the data supports, and claiming
    more than the data supports is the bug being fixed here.
    """

    mirror_count = _count_future_mirror_bookings(master)
    return mirror_count, mirror_count <= actionable_count


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

    Reports two counts, not one (DRF-1139). ``total_future_bookings`` is
    what the cascade can act on; ``mirror_future_bookings`` is what the
    Ayla mirror says the master actually has. On the pilot the first was
    0 for every master while the second was not — every
    ``BookingRequest`` row there has ``master_id IS NULL``, so the
    actionable query returns nothing regardless of the data. The screen
    said «0 future bookings» and offered an irreversible button on top of
    that. Both numbers now travel to the UI, and a shortfall blocks
    :func:`execute_deactivation`.
    """

    bookings = _query_future_confirmed_bookings(master)
    previews = [_build_booking_preview(b, deactivating_master_id=master.id) for b in bookings]

    with_fb = sum(1 for p in previews if p.fallback_masters)
    without_fb = len(previews) - with_fb

    mirror_count, inventory_complete = _assess_inventory(master, len(previews))
    if not inventory_complete:
        logger.warning(
            "master_deactivation.inventory_incomplete master=%s actionable=%d mirror=%d",
            master.id,
            len(previews),
            mirror_count,
        )

    payload = {
        "master_id": str(master.id),
        "actor_id": str(actor.pk),
        "actor_role": actor_role,
        "tenant_id": str(master.tenant_id),
        "future_bookings_count": len(previews),
        "mirror_future_bookings": mirror_count,
        "inventory_complete": inventory_complete,
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
        mirror_future_bookings=mirror_count,
        inventory_complete=inventory_complete,
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

        # DRF-1139 — refuse to act on an inventory we know is short.
        #
        # The plan is built from what the preview could see. If the Ayla
        # mirror reports more live future visits than the actionable set
        # holds, then covering every id in the plan does NOT mean every
        # client has been taken care of, and archiving the master here
        # would strand the difference on somebody who no longer works.
        #
        # This is checked under the same lock as the cascade, not just at
        # preview time: an inbound `booking.created` for this master
        # between preview and commit must stop the commit too.
        #
        # Switching the preview's data source alone would not have been
        # enough. An empty result from a correct query and an empty result
        # from a broken one look identical on screen, and the action they
        # green-light is irreversible — so the check refuses rather than
        # reports.
        mirror_count, inventory_complete = _assess_inventory(master, len(current_bookings))
        if not inventory_complete:
            raise DeactivationError(
                "inventory_incomplete",
                (
                    f"Ayla mirror reports {mirror_count} live future visit(s) for this "
                    f"master but only {len(current_bookings)} can be reassigned or "
                    "cancelled from here. Deactivating now would strand the difference. "
                    "Resolve those visits in Ayla first."
                ),
                status=409,
            )

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

                bu = booking.bot_user
                notify_blocked = _customer_notification_blocker(bu, rendered)

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
                    "customer_notification_blocked": notify_blocked or "",
                }
                write_audit(
                    MASTER_BOOKINGS_REASSIGNED,
                    target="booking.BookingRequest",
                    target_id=booking.id,
                    payload=payload,
                    actor_id=actor.pk,
                )
                emit(MASTER_BOOKINGS_REASSIGNED, properties=payload)

                pending_customer_notifications.append(
                    _PendingNotification(
                        chat_id=bu.chat_id if bu else None,
                        text=rendered,
                        hash_=msg_hash,
                        booking_id=str(booking.id),
                        branch="reassign",
                        blocked_reason=notify_blocked,
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

                bu = booking.bot_user
                notify_blocked = _customer_notification_blocker(bu, rendered)

                payload = {
                    "master_id": str(master.id),
                    "actor_id": str(actor.pk),
                    "actor_role": actor_role,
                    "tenant_id": str(master.tenant_id),
                    "booking_id": str(booking.id),
                    "visit_at": booking.visit_at.isoformat() if booking.visit_at else "",
                    "customer_notification_message_hash": msg_hash,
                    "customer_notification_blocked": notify_blocked or "",
                }
                write_audit(
                    MASTER_BOOKINGS_CANCELLED,
                    target="booking.BookingRequest",
                    target_id=booking.id,
                    payload=payload,
                    actor_id=actor.pk,
                )
                emit(MASTER_BOOKINGS_CANCELLED, properties=payload)

                pending_customer_notifications.append(
                    _PendingNotification(
                        chat_id=bu.chat_id if bu else None,
                        text=rendered,
                        hash_=msg_hash,
                        booking_id=str(booking.id),
                        branch="cancel",
                        blocked_reason=notify_blocked,
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
                        blocked_reason=_master_notification_blocker(bu, text),
                    )
                )

    # Dispatch on commit — failures non-fatal.
    customer_dispatched = 0
    master_dispatched = 0
    customer_blocked = sum(1 for pn in pending_customer_notifications if pn.blocked_reason)

    def _dispatch_customer_notifications() -> None:
        nonlocal customer_dispatched
        for pn in pending_customer_notifications:
            if pn.blocked_reason:
                # Decided at plan time and already in the audit row; logged
                # again here so the reason is visible in the same stream as
                # the sends it sits between.
                logger.info(
                    "mm5.notify.skip booking=%s reason=%s",
                    pn.booking_id,
                    pn.blocked_reason,
                )
                continue
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
            if pn.blocked_reason:
                logger.info(
                    "mm5.notify.master_skip master=%s reason=%s",
                    pn.master_id,
                    pn.blocked_reason,
                )
                continue
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
        customer_notifications_blocked=customer_blocked,
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
                blocked = _master_notification_blocker(bu, REACTIVATION_NOTIFICATION_TEXT)
                pending_master_dm = _PendingMasterNotification(
                    chat_id=bu.chat_id,
                    text=REACTIVATION_NOTIFICATION_TEXT,
                    master_id=str(master.id),
                    blocked_reason=blocked,
                )
                payload["notified_master"] = blocked is None
                payload["master_notification_blocked"] = blocked or ""

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
        if pending_master_dm.blocked_reason:
            logger.info(
                "mm5.reactivate.notify_skip master=%s reason=%s",
                pending_master_dm.master_id,
                pending_master_dm.blocked_reason,
            )
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
