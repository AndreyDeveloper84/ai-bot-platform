"""Master mobile §M3 schedule + availability-change service layer.

Pure-function helpers powering the three master-side schedule endpoints
introduced for PR Tier1.2 (master-mobile §M3, lines 380–478 of
``docs/design/handoffs/2026-05-18-master-mobile-handoff.md``):

* ``GET /api/v1/master/schedule`` — own bookings + working hours +
  approved time-off blocks + free windows + conflict surfacing over a
  bounded date range.
* ``POST /api/v1/master/availability`` — master proposes an off-time
  window for owner approval.
* ``GET /api/v1/master/availability/pending`` — master's own pending
  + recently-decided availability requests.

Spec quote (§M3 line 458): «**Mark unavailable / off-day**: master
taps empty slot → sheet `Помечу как недоступно` → confirmation →
server marks slot blocked → owner notified (audit + bot DM)»

Spec quote (§M3 line 466): «Conflict (admin double-booked you): «⚠
Конфликт расписания — посмотрите» + tap → conversation with admin /
banner with «уточнить у админа» button»

### Why pure helpers (not Django views)

Same pattern as :mod:`apps.master_api.services.dashboard`. The HTTP
layer is a 10-line view that calls one of these helpers and JSON-
serialises the result. Test surface is the helpers, not the views —
pinning ``now`` and tenant + master directly avoids the overhead of
constructing a full Django request.

### Tenant TZ contract

Date-range boundaries (``from`` / ``to``) are interpreted in the
tenant's IANA timezone (:attr:`apps.tenancy.models.Tenant.timezone`,
defaulting to ``Europe/Moscow``). Day boundaries within the response
follow the same convention so masters in MSK never see a tomorrow-
morning slot bleed into today.

### Conflict-detection rationale

Spec §M3 line 466 flags «Конфликт расписания» as a banner the master
must see. Three kinds are surfaced defensively even though
:class:`apps.booking.models.BookingRequest` has DB-level uniqueness
(PR #192's partial-UNIQUE on ``(master, visit_at)``):

* ``double_booking`` — two CONFIRMED bookings whose [visit_at,
  visit_at+duration] intervals overlap. The DB constraint protects
  against equal ``visit_at``; this catches the case where booking B
  starts mid-way through booking A's duration.
* ``outside_hours`` — booking whose interval is partly or wholly
  outside the master's working hours for that weekday.
* ``overlapping_exception`` — booking inside an approved time-off /
  custom-hours exception.

All three are «admin error» banners. The master's only action is
«уточнить у админа» — they CANNOT edit bookings from the M3 screen.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date as date_cls, datetime, time, timedelta, timezone as dt_timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone as dj_timezone

from apps.booking.models import RemoteBookingProxy
from apps.catalog.models import CatalogMaster, CatalogService
from apps.identity.models import BotUser
from apps.master_api.services.visit_source import (
    UPCOMING_STATUSES,
    VisitRow,
    master_visits,
)
from apps.scheduling.models import (
    ScheduleChangeRequest,
    ScheduleException,
    WorkingHours,
)

logger = logging.getLogger(__name__)


# --- constants ---------------------------------------------------------

MAX_RANGE_DAYS = 31
"""Hard cap on (to - from). Anything wider is 400."""

DEFAULT_RANGE_DAYS = 7
"""When ``to`` is omitted, default to ``from + 7d``."""

DEFAULT_SERVICE_DURATION_MIN = 60
"""Fallback duration for bookings missing both
``duration_min`` and a linked ``CatalogService``. Same fallback as
:mod:`apps.master_api.services.dashboard`."""

FREE_WINDOW_MIN_GAP_MIN = 30
"""Minimum gap to surface as a free window. Anything shorter is a
sliver the master can't realistically slot into. Aligns with
:data:`apps.master_api.services.dashboard.NEXT_FREE_WINDOW_MIN_GAP_MIN`
so the M1 dashboard and M3 schedule advertise the same notion of
«окно»."""

RECENT_DECIDED_WINDOW_DAYS = 14
""":func:`list_pending_requests` returns pending + recently-decided
within this many days. Older decided rows are out of the M3 feed."""

REASON_CLASS_VALUES = frozenset(
    ScheduleChangeRequest.ReasonClass.values  # type: ignore[attr-defined]
)
"""Accepted ``reason_class`` values for
:func:`request_availability_change`. Set at import time so the request
validation doesn't keep re-asking Django for the choices set."""


