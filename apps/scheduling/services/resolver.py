"""Slot resolution — schedule rows + occupied intervals → bookable starts.

Rewritten after the Phase 0a draft was reviewed against
``docs/design/handoffs/2026-05-18-schedule-management-handoff.md``. The
new shape honors:

* :class:`apps.scheduling.models.WorkingHours.is_working` flag + nullable
  times
* :class:`apps.scheduling.models.WorkingHours.lunch_start` /
  ``lunch_end`` — splits the working block around the recurring lunch
* :class:`apps.scheduling.models.ScheduleException.Type` 5-value enum —
  full-day types short-circuit, ``custom_hours`` overrides weekday
  template
* :class:`apps.scheduling.models.TimeBlock` — sub-day operational
  blocks, subtracted as occupied intervals
* :class:`apps.scheduling.models.SlotConfig` — tenant-wide
  ``buffer_min`` (extends occupied intervals on both sides),
  ``lead_time_min`` (cutoff vs ``now``), ``max_advance_days`` (caps
  the date), ``slot_granularity_min`` (grid step)

### Public surface

* :func:`get_slot_config` — fetch tenant's SlotConfig with defaults
  fallback when no row exists.
* :func:`resolve_working_blocks` — given (tenant, master, date), return
  ``[(start_time, end_time), …]`` after applying exception rules + lunch
  split. Returns ``[]`` when the master isn't working that date.
* :func:`compute_free_slots` — pure function: working blocks + occupied
  intervals + duration + config → list of :class:`FreeSlot`.

### Weekday indexing

This module uses Python's native :meth:`datetime.date.weekday` (Mon=0 …
Sun=6), matching :class:`apps.scheduling.models.Weekday`. The Phase 0a
draft used ISO 1–7; that was a silent corruption bug caught in handoff
review.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls, datetime, time, timedelta
from zoneinfo import ZoneInfo

from apps.catalog.models import CatalogMaster
from apps.scheduling.models import (
    DEFAULT_BUFFER_MIN,
    DEFAULT_LEAD_TIME_MIN,
    DEFAULT_MAX_ADVANCE_DAYS,
    DEFAULT_SLOT_GRANULARITY_MIN,
    ScheduleException,
    SlotConfig,
    TimeBlock,
    Weekday,
    WorkingHours,
)
from apps.tenancy.models import Tenant


@dataclass(frozen=True)
class FreeSlot:
    """A single bookable start time, timezone-aware in tenant local TZ."""

    start: datetime


@dataclass(frozen=True)
class ResolverConfig:
    """In-memory snapshot of :class:`SlotConfig` (or its defaults).

    Used so the resolver doesn't take a Django model dependency for
    pure-function tests. Production callers should pass
    :func:`get_slot_config`'s return value.
    """

    slot_granularity_min: int = DEFAULT_SLOT_GRANULARITY_MIN
    buffer_min: int = DEFAULT_BUFFER_MIN
    lead_time_min: int = DEFAULT_LEAD_TIME_MIN
    max_advance_days: int = DEFAULT_MAX_ADVANCE_DAYS


def get_slot_config(tenant: Tenant) -> ResolverConfig:
    """Resolve effective slot config for a tenant.

    Returns the SlotConfig row's values if present; falls back to the
    module defaults otherwise. Never raises — a missing row is a
    legitimate "use defaults" state.
    """

    row = SlotConfig.all_tenants.filter(tenant=tenant).first()
    if row is None:
        return ResolverConfig()
    return ResolverConfig(
        slot_granularity_min=row.slot_granularity_min,
        buffer_min=row.buffer_min,
        lead_time_min=row.lead_time_min,
        max_advance_days=row.max_advance_days,
    )


def resolve_working_blocks(
    *,
    tenant: Tenant,
    master: CatalogMaster,
    on_date: date_cls,
) -> list[tuple[time, time]]:
    """Return the master's bookable working blocks for ``on_date``.

    Resolution order:

    1. Look up the (master, date) :class:`ScheduleException` (unique).
       If present:

       * full-day types (vacation / sick_leave / day_off / event) → ``[]``
       * ``custom_hours`` → use exception's ``(start, end)`` as the only
         block; weekday template is ignored for that date

    2. Otherwise: fetch :class:`WorkingHours` for the date's weekday.
       Skip if ``is_working=False``. Apply lunch split if both
       ``lunch_start`` and ``lunch_end`` are set.

    Returns blocks sorted by ``start_time``.
    """

    exc = ScheduleException.all_tenants.filter(tenant=tenant, master=master, date=on_date).first()
    if exc is not None:
        if exc.type in ScheduleException.FULL_DAY_TYPES:
            return []
        # CHECK constraint guarantees both times non-null for custom_hours.
        assert exc.start_time is not None and exc.end_time is not None  # noqa: S101
        return _split_by_lunch(exc.start_time, exc.end_time, None, None)

    weekday = on_date.weekday()  # 0=Mon … 6=Sun — matches Weekday choices
    wh = WorkingHours.all_tenants.filter(tenant=tenant, master=master, day_of_week=weekday).first()
    if wh is None or not wh.is_working:
        return []
    # CHECK constraint guarantees both times non-null when is_working=True.
    assert wh.start_time is not None and wh.end_time is not None  # noqa: S101
    return _split_by_lunch(wh.start_time, wh.end_time, wh.lunch_start, wh.lunch_end)


def _split_by_lunch(
    start: time,
    end: time,
    lunch_start: time | None,
    lunch_end: time | None,
) -> list[tuple[time, time]]:
    """Return ``[(start, end)]`` or pre/post-lunch ranges.

    When the lunch break sits outside the working window, falls back to
    a single ``[(start, end)]`` block (defensive against future data
    where lunch settings drift).
    """

    if lunch_start is None or lunch_end is None:
        return [(start, end)]
    if lunch_end <= start or lunch_start >= end:
        # Lunch entirely outside the working window — ignore.
        return [(start, end)]
    out: list[tuple[time, time]] = []
    if lunch_start > start:
        out.append((start, lunch_start))
    if lunch_end < end:
        out.append((lunch_end, end))
    return out


def compute_free_slots(
    *,
    working_blocks: list[tuple[time, time]],
    occupied: list[tuple[datetime, datetime]],
    service_duration_min: int,
    on_date: date_cls,
    tz: ZoneInfo,
    config: ResolverConfig | None = None,
    now: datetime | None = None,
) -> list[FreeSlot]:
    """Generate free slot starts inside the working blocks.

    Parameters
    ----------
    working_blocks
        ``(start_time, end_time)`` ranges in tenant-local time, from
        :func:`resolve_working_blocks`. Naive :class:`datetime.time`.
    occupied
        Timezone-aware ``(start_dt, end_dt)`` intervals — bookings +
        :class:`TimeBlock` rows that intersect the date. The caller is
        responsible for the union; the resolver does NOT query the DB.
    service_duration_min
        Required service length in minutes. Reject any candidate where
        ``candidate + duration > block.end``.
    on_date
        Calendar date in tenant-local TZ.
    tz
        Tenant timezone for attaching to naive times.
    config
        :class:`ResolverConfig` (defaults applied when ``None``):

        * ``buffer_min`` — symmetric buffer expanded on both sides of
          every occupied interval
        * ``lead_time_min`` — earliest bookable instant = ``now + lead``
        * ``slot_granularity_min`` — grid step
        * ``max_advance_days`` — NOT enforced by this function; caller
          must clamp the date range before calling
    now
        Reference instant for ``lead_time`` cutoff. ``None`` keeps all
        slots (used by pure-tuple tests).

    Returns sorted list of :class:`FreeSlot`.
    """

    if service_duration_min <= 0:
        return []

    cfg = config or ResolverConfig()
    if cfg.slot_granularity_min <= 0:
        return []

    step = timedelta(minutes=cfg.slot_granularity_min)
    service_len = timedelta(minutes=service_duration_min)
    buffer = timedelta(minutes=cfg.buffer_min)

    # Apply lead_time cutoff: candidate must be >= now + lead_time.
    cutoff: datetime | None = None
    if now is not None:
        cutoff = (now + timedelta(minutes=cfg.lead_time_min)).astimezone(tz)

    # Localise occupied intervals + expand symmetrically by buffer.
    expanded_occupied: list[tuple[datetime, datetime]] = []
    for occ_start, occ_end in occupied:
        local_start = occ_start.astimezone(tz) - buffer
        local_end = occ_end.astimezone(tz) + buffer
        expanded_occupied.append((local_start, local_end))

    out: list[FreeSlot] = []
    for block_start_t, block_end_t in working_blocks:
        block_start = datetime.combine(on_date, block_start_t, tzinfo=tz)
        block_end = datetime.combine(on_date, block_end_t, tzinfo=tz)
        latest_start = block_end - service_len
        if latest_start < block_start:
            continue

        cursor = block_start
        while cursor <= latest_start:
            slot_end = cursor + service_len
            if cutoff is not None and cursor < cutoff:
                cursor += step
                continue
            if _overlaps_any(cursor, slot_end, expanded_occupied):
                cursor += step
                continue
            out.append(FreeSlot(start=cursor))
            cursor += step

    return out


def _overlaps_any(
    start: datetime,
    end: datetime,
    occupied: list[tuple[datetime, datetime]],
) -> bool:
    """Closed-open overlap: ``a < o_end AND o_start < b``."""

    for o_start, o_end in occupied:
        if start < o_end and o_start < end:
            return True
    return False


def collect_time_block_intervals(
    *,
    tenant: Tenant,
    master: CatalogMaster,
    date_from: date_cls,
    date_to: date_cls,
    tz: ZoneInfo,
) -> list[tuple[datetime, datetime]]:
    """Return time-block intervals intersecting the date window.

    Helper for the slot endpoint: pulls :class:`TimeBlock` rows whose
    span intersects ``[date_from, date_to+1)`` in tenant-local TZ and
    returns timezone-aware ``(start_at, end_at)`` tuples ready to merge
    with the booking-occupancy list.
    """

    window_start_local = datetime.combine(date_from, datetime.min.time(), tzinfo=tz)
    window_end_local = datetime.combine(date_to + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    qs = TimeBlock.all_tenants.filter(
        tenant=tenant,
        master=master,
        start_at__lt=window_end_local,
        end_at__gt=window_start_local,
    ).values_list("start_at", "end_at")
    return [(s, e) for s, e in qs]


__all__ = [
    "FreeSlot",
    "ResolverConfig",
    "Weekday",
    "collect_time_block_intervals",
    "compute_free_slots",
    "get_slot_config",
    "resolve_working_blocks",
]
