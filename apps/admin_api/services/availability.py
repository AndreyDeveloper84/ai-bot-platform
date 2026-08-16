"""Admin approve/reject of master availability requests (M3-admin / Bundle B).

Per master-mobile §M3 line 458::

    «Mark unavailable / off-day: master taps empty slot → sheet
    «Помечу как недоступно» → confirmation → server marks slot blocked
    → owner notified (audit + bot DM)»

Per master-mobile §M3 line 1170::

    «Returns pending request; owner approves async.»

Master-side endpoint (PR Tier1.2) creates a PENDING
:class:`apps.scheduling.models.ScheduleChangeRequest` row + DMs the
salon manager. THIS module is the admin-side complement: list pending,
approve (materialise into :class:`apps.scheduling.models.ScheduleException`
day_off rows + DM the master), or reject (annotate + DM the master).

### Atomic + on_commit pattern

DB mutations (status flip + ScheduleException materialisation) live
inside ``transaction.atomic`` with a ``select_for_update`` row-lock on
the request. The side-effect dispatch (master MAX DM) is hooked via
``transaction.on_commit`` so a failed MAX call CAN'T roll back the
decision. Same shape as :mod:`apps.admin_api.services.master_deactivation`.

### Overlap re-check at decision time

Between master submission and admin decision a second approval could
have created a ScheduleException covering the same date(s) (another
master's vacation request resolved first → catalog-wide reshuffle).
We re-check overlap at decision time and raise ``overlap_conflict``
when the new request would create a duplicate (master, date) clash on
any of its covered dates. The original create-time check (PR Tier1.2
``request_availability_change``) is a cheap pre-flight; the decision-
time check is the authoritative one.

### Materialisation rule

Per spec §M3 line 458 «mark unavailable» is the only master-side
capability — NOT «edit hours». We materialise one
:class:`ScheduleException` row per local calendar date the window
touches, ``type=day_off``. CUSTOM_HOURS (partial-day with custom
times) is NOT produced — the master interface doesn't offer it. A
sub-24h window still materialises a day_off for the date it sits on;
this is the simpler MVP per the §M3 wireframe (master taps a slot →
«mark unavailable» yields a whole-day block, not a precise time
range). The free-form ``reason_text`` from the master flows into the
materialised row's ``reason`` field for the salon team.

### Idempotency on materialisation

If a ``ScheduleException`` already exists for one of the covered
dates (seeded by another flow, or a re-attempt after a previously-
rolled-back decision), :meth:`update_or_create` overwrites the type
+ reason. Per :class:`ScheduleException` docstring line 238:
«new exception OVERWRITES the previous one for that date».
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as date_cls, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Q
from django.utils import timezone as dj_timezone

from apps.audit.services import write_audit
from apps.catalog.models import CatalogMaster
from apps.events.services import emit
from apps.events.vocabulary import (
    ADMIN_AVAILABILITY_APPROVED,
    ADMIN_AVAILABILITY_REJECTED,
)
from apps.master_api.services.schedule import get_tenant_tz
from apps.scheduling.models import ScheduleChangeRequest, ScheduleException

logger = logging.getLogger(__name__)


# --- constants ------------------------------------------------------------

MAX_REJECTION_REASON_LEN = 500
"""Cap on the admin's free-form rejection note. Mirrors the model's
``resolution_note`` TextField (no DB cap) but kept tight for the audit
payload size budget (8 KB cap in :func:`write_audit`)."""

DEFAULT_LIST_LIMIT = 25
MAX_LIST_LIMIT = 50

STATUS_PENDING_FILTER = "pending"
STATUS_DECIDED_FILTER = "decided"
STATUS_ALL_FILTER = "all"
VALID_STATUS_FILTERS = frozenset({STATUS_PENDING_FILTER, STATUS_DECIDED_FILTER, STATUS_ALL_FILTER})


# --- errors ---------------------------------------------------------------


def _block_time_in_ayla(
    *,
    master,
    tenant_id,
    start_at,
    end_at,
    reason: str,
) -> None:
    """Write the approved absence into Ayla, the system of record (DRF-1062).

    ``CatalogMaster.id`` IS the Ayla specialist id — the catalog mirror
    upserts with ``id=dto.ayla_master_id``. ``ayla_user_id`` is the Ayla
    *User* id and would address nobody here.

    The exact requested interval is sent, not the whole covered days. The
    local ``ScheduleException`` model is date-keyed and therefore rounds a
    partial-day request up to full days; Ayla's ``SpecialistTimeOff`` is an
    interval and can hold what the master actually asked for. Blocking
    more time than requested would quietly cost the salon bookings.
    """
    from django.conf import settings

    if not getattr(settings, "BOOKING_VIA_AYLA_REST", False):
        # Flag OFF: this deployment's schedule really is the local one —
        # the customer picker computes slots from apps.scheduling and
        # bookings are written locally too. Reaching into Ayla there would
        # block time in a system this deployment does not book against.
        return

    from apps.integrations.ayla.booking_client import (
        BookingAPIError,
        ScheduleBlockConflictError,
        get_ayla_booking_client,
    )

    try:
        get_ayla_booking_client().create_specialist_time_off(
            specialist_id=str(master.id),
            tenant_id=str(tenant_id),
            start_at=start_at.isoformat(),
            end_at=end_at.isoformat(),
            reason=reason,
        )
    except ScheduleBlockConflictError as exc:
        # Not a failure of the approval — the time is booked. Say so, so
        # the administrator can settle those bookings on the surface built
        # for it instead of guessing why "approve" did nothing.
        raise AvailabilityDecisionError(
            "has_active_appointments",
            "There are active bookings in this period. Resolve them before approving the time off.",
            status=409,
        ) from exc
    except BookingAPIError as exc:
        # An outage must not silently approve a day off that never reached
        # the schedule; refuse and leave the request pending.
        logger.warning(
            "availability.ayla_block_failed master=%s err=%s",
            master.id,
            type(exc).__name__,
        )
        raise AvailabilityDecisionError(
            "schedule_unavailable",
            "The booking system is temporarily unavailable. Try again.",
            status=503,
        ) from exc


class AvailabilityDecisionError(Exception):
    """Decision-time validation failure with a stable HTTP-friendly slug.

    Attributes:
      slug: stable error slug for the JSON envelope.
      detail: human-readable explanation.
      status: HTTP status code the view should return.
    """

    def __init__(self, slug: str, detail: str, status: int = 400) -> None:
        super().__init__(detail)
        self.slug = slug
        self.detail = detail
        self.status = status


# --- public helpers -------------------------------------------------------


def _resolved_by_name(user: Any) -> str:
    """Owner / admin Django User → display name. Fallback to username."""

    if user is None:
        return ""
    full = (user.get_full_name() or "").strip()
    if full:
        return full
    return (user.username or "").strip()


def _decided_by_name_from_bot_user(bot_user_id: UUID | None) -> str:
    """Resolve a BotUser id → display name for the audit-trail surface.

    PR #521 adversarial blocker #3: ``ScheduleChangeRequest.resolved_by``
    (Django User FK) is NULL today because the admin Mini App auth
    threads through BotUser only. Without this lookup the list endpoint
    rendered an empty ``decided_by_name`` for every decided request,
    breaking the audit trail. We resolve from the new
    ``resolved_by_bot_user_id`` column directly.

    Returns the BotUser's ``display_name`` / ``client_name`` /
    ``channel_user_id`` (best available), or ``""`` if the BotUser row
    has been deleted.
    """

    if bot_user_id is None:
        return ""
    # Local import — keeps the identity app out of the import graph for
    # callers that never need it (e.g. the approve/reject services).
    from apps.identity.models import BotUser

    try:
        bu = BotUser.all_tenants.only("display_name", "client_name", "channel_user_id").get(
            id=bot_user_id
        )
    except BotUser.DoesNotExist:
        return ""
    return (
        (bu.display_name or "").strip()
        or (bu.client_name or "").strip()
        or (bu.channel_user_id or "").strip()
    )


def _format_date_human(d: date_cls) -> str:
    """Render a date for the master MAX DM in Russian locale.

    Hard-coded month abbreviations to avoid a server-locale dependency;
    aligns with the customer reminder message style.
    """

    months_ru = [
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    ]
    return f"{d.day} {months_ru[d.month - 1]}"


def _format_date_range_human(start_date: date_cls, end_date: date_cls) -> str:
    """Render a date or date range for the master DM (single-day or N-day)."""

    if start_date == end_date:
        return _format_date_human(start_date)
    return f"{_format_date_human(start_date)} — {_format_date_human(end_date)}"


def _covered_dates(start: datetime, end: datetime, tz: ZoneInfo) -> list[date_cls]:
    """Return the local-date list a UTC ``[start, end)`` window touches.

    The end is treated as exclusive at the millisecond level but a
    window that ends exactly at midnight local DOES cover the prior
    date only (a 24h ``[00:00, 00:00 next-day)`` window covers one
    date, not two). Implemented by walking from the start's local date
    to the latest local date strictly before ``end`` (inclusive).
    """

    start_local_date = start.astimezone(tz).date()
    # End-exclusive: subtract one microsecond so a 23:59:59.9999 → next
    # midnight window doesn't bleed into the next date.
    end_local = (end - timedelta(microseconds=1)).astimezone(tz)
    end_local_date = end_local.date()
    if end_local_date < start_local_date:
        # Degenerate (end before start). Caller should have validated;
        # belt-and-braces — return just the start date.
        return [start_local_date]
    dates: list[date_cls] = []
    cursor = start_local_date
    while cursor <= end_local_date:
        dates.append(cursor)
        cursor += timedelta(days=1)
    return dates


# --- list -----------------------------------------------------------------


def _decoded_cursor(cursor: str | None) -> tuple[str, str] | None:
    """Decode a base64 JSON cursor → (created_at_iso, id). None on bad input."""

    if not cursor:
        return None
    import base64
    import binascii
    import json

    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        data = json.loads(raw)
        return str(data["t"]), str(data["id"])
    except (binascii.Error, ValueError, KeyError, UnicodeDecodeError):
        return None


def _encoded_cursor(created_at: datetime, row_id: UUID) -> str:
    import base64
    import json

    raw = json.dumps({"t": created_at.isoformat(), "id": str(row_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def list_pending_for_admin(
    tenant_id: UUID,
    *,
    status: str = STATUS_PENDING_FILTER,
    master_id: UUID | None = None,
    cursor: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
) -> dict[str, Any]:
    """Paginated list of availability requests for admin review.

    Args:
      tenant_id: tenant scoping — required, defence in depth on top of
        the auth decorator's ``tenant_scope``.
      status: ``pending`` (default), ``decided``, or ``all``. Anything
        else raises :class:`AvailabilityDecisionError` with
        ``bad_request``.
      master_id: optional filter to a single master's requests.
      cursor: opaque base64 cursor from the previous page.
      limit: page size, clamped to 1..:data:`MAX_LIST_LIMIT`.

    Returns:
      ``{"items": [...], "next_cursor": str | None}``.

      Each item carries: ``request_id``, ``master_id``,
      ``master_name``, ``requested_start``, ``requested_end``,
      ``reason_class``, ``reason_text``, ``status``, ``created_at``,
      ``decided_at``, ``decided_by_name``, ``resolution_note``.
    """

    if status not in VALID_STATUS_FILTERS:
        raise AvailabilityDecisionError(
            "bad_request",
            f"status must be one of {sorted(VALID_STATUS_FILTERS)}",
            status=400,
        )
    if limit < 1:
        raise AvailabilityDecisionError("bad_request", "limit must be positive", status=400)
    limit = min(limit, MAX_LIST_LIMIT)

    qs = ScheduleChangeRequest.all_tenants.filter(tenant_id=tenant_id)

    if status == STATUS_PENDING_FILTER:
        qs = qs.filter(status=ScheduleChangeRequest.Status.PENDING)
    elif status == STATUS_DECIDED_FILTER:
        qs = qs.exclude(status=ScheduleChangeRequest.Status.PENDING)
    # status == ALL → no extra filter.

    if master_id is not None:
        qs = qs.filter(master_id=master_id)

    qs = qs.select_related("master", "resolved_by").order_by("-created_at", "-id")

    if cursor:
        decoded = _decoded_cursor(cursor)
        if decoded is None:
            raise AvailabilityDecisionError("bad_request", "invalid cursor", status=400)
        last_created_iso, last_id = decoded
        try:
            last_created_at = datetime.fromisoformat(last_created_iso)
        except ValueError as exc:
            raise AvailabilityDecisionError(
                "bad_request", "invalid cursor timestamp", status=400
            ) from exc
        # Strictly older than (created_at, id) so the page advances.
        qs = qs.filter(
            Q(created_at__lt=last_created_at) | (Q(created_at=last_created_at) & Q(id__lt=last_id))
        )

    rows = list(qs[: limit + 1])
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    items: list[dict[str, Any]] = []
    for row in rows:
        master_name = row.master.name if row.master_id else ""
        decided_at = row.resolved_at.isoformat() if row.resolved_at else None
        # PR #521 adversarial blocker #3: prefer the Django User FK
        # (Phase 2+ will populate it); fall back to the BotUser id
        # column which is populated today.
        if row.resolved_at:
            decided_by_name = _resolved_by_name(row.resolved_by)
            if not decided_by_name:
                decided_by_name = _decided_by_name_from_bot_user(row.resolved_by_bot_user_id)
        else:
            decided_by_name = ""
        items.append(
            {
                "request_id": str(row.id),
                "master_id": str(row.master_id) if row.master_id else None,
                "master_name": master_name,
                "requested_start": (
                    row.requested_start.isoformat() if row.requested_start else None
                ),
                "requested_end": (row.requested_end.isoformat() if row.requested_end else None),
                "reason_class": row.reason_class or "",
                "reason_text": row.reason_text or "",
                "status": row.status,
                "created_at": row.created_at.isoformat(),
                "decided_at": decided_at,
                "decided_by_name": decided_by_name,
                "resolution_note": row.resolution_note or "",
            }
        )

    next_cursor: str | None = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = _encoded_cursor(last.created_at, last.id)

    return {"items": items, "next_cursor": next_cursor}


# --- master DM dispatch ---------------------------------------------------


def _enqueue_master_dm_post_commit(
    *,
    master: CatalogMaster,
    request_id: UUID,
    decision: str,
    date_range_human: str,
    rejection_reason: str = "",
) -> None:
    """Enqueue the master DM via Celery — runs from ``transaction.on_commit``.

    PR #521 adversarial blocker #2 fix: previously this helper called
    ``send_message`` inline inside the HTTP request thread, blocking
    the API response and silently swallowing ``MaxAPIError``. Now we
    only enqueue a Celery task; the actual outbound MAX call runs in
    a worker with bounded retry and dead-letter observability (see
    :mod:`apps.admin_api.tasks`). The HTTP response returns immediately
    after the txn commits; MAX-side transient failures auto-retry; a
    sustained outage surfaces via Celery's failed-task path instead
    of an inline warning that nobody reads.
    """

    linked = master.linked_bot_user
    if linked is None or not (linked.chat_id or "").strip():
        logger.info(
            "admin_api.availability.no_master_chat_id master=%s request=%s",
            master.id,
            request_id,
        )
        return

    # Local import keeps the task module out of the import graph for
    # callers that never approve/reject (e.g. list endpoint), and avoids
    # a circular import between services and tasks.
    from apps.admin_api.tasks import dispatch_master_decision_dm

    # The enqueue call runs from ``transaction.on_commit`` AFTER the
    # DB commit lands. If the broker is unreachable here, we must NOT
    # let the exception propagate — the DB-level decision is already
    # durable and the API has already returned (or is about to). A
    # broker outage is an ops-visible event, not an API-caller error.
    try:
        dispatch_master_decision_dm.delay(
            chat_id=linked.chat_id.strip(),
            decision=decision,
            date_range_human=date_range_human,
            request_id=str(request_id),
            master_id=str(master.id),
            rejection_reason=rejection_reason,
        )
    except Exception:  # noqa: BLE001 — broker-side errors are ops-visible
        logger.warning(
            "admin_api.availability.enqueue_failed master=%s request=%s decision=%s",
            master.id,
            request_id,
            decision,
            exc_info=True,
        )


# --- approve --------------------------------------------------------------


@dataclass(frozen=True)
class DecisionResult:
    """Return value for approve / reject — convenient for view serialisation."""

    request: ScheduleChangeRequest
    materialised_dates: list[date_cls]


def approve_availability_request(
    *,
    request_id: UUID,
    tenant_id: UUID,
    actor: Any,
    actor_bot_user_id: UUID | None = None,
    actor_role: str = "",
    now: datetime | None = None,
) -> DecisionResult:
    """Approve a PENDING request and materialise into ScheduleException(s).

    Args:
      request_id: UUID of the :class:`ScheduleChangeRequest` to approve.
      tenant_id: tenant scoping — defence in depth above auth decorator.
      actor: Django User performing the approval (stored as
        ``resolved_by``).
      actor_bot_user_id: optional BotUser id of the actor for audit
        rows (the audit row's ``actor_id`` is the BotUser, not the
        Django User — same convention as MM5).
      actor_role: ``"owner"`` / ``"admin"`` for the audit payload.
      now: optional pinned timestamp for ``resolved_at``. Defaults to
        ``timezone.now()``.

    Raises:
      AvailabilityDecisionError:
        ``not_found`` (404) — request id missing or cross-tenant.
        ``already_decided`` (409) — status != PENDING.
        ``overlap_conflict`` (409) — a ScheduleException now occupies
          one or more covered dates with a CONFLICTING type. (We allow
          idempotent overwrite when overwriting an existing day_off,
          but a vacation row that already covers the date is treated
          as a hard conflict — the admin is approving overlapping
          off-time for the same master.)

    Returns:
      :class:`DecisionResult` carrying the updated request and the
      list of dates materialised.
    """

    if now is None:
        now = dj_timezone.now()

    with transaction.atomic():
        try:
            req = (
                ScheduleChangeRequest.all_tenants.select_for_update()
                .select_related("master", "tenant")
                .get(id=request_id, tenant_id=tenant_id)
            )
        except ScheduleChangeRequest.DoesNotExist as exc:
            raise AvailabilityDecisionError(
                "not_found", "availability request not found", status=404
            ) from exc

        if req.status != ScheduleChangeRequest.Status.PENDING:
            raise AvailabilityDecisionError(
                "already_decided",
                f"request is already {req.status}",
                status=409,
            )

        if req.requested_start is None or req.requested_end is None:
            # Defensive: PR Tier1.2 creates rows with both fields set,
            # but legacy ``requested_change``-only rows could surface
            # here. Reject — the admin endpoint can only materialise
            # typed windows.
            raise AvailabilityDecisionError(
                "bad_request",
                "request has no typed window (legacy row); cannot materialise",
                status=400,
            )

        master_id: UUID = req.master_id
        # PR #521 adversarial blocker #1: lock the master row in
        # addition to the request row. ``select_for_update`` on the
        # request alone serialises updates to a SINGLE request only.
        # Two admins approving two DIFFERENT pending requests for the
        # SAME master with overlapping date windows would both pass
        # their row-locks (different rows), both fail to find an
        # existing ScheduleException (neither has committed yet), and
        # both call ``update_or_create``. One wins the unique-on-
        # (master, date) race; the second raises IntegrityError → 500.
        # Locking the master row here forces serial execution of all
        # approval activity for that master, so the second approver
        # sees the freshly-committed ScheduleException and surfaces a
        # clean 409 ``overlap_conflict`` instead of a 500.
        try:
            CatalogMaster.all_tenants.select_for_update().only("id").get(id=master_id)
        except CatalogMaster.DoesNotExist as exc:  # pragma: no cover — FK guarantees existence
            raise AvailabilityDecisionError("not_found", "master not found", status=404) from exc

        master: CatalogMaster = req.master
        tz = get_tenant_tz(master.tenant)
        dates = _covered_dates(req.requested_start, req.requested_end, tz)

        # Overlap re-check: any pre-existing ScheduleException on one of
        # the covered dates that is NOT a day_off / event we're about to
        # overwrite is a conflict. day_off → day_off is idempotent
        # overwrite per ScheduleException docstring; vacation already on
        # the date is a true conflict (separate approval), abort.
        existing = list(
            ScheduleException.all_tenants.filter(
                tenant_id=tenant_id,
                master_id=master.id,
                date__in=dates,
            )
        )
        conflicting_dates: list[str] = []
        for exc_row in existing:
            # Allow idempotent overwrite of an existing DAY_OFF (the
            # same master previously had a self-marked day_off on this
            # date — re-issuing is benign). Anything else is a conflict.
            if exc_row.type != ScheduleException.Type.DAY_OFF:
                conflicting_dates.append(exc_row.date.isoformat())
        if conflicting_dates:
            raise AvailabilityDecisionError(
                "overlap_conflict",
                f"existing exceptions conflict on dates: {sorted(conflicting_dates)}",
                status=409,
            )

        # DRF-1062 — Ayla owns the schedule, so the approval lands there
        # FIRST. Written only into apps.scheduling it would change nothing
        # a customer can see: the customer picker reads slots from Ayla,
        # so the administrator would be told the day off was approved and
        # the day would stay on sale.
        #
        # Ordering is deliberate. If Ayla refuses, nothing local changes
        # and the request stays pending for a human to look at. If Ayla
        # succeeds and the local work then fails, the roll-back leaves a
        # block in Ayla with the request still pending — the schedule is
        # closed (the conservative direction) and a retry is idempotent
        # only in the sense that a second block would overlap harmlessly.
        # The reverse order could mark a request approved while the time
        # stayed bookable, which is the failure this task exists to end.
        _block_time_in_ayla(
            master=master,
            tenant_id=tenant_id,
            start_at=req.requested_start,
            end_at=req.requested_end,
            reason=req.reason_text or "",
        )

        # Materialise — one ScheduleException per covered date.
        materialised_dates: list[date_cls] = []
        for d in dates:
            ScheduleException.all_tenants.update_or_create(
                tenant_id=tenant_id,
                master=master,
                date=d,
                defaults={
                    "type": ScheduleException.Type.DAY_OFF,
                    "start_time": None,
                    "end_time": None,
                    "reason": req.reason_text or "",
                    "created_by": actor if getattr(actor, "pk", None) else None,
                },
            )
            materialised_dates.append(d)

        # Flip request status. ``resolved_by_bot_user_id`` is the
        # durable BotUser-side provenance handle (PR #521 adversarial
        # blocker #3) — populated even when no Django User row is
        # wired through (the dominant case while Phase 1 has no
        # BotUser↔User bridge).
        req.status = ScheduleChangeRequest.Status.APPROVED
        req.resolved_at = now
        req.resolved_by = actor if getattr(actor, "pk", None) else None
        req.resolved_by_bot_user_id = actor_bot_user_id
        req.resolution_note = ""
        req.save(
            update_fields=[
                "status",
                "resolved_at",
                "resolved_by",
                "resolved_by_bot_user_id",
                "resolution_note",
            ]
        )

        # Audit + event emit (both inside the txn so a roll-back nukes
        # both).
        payload = {
            "tenant_id": str(tenant_id),
            "master_id": str(master.id),
            "request_id": str(req.id),
            "actor_role": actor_role,
            "requested_start": (req.requested_start.isoformat() if req.requested_start else None),
            "requested_end": (req.requested_end.isoformat() if req.requested_end else None),
            "reason_class": req.reason_class or "",
            "materialised_dates": [d.isoformat() for d in materialised_dates],
        }
        write_audit(
            ADMIN_AVAILABILITY_APPROVED,
            target="scheduling.ScheduleChangeRequest",
            target_id=req.id,
            payload=payload,
            actor_id=actor_bot_user_id,
        )
        emit(ADMIN_AVAILABILITY_APPROVED, properties=payload)

        # Post-commit DM. Bind captured args via a nested function so
        # the closure freezes them at hook-creation time and mypy can
        # type-check the call site.
        date_range_human = _format_date_range_human(
            materialised_dates[0],
            materialised_dates[-1],
        )
        captured_master = master
        captured_request_id = req.id
        captured_range = date_range_human

        def _approve_dispatch() -> None:
            _enqueue_master_dm_post_commit(
                master=captured_master,
                request_id=captured_request_id,
                decision="approved",
                date_range_human=captured_range,
            )

        transaction.on_commit(_approve_dispatch)

    return DecisionResult(request=req, materialised_dates=materialised_dates)


# --- reject ---------------------------------------------------------------


def reject_availability_request(
    *,
    request_id: UUID,
    tenant_id: UUID,
    actor: Any,
    actor_bot_user_id: UUID | None = None,
    actor_role: str = "",
    rejection_reason: str,
    now: datetime | None = None,
) -> DecisionResult:
    """Reject a PENDING request with an admin-supplied reason.

    Args:
      rejection_reason: non-blank ≤500-char admin rationale. Stored in
        ``ScheduleChangeRequest.resolution_note`` and surfaced to the
        master in the DM.

    Raises:
      AvailabilityDecisionError:
        ``bad_request`` (400) — reason missing / blank / > 500 chars.
        ``not_found`` (404) — request id missing or cross-tenant.
        ``already_decided`` (409) — status != PENDING.
    """

    reason_stripped = (rejection_reason or "").strip()
    if not reason_stripped:
        raise AvailabilityDecisionError(
            "bad_request",
            "rejection_reason is required",
            status=400,
        )
    if len(reason_stripped) > MAX_REJECTION_REASON_LEN:
        raise AvailabilityDecisionError(
            "bad_request",
            f"rejection_reason exceeds {MAX_REJECTION_REASON_LEN} chars",
            status=400,
        )
    if now is None:
        now = dj_timezone.now()

    with transaction.atomic():
        try:
            req = (
                ScheduleChangeRequest.all_tenants.select_for_update()
                .select_related("master", "tenant")
                .get(id=request_id, tenant_id=tenant_id)
            )
        except ScheduleChangeRequest.DoesNotExist as exc:
            raise AvailabilityDecisionError(
                "not_found", "availability request not found", status=404
            ) from exc

        if req.status != ScheduleChangeRequest.Status.PENDING:
            raise AvailabilityDecisionError(
                "already_decided",
                f"request is already {req.status}",
                status=409,
            )

        master: CatalogMaster = req.master
        req.status = ScheduleChangeRequest.Status.REJECTED
        req.resolved_at = now
        req.resolved_by = actor if getattr(actor, "pk", None) else None
        req.resolved_by_bot_user_id = actor_bot_user_id
        req.resolution_note = reason_stripped
        req.save(
            update_fields=[
                "status",
                "resolved_at",
                "resolved_by",
                "resolved_by_bot_user_id",
                "resolution_note",
            ]
        )

        payload = {
            "tenant_id": str(tenant_id),
            "master_id": str(master.id),
            "request_id": str(req.id),
            "actor_role": actor_role,
            "requested_start": (req.requested_start.isoformat() if req.requested_start else None),
            "requested_end": (req.requested_end.isoformat() if req.requested_end else None),
            "reason_class": req.reason_class or "",
            "rejection_reason": reason_stripped,
        }
        write_audit(
            ADMIN_AVAILABILITY_REJECTED,
            target="scheduling.ScheduleChangeRequest",
            target_id=req.id,
            payload=payload,
            actor_id=actor_bot_user_id,
        )
        emit(ADMIN_AVAILABILITY_REJECTED, properties=payload)

        # Human date range for the DM. If both endpoints are present,
        # compute covered dates; else fall back to a generic phrase.
        if req.requested_start and req.requested_end:
            tz = get_tenant_tz(master.tenant)
            dates = _covered_dates(req.requested_start, req.requested_end, tz)
            date_range_human = _format_date_range_human(dates[0], dates[-1])
        else:
            date_range_human = "указанные даты"

        captured_master = master
        captured_request_id = req.id
        captured_reason = reason_stripped
        captured_range = date_range_human

        def _reject_dispatch() -> None:
            _enqueue_master_dm_post_commit(
                master=captured_master,
                request_id=captured_request_id,
                decision="rejected",
                date_range_human=captured_range,
                rejection_reason=captured_reason,
            )

        transaction.on_commit(_reject_dispatch)

    return DecisionResult(request=req, materialised_dates=[])


__all__ = [
    "AvailabilityDecisionError",
    "DEFAULT_LIST_LIMIT",
    "DecisionResult",
    "MAX_LIST_LIMIT",
    "MAX_REJECTION_REASON_LEN",
    "STATUS_ALL_FILTER",
    "STATUS_DECIDED_FILTER",
    "STATUS_PENDING_FILTER",
    "VALID_STATUS_FILTERS",
    "approve_availability_request",
    "list_pending_for_admin",
    "reject_availability_request",
]