# Map ScheduleException.Type → M3 «reason» label for the response shape.
# Master-mobile §M3 line 462 + the block payload contract in the spec
# quote shape requires {lunch|vacation|sick|personal|other}.
_EXCEPTION_TYPE_TO_REASON: dict[str, str] = {
    ScheduleException.Type.VACATION: "vacation",
    ScheduleException.Type.SICK_LEAVE: "sick",
    ScheduleException.Type.DAY_OFF: "personal",
    ScheduleException.Type.CUSTOM_HOURS: "other",
    ScheduleException.Type.EVENT: "other",
}


# --- result dataclasses ------------------------------------------------


@dataclass(frozen=True)
class FreeWindow:
    """A bookable gap in the master's day, tenant-local HH:MM."""

    start: str  # HH:MM
    end: str  # HH:MM
    duration_min: int


@dataclass(frozen=True)
class Conflict:
    type: str  # double_booking | outside_hours | overlapping_exception
    booking_id: str
    description: str


@dataclass(frozen=True)
class ScheduleBooking:
    booking_id: str
    visit_at: str  # iso utc
    duration_min: int
    service_name: str
    client_first_name: str
    client_last_initial: str
    is_in_progress: bool
    is_returning_customer: bool


@dataclass(frozen=True)
class ScheduleBlock:
    exception_id: str
    start: str  # iso utc
    end: str  # iso utc
    reason: str  # lunch|vacation|sick|personal|other
    approved: bool


@dataclass
class ScheduleDay:
    date: str  # YYYY-MM-DD
    is_off_day: bool
    working_hours: dict[str, str] | None  # {"start":"10:00","end":"19:00"} or None
    bookings: list[ScheduleBooking] = field(default_factory=list)
    blocks: list[ScheduleBlock] = field(default_factory=list)
    free_windows: list[FreeWindow] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)


@dataclass
class ScheduleResponse:
    tenant_tz: str
    from_date: str  # YYYY-MM-DD
    to_date: str  # YYYY-MM-DD
    days: list[ScheduleDay] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_tz": self.tenant_tz,
            "from": self.from_date,
            "to": self.to_date,
            "days": [
                {
                    "date": d.date,
                    "is_off_day": d.is_off_day,
                    "working_hours": d.working_hours,
                    "bookings": [
                        {
                            "booking_id": b.booking_id,
                            "visit_at": b.visit_at,
                            "duration_min": b.duration_min,
                            "service_name": b.service_name,
                            "client_first_name": b.client_first_name,
                            "client_last_initial": b.client_last_initial,
                            "is_in_progress": b.is_in_progress,
                            "is_returning_customer": b.is_returning_customer,
                        }
                        for b in d.bookings
                    ],
                    "blocks": [
                        {
                            "exception_id": bl.exception_id,
                            "start": bl.start,
                            "end": bl.end,
                            "reason": bl.reason,
                            "approved": bl.approved,
                        }
                        for bl in d.blocks
                    ],
                    "free_windows": [
                        {
                            "start": fw.start,
                            "end": fw.end,
                            "duration_min": fw.duration_min,
                        }
                        for fw in d.free_windows
                    ],
                    "conflicts": [
                        {
                            "type": c.type,
                            "booking_id": c.booking_id,
                            "description": c.description,
                        }
                        for c in d.conflicts
                    ],
                }
                for d in self.days
            ],
        }


# --- errors ------------------------------------------------------------


class AvailabilityRequestError(Exception):
    """Validation failure inside :func:`request_availability_change`.

    Carries a stable ``slug`` for the HTTP envelope so view code can
    map it to a 400 without duplicate strings.
    """

    slug: str = "bad_request"

    def __init__(self, slug: str, detail: str) -> None:
        super().__init__(detail)
        self.slug = slug
        self.detail = detail


# --- TZ + duration helpers --------------------------------------------


def get_tenant_tz(tenant: Any) -> ZoneInfo:
    """Resolve the tenant's IANA TZ — fall back to UTC on bad values.

    Same fallback contract as
    :func:`apps.master_api.services.dashboard.get_tenant_tz`.
    """

    tz_name = getattr(tenant, "timezone", "") or "UTC"
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        logger.warning("master_api.schedule.bad_tenant_tz tz=%s", tz_name)
        return ZoneInfo("UTC")


def _resolve_duration(booking: VisitRow, service_cache: dict[Any, int]) -> int:
    """Pick best-available duration; memoised lookup of CatalogService.duration_min.

    ``service_cache`` avoids the N+1 problem when the same service is
    booked multiple times across the date range. Caller passes a dict
    that survives the whole :func:`build_schedule` call.

    DRF-1085: the visit's own window (``end_at - start_at``) answers this
    almost always, since the mirror carries both ends. The catalog fallback
    is keyed on ``ayla_service_id`` — on the Ayla path ``service_id`` is
    Ayla's identifier, stored verbatim, not a local primary key.
    """

    if booking.duration_min:
        return int(booking.duration_min)
    sid = booking.service_id
    if sid is not None:
        if sid in service_cache:
            return service_cache[sid]
        svc = CatalogService.all_tenants.filter(ayla_service_id=sid).first()
        cached = (
            int(svc.duration_min) if (svc and svc.duration_min) else DEFAULT_SERVICE_DURATION_MIN
        )
        service_cache[sid] = cached
        return cached
    return DEFAULT_SERVICE_DURATION_MIN


def _split_name(client_name: str) -> tuple[str, str]:
    """Split «Мария Иванова» → («Мария», «И.»). See dashboard helper."""

    parts = (client_name or "").strip().split()
    if not parts:
        return "", ""
    first = parts[0]
    if len(parts) == 1:
        return first, ""
    last_initial = parts[1][:1].upper() + "."
    return first, last_initial


def _time_diff_min(start: time, end: time) -> int:
    return (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)


def _day_bounds_utc(day: date_cls, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Return ``(start_utc, end_utc)`` for ``day`` in ``tz``."""

    start_local = datetime.combine(day, time.min, tzinfo=tz)
    end_local = datetime.combine(day, time.max, tzinfo=tz)
    return (
        start_local.astimezone(dt_timezone.utc),
        end_local.astimezone(dt_timezone.utc),
    )


# --- working block lookup ----------------------------------------------


def _working_block_for_day(
    master: CatalogMaster,
    day: date_cls,
    exceptions_by_date: dict[date_cls, ScheduleException],
    wh_by_weekday: dict[int, WorkingHours],
) -> tuple[time, time] | None:
    """Return the master's effective working window for ``day``, in tenant-local time.

    Priority: ScheduleException (custom_hours → use; full-day-off →
    None) → WorkingHours for the weekday (skip is_working=False).
    Returns None when the master isn't working that day.

    Caller passes pre-fetched dicts so we don't re-hit the DB per day.
    """

    exc = exceptions_by_date.get(day)
    if exc is not None:
        if exc.type == ScheduleException.Type.CUSTOM_HOURS:
            if exc.start_time and exc.end_time:
                return (exc.start_time, exc.end_time)
            return None
        # Any other exception type is full-day off.
        return None

    wh = wh_by_weekday.get(day.weekday())
    if wh is None or not wh.is_working or not wh.start_time or not wh.end_time:
        return None
    return (wh.start_time, wh.end_time)


# --- free-window + conflict computation -------------------------------


def _compute_free_windows(
    working_hours: tuple[time, time] | None,
    booking_intervals_local: list[tuple[time, time]],
    block_intervals_local: list[tuple[time, time]],
) -> list[FreeWindow]:
    """Return free windows of ≥ :data:`FREE_WINDOW_MIN_GAP_MIN` minutes.

    Pure function — works on tenant-local ``time`` tuples so it's
    trivially testable without TZ math. Caller projects bookings +
    blocks into local time first.

    Algorithm: merge occupied intervals (capped to working block) → walk
    cursor through working block, emitting gaps ≥ threshold. Bookings
    + blocks are combined as occupied; the resolver lives separately
    (this is a read-side computation, not a slot-picker).
    """

    if working_hours is None:
        return []
    block_start, block_end = working_hours

    # Combine + clip occupied intervals to the working block. Filter
    # out anything that doesn't intersect the block at all.
    occupied: list[tuple[time, time]] = []
    for s, e in list(booking_intervals_local) + list(block_intervals_local):
        if e <= block_start or s >= block_end:
            continue
        occupied.append((max(s, block_start), min(e, block_end)))

    # Merge overlapping intervals so the gap walk is O(n).
    occupied.sort(key=lambda iv: iv[0])
    merged: list[tuple[time, time]] = []
    for s, e in occupied:
        if not merged or s > merged[-1][1]:
            merged.append((s, e))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))

    windows: list[FreeWindow] = []
    cursor = block_start
    for s, e in merged:
        if s > cursor:
            gap = _time_diff_min(cursor, s)
            if gap >= FREE_WINDOW_MIN_GAP_MIN:
                windows.append(
                    FreeWindow(
                        start=cursor.strftime("%H:%M"),
                        end=s.strftime("%H:%M"),
                        duration_min=gap,
                    )
                )
        cursor = max(cursor, e)
        if cursor >= block_end:
            break

    if cursor < block_end:
        gap = _time_diff_min(cursor, block_end)
        if gap >= FREE_WINDOW_MIN_GAP_MIN:
            windows.append(
                FreeWindow(
                    start=cursor.strftime("%H:%M"),
                    end=block_end.strftime("%H:%M"),
                    duration_min=gap,
                )
            )
    return windows


def _detect_conflicts(
    working_hours: tuple[time, time] | None,
    confirmed_bookings: list[tuple[str, time, time]],
    block_intervals: list[tuple[str, time, time]],
) -> list[Conflict]:
    """Surface admin-error conflicts for the day.

    Inputs are tuples of (booking_id, start_local, end_local) for
    bookings and (exception_id, start_local, end_local) for blocks.

    Three kinds: double_booking, outside_hours, overlapping_exception.
    """

    conflicts: list[Conflict] = []

    # Pairwise overlap among CONFIRMED bookings.
    for i, (id_a, s_a, e_a) in enumerate(confirmed_bookings):
        for id_b, s_b, e_b in confirmed_bookings[i + 1 :]:
            if s_a < e_b and s_b < e_a:
                # Report each pair once, attributed to the second
                # booking to keep the count stable across runs.
                conflicts.append(
                    Conflict(
                        type="double_booking",
                        booking_id=id_b,
                        description=(
                            f"Overlaps booking {id_a} "
                            f"({s_a.strftime('%H:%M')}-{e_a.strftime('%H:%M')})"
                        ),
                    )
                )

    # outside_hours — booking partly or wholly outside working block.
    if working_hours is None:
        # No working hours on a day with bookings = every booking is
        # outside hours. Surface them all as outside_hours.
        for booking_id, s, e in confirmed_bookings:
            conflicts.append(
                Conflict(
                    type="outside_hours",
                    booking_id=booking_id,
                    description="No working hours set for this day",
                )
            )
    else:
        wh_start, wh_end = working_hours
        for booking_id, s, e in confirmed_bookings:
            if s < wh_start or e > wh_end:
                conflicts.append(
                    Conflict(
                        type="outside_hours",
                        booking_id=booking_id,
                        description=(
                            f"Booking {s.strftime('%H:%M')}-{e.strftime('%H:%M')} "
                            f"outside hours "
                            f"{wh_start.strftime('%H:%M')}-{wh_end.strftime('%H:%M')}"
                        ),
                    )
                )

    # overlapping_exception — booking interval intersects a block.
    for booking_id, bs, be in confirmed_bookings:
        for _exception_id, blk_s, blk_e in block_intervals:
            if bs < blk_e and blk_s < be:
                conflicts.append(
                    Conflict(
                        type="overlapping_exception",
                        booking_id=booking_id,
                        description=(
                            f"Booking overlaps approved time-off "
                            f"{blk_s.strftime('%H:%M')}-{blk_e.strftime('%H:%M')}"
                        ),
                    )
                )
                break

    return conflicts


# --- returning-customer helper ----------------------------------------


def _build_returning_customer_index(master: CatalogMaster, bot_user_ids: list[Any]) -> set[Any]:
    """Return the subset of bot_user_ids with >1 COMPLETED visit with this master.

    One DB scan per range (not per booking). DRF-1146 (owner decision
    25.08): «returning» counts visits, not bookings — a customer who
    booked five times and never showed is not a regular. The mirror
    marks a visit completed via the ``completed`` status.

    DRF-1085 changed the shape of this rule, not just its source. The old
    version counted ``RESCHEDULED`` rows as separate visits because the
    local model superseded a booking with a new row on reschedule. The
    mirror has no such state: a reschedule **moves the existing row**, so
    there is nothing extra to count — and counting it would have been
    wrong anyway (one client moving one appointment is not two visits).
    """

    if not bot_user_ids:
        return set()
    from collections import Counter

    rows = RemoteBookingProxy.all_tenants.filter(
        tenant_id=master.tenant_id,
        specialist_id=master.id,
        bot_user_id__in=list(bot_user_ids),
        status="completed",
    ).values_list("bot_user_id", flat=True)
    counts = Counter(rows)
    return {bid for bid, n in counts.items() if n > 1}


# --- public: build_schedule -------------------------------------------


def build_schedule(
    master: CatalogMaster,
    *,
    from_date: date_cls,
    to_date: date_cls,
    now: datetime | None = None,
) -> ScheduleResponse:
    """Compose the master-mobile §M3 schedule payload.

    Args:
      master: The CatalogMaster whose schedule to fetch. Cross-master
        scoping happens at the auth layer; this helper trusts the input.
      from_date / to_date: Inclusive date range in tenant-local TZ.
        Caller enforces ``to_date - from_date <= MAX_RANGE_DAYS`` and
        ``from_date <= to_date``.
      now: Optional pinned timestamp for ``is_in_progress`` computation.
        Defaults to ``timezone.now()``.

    Returns:
      A :class:`ScheduleResponse` with one :class:`ScheduleDay` per
      calendar day in the range.
    """

    if now is None:
        now = dj_timezone.now()
    tz = get_tenant_tz(master.tenant)
    tz_name = str(tz)

    # Pre-fetch all per-day inputs in a single DB roundtrip each.
    range_start_utc, _ = _day_bounds_utc(from_date, tz)
    _, range_end_utc = _day_bounds_utc(to_date, tz)

    # Booked statuses only. The mirror has no `rescheduled` state to
    # exclude — a reschedule moves the row in place — and cancelled /
    # no-show rows are filtered by asking for the booked set instead.
    bookings_qs = master_visits(
        master,
        start=range_start_utc,
        end=range_end_utc,
    )

    exceptions_qs = list(
        ScheduleException.all_tenants.filter(
            tenant_id=master.tenant_id,
            master_id=master.id,
            date__gte=from_date,
            date__lte=to_date,
        )
    )
    exceptions_by_date: dict[date_cls, ScheduleException] = {e.date: e for e in exceptions_qs}

    wh_qs = list(
        WorkingHours.all_tenants.filter(
            tenant_id=master.tenant_id,
            master_id=master.id,
        )
    )
    wh_by_weekday: dict[int, WorkingHours] = {wh.day_of_week: wh for wh in wh_qs}

    # Pre-compute returning-customer set across the range.
    bot_user_ids = [b.bot_user_id for b in bookings_qs if b.bot_user_id is not None]
    returning_set = _build_returning_customer_index(master, bot_user_ids)

    # Cache CatalogService.duration_min lookups across the range.
    service_cache: dict[Any, int] = {}

    # Bucket bookings per local calendar date (using tenant-local
    # date of the visit_at — a booking at 23:30 MSK lives on its
    # local date).
    bookings_by_date: dict[date_cls, list[VisitRow]] = {}
    for b in bookings_qs:
        if b.visit_at is None:
            continue
        local_date = b.visit_at.astimezone(tz).date()
        bookings_by_date.setdefault(local_date, []).append(b)

    days: list[ScheduleDay] = []
    cursor = from_date
    while cursor <= to_date:
        day = _build_one_day(
            master=master,
            day=cursor,
            tz=tz,
            now=now,
            wh_by_weekday=wh_by_weekday,
            exceptions_by_date=exceptions_by_date,
            bookings_today=bookings_by_date.get(cursor, []),
            returning_set=returning_set,
            service_cache=service_cache,
        )
        days.append(day)
        cursor += timedelta(days=1)

    return ScheduleResponse(
        tenant_tz=tz_name,
        from_date=from_date.isoformat(),
        to_date=to_date.isoformat(),
        days=days,
    )


def _build_one_day(
    *,
    master: CatalogMaster,
    day: date_cls,
    tz: ZoneInfo,
    now: datetime,
    wh_by_weekday: dict[int, WorkingHours],
    exceptions_by_date: dict[date_cls, ScheduleException],
    bookings_today: list[VisitRow],
    returning_set: set[Any],
    service_cache: dict[Any, int],
) -> ScheduleDay:
    """Build a single :class:`ScheduleDay`. See :func:`build_schedule`."""

    working_block = _working_block_for_day(master, day, exceptions_by_date, wh_by_weekday)
    # An «off day» is one with no working hours configured AT ALL —
    # i.e. WorkingHours row is missing OR is_working=False. A full-day
    # ScheduleException (vacation/sick) on a normally-working day is
    # NOT an off_day; the block is surfaced separately.
    wh_row = wh_by_weekday.get(day.weekday())
    is_off_day = wh_row is None or not wh_row.is_working

    # Project bookings → local time + canonical ScheduleBooking shape.
    booking_objs: list[ScheduleBooking] = []
    confirmed_intervals_local: list[tuple[str, time, time]] = []
    booking_intervals_local: list[tuple[time, time]] = []
    for b in bookings_today:
        if b.visit_at is None:
            continue
        duration = _resolve_duration(b, service_cache)
        end_at_utc = b.visit_at + timedelta(minutes=duration)
        local_start = b.visit_at.astimezone(tz)
        local_end = end_at_utc.astimezone(tz)
        # Clamp end to end-of-day so a midnight-spanning booking still
        # has a sensible local end for gap computation.
        if local_end.date() != day:
            local_end_time = time(23, 59)
        else:
            local_end_time = local_end.time().replace(microsecond=0)
        local_start_time = local_start.time().replace(microsecond=0)
        first, last_initial = _split_name(b.client_name)
        is_in_progress = b.visit_at <= now < end_at_utc
        booking_objs.append(
            ScheduleBooking(
                booking_id=b.id,
                visit_at=b.visit_at.isoformat(),
                duration_min=duration,
                service_name=b.service_name,
                client_first_name=first,
                client_last_initial=last_initial,
                is_in_progress=is_in_progress,
                is_returning_customer=b.bot_user_id in returning_set,
            )
        )
        if b.status in UPCOMING_STATUSES:
            confirmed_intervals_local.append((b.id, local_start_time, local_end_time))
            booking_intervals_local.append((local_start_time, local_end_time))

    # Blocks: approved time-off / custom-hours exceptions for this date.
    blocks: list[ScheduleBlock] = []
    block_intervals_local: list[tuple[time, time]] = []
    block_intervals_for_conflicts: list[tuple[str, time, time]] = []
    exc = exceptions_by_date.get(day)
    if exc is not None and exc.type in ScheduleException.FULL_DAY_TYPES:
        start_utc, end_utc = _day_bounds_utc(day, tz)
        blocks.append(
            ScheduleBlock(
                exception_id=str(exc.id),
                start=start_utc.isoformat(),
                end=end_utc.isoformat(),
                reason=_EXCEPTION_TYPE_TO_REASON.get(exc.type, "other"),
                approved=True,
            )
        )
        # Full-day blocks occupy from working_block start to end (so a
        # confirmed booking on a full-day-off shows as
        # overlapping_exception).
        if working_block is not None:
            block_intervals_local.append(working_block)
            block_intervals_for_conflicts.append((str(exc.id), working_block[0], working_block[1]))
        else:
            block_intervals_local.append((time(0, 0), time(23, 59)))
            block_intervals_for_conflicts.append((str(exc.id), time(0, 0), time(23, 59)))

    free_windows = _compute_free_windows(
        working_block, booking_intervals_local, block_intervals_local
    )
    conflicts = _detect_conflicts(
        working_block, confirmed_intervals_local, block_intervals_for_conflicts
    )

    working_hours_payload: dict[str, str] | None = None
    if working_block is not None:
        working_hours_payload = {
            "start": working_block[0].strftime("%H:%M"),
            "end": working_block[1].strftime("%H:%M"),
        }

    return ScheduleDay(
        date=day.isoformat(),
        is_off_day=is_off_day,
        working_hours=working_hours_payload,
        bookings=booking_objs,
        blocks=blocks,
        free_windows=free_windows,
        conflicts=conflicts,
    )


# --- public: request_availability_change ------------------------------


def request_availability_change(
    master: CatalogMaster,
    *,
    start: datetime,
    end: datetime,
    reason_class: str,
    reason_text: str,
    actor: BotUser,
    now: datetime | None = None,
) -> ScheduleChangeRequest:
    """Create a :class:`ScheduleChangeRequest` for the master's off-time.

    Validates:
      * ``start < end``
      * ``end > now`` (allow today's remaining hours; spec line «not in
        the past, allow today's remaining hours»)
      * No overlap with the master's existing APPROVED
        :class:`ScheduleException` (a vacation already granted)
      * ``reason_class`` ∈ :data:`REASON_CLASS_VALUES`
      * ``reason_text`` ≤ 200 chars (CharField cap)

    Raises :class:`AvailabilityRequestError` with a stable slug on each
    failure mode so view code maps directly to HTTP 400.

    Does NOT emit audit / event / MAX DM — those are the caller's
    responsibility so they run inside the same DB transaction +
    post-commit hook the view owns.
    """

    if now is None:
        now = dj_timezone.now()

    if start >= end:
        raise AvailabilityRequestError("bad_request", "start must be before end")
    if end <= now:
        raise AvailabilityRequestError("bad_request", "window is in the past")
    if reason_class not in REASON_CLASS_VALUES:
        raise AvailabilityRequestError(
            "bad_request",
            f"reason_class must be one of {sorted(REASON_CLASS_VALUES)}",
        )
    if len(reason_text or "") > 200:
        raise AvailabilityRequestError("bad_request", "reason_text must be ≤ 200 chars")

    tz = get_tenant_tz(master.tenant)
    # Compute the date range the window touches in tenant-local TZ.
    start_local_date = start.astimezone(tz).date()
    end_local_date = end.astimezone(tz).date()

    # Overlap check against approved ScheduleException rows.
    # CUSTOM_HOURS is a partial-day exception so we project to UTC
    # window precisely; full-day types cover [00:00, 23:59:59] local.
    exceptions = list(
        ScheduleException.all_tenants.filter(
            tenant_id=master.tenant_id,
            master_id=master.id,
            date__gte=start_local_date,
            date__lte=end_local_date,
        )
    )
    for exc in exceptions:
        if exc.type == ScheduleException.Type.CUSTOM_HOURS:
            if not exc.start_time or not exc.end_time:
                continue  # malformed row; skip rather than crash
            exc_start = datetime.combine(exc.date, exc.start_time, tzinfo=tz).astimezone(
                dt_timezone.utc
            )
            exc_end = datetime.combine(exc.date, exc.end_time, tzinfo=tz).astimezone(
                dt_timezone.utc
            )
        else:
            # Full-day off types — block the whole local date.
            day_start, day_end = _day_bounds_utc(exc.date, tz)
            exc_start, exc_end = day_start, day_end
        if start < exc_end and exc_start < end:
            raise AvailabilityRequestError(
                "overlap",
                f"window overlaps an existing approved exception on {exc.date.isoformat()}",
            )

    req = ScheduleChangeRequest.all_tenants.create(
        tenant=master.tenant,
        master=master,
        requested_start=start,
        requested_end=end,
        reason_class=reason_class,
        reason_text=reason_text or "",
        requested_by=actor,
        status=ScheduleChangeRequest.Status.PENDING,
    )
    return req


# --- public: list_pending_requests ------------------------------------


def list_pending_requests(
    master: CatalogMaster, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Return the master's own pending + recently-decided requests.

    Filter:
      * ``master`` == passed-in master
      * status == pending, OR (status != pending AND
        resolved_at >= now - 14d)

    Order: created_at desc.

    Returns JSON-serialisable dicts so the view layer is a one-liner.
    Master sees ONLY their own rows — strict cross-master isolation
    relies on the auth-layer's tenant_scope + the explicit master_id
    filter here.
    """

    if now is None:
        now = dj_timezone.now()
    cutoff = now - timedelta(days=RECENT_DECIDED_WINDOW_DAYS)

    qs = ScheduleChangeRequest.all_tenants.filter(
        tenant_id=master.tenant_id,
        master_id=master.id,
    )

    from django.db.models import Q

    qs = (
        qs.filter(Q(status=ScheduleChangeRequest.Status.PENDING) | Q(resolved_at__gte=cutoff))
        .select_related("resolved_by")
        .order_by("-created_at")
    )

    items: list[dict[str, Any]] = []
    for row in qs:
        decided_by_name = ""
        if row.resolved_by is not None:
            # Owner / admin Django User. ``get_full_name`` falls back to
            # username — safe defensive default for SQLite test users.
            decided_by_name = (
                row.resolved_by.get_full_name() or row.resolved_by.username or ""
            ).strip()
        items.append(
            {
                "request_id": str(row.id),
                "requested_start": row.requested_start.isoformat() if row.requested_start else None,
                "requested_end": row.requested_end.isoformat() if row.requested_end else None,
                "reason_class": row.reason_class or "",
                "reason_text": row.reason_text or "",
                "status": row.status,
                "decided_at": row.resolved_at.isoformat() if row.resolved_at else None,
                "decided_by_name": decided_by_name if row.resolved_at else None,
                "rejection_reason": (
                    row.resolution_note
                    if row.status == ScheduleChangeRequest.Status.REJECTED
                    else None
                ),
                "created_at": row.created_at.isoformat(),
            }
        )
    return items


__all__ = [
    "AvailabilityRequestError",
    "Conflict",
    "DEFAULT_RANGE_DAYS",
    "FREE_WINDOW_MIN_GAP_MIN",
    "FreeWindow",
    "MAX_RANGE_DAYS",
    "RECENT_DECIDED_WINDOW_DAYS",
    "ScheduleBlock",
    "ScheduleBooking",
    "ScheduleDay",
    "ScheduleResponse",
    "build_schedule",
    "get_tenant_tz",
    "list_pending_requests",
    "request_availability_change",
]
