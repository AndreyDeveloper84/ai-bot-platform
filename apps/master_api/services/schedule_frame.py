"""Where the master screen's day frame comes from (DRF-1126).

The frame — the weekly working-hours template plus per-date overrides —
is what tells the master when their day begins, ends, and is blocked.
PR #1186 moved the CLIENT's slot supply to Ayla as the source of truth;
the master screen kept reading the local ``apps.scheduling`` tables,
which the admin surface no longer updates. The salon edits the schedule
in Ayla, the client sees the new frame, and the master saw the old one —
no error anywhere, both sides looking authoritative.

Two sources, one flag:

* ``BOOKING_VIA_AYLA_REST`` OFF — the local ``WorkingHours`` /
  ``ScheduleException`` tables, exactly as before. Legacy mode keeps its
  source; nothing here narrows it.
* ON — Ayla, via the salon client's read-only routes
  (``get_master_schedule`` / ``list_schedule_exceptions`` /
  ``list_time_off``; all three are registered in ``SALON_ROUTES`` and
  service-credential reads are allowed there). The local tables are NOT
  consulted: a stale answer is the defect being fixed, so a silent
  fallback would resurrect it.

Failure semantics follow the detector family (DRF-1111): when the canon
cannot be read, the frame refuses rather than guesses. ``SalonAPIError``
propagates to the caller — the master gets an error, not a stale schedule.

What the wire cannot say: Ayla's per-date exception carries no *kind*
(vacation vs sick vs personal), only ``is_working_day`` + times + a free
``note``. Rather than guess a label, wire exceptions report the M3
reason ``other`` — a degraded but honest label, instead of «personal»
on what might be someone's vacation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from django.conf import settings

from apps.integrations.ayla.salon_client import (
    SalonNotConfigured,
    SalonUnavailable,
    get_salon_client,
)
from apps.integrations.ayla.user_proxy import external_user_id_for
from apps.scheduling.models import ScheduleException

logger = logging.getLogger(__name__)


class WorkingHoursLike(Protocol):
    """The three attributes the schedule screen reads off a working row."""

    is_working: bool
    start_time: time | None
    end_time: time | None


class ExceptionLike(Protocol):
    """The four attributes the schedule screen reads off an exception row."""

    id: Any
    type: str
    start_time: time | None
    end_time: time | None


@dataclass
class FrameHours:
    """One weekday of the weekly template, Ayla-wire flavour."""

    is_working: bool
    start_time: time | None
    end_time: time | None


@dataclass
class FrameException:
    """One per-date override, Ayla-wire flavour.

    ``type`` carries real ``ScheduleException.Type`` members so the
    screen's existing branches (``CUSTOM_HOURS`` → custom window,
    ``FULL_DAY_TYPES`` → day off) work unchanged. Full-day wire rows get
    ``EVENT`` (→ reason ``other``), never ``DAY_OFF`` (→ «personal»):
    the wire does not name the kind, and a guessed label would lie.
    """

    id: Any
    type: str
    start_time: time | None
    end_time: time | None


@dataclass
class FrameBlock:
    """A partial-day absence — what the local model cannot express.

    Ayla's time-off is datetime-ranged («сегодня ушла раньше»), while
    ``ScheduleException`` is per-date. Partial rows become day-level
    blocks, which the screen's ``blocks`` list already supports.
    """

    id: Any
    start_local: time
    end_local: time
    reason: str


#: (weekly template by weekday, per-date overrides, partial-day blocks by date)
DayFrame = tuple[
    dict[int, WorkingHoursLike],
    dict[date_cls, ExceptionLike],
    dict[date_cls, list[FrameBlock]],
]


def load_day_frame(
    master: Any,
    *,
    from_date: date_cls,
    to_date: date_cls,
    tz: ZoneInfo,
) -> DayFrame:
    """The master's frame for ``from_date..to_date``, from the live source."""

    if not getattr(settings, "BOOKING_VIA_AYLA_REST", False):
        return _load_local(master, from_date=from_date, to_date=to_date)
    return _load_ayla(master, from_date=from_date, to_date=to_date, tz=tz)


# ─── local (flag OFF — legacy source, unchanged behaviour) ──────────────────


def _load_local(master: Any, *, from_date: date_cls, to_date: date_cls) -> DayFrame:
    from apps.scheduling.models import WorkingHours

    exceptions_qs = ScheduleException.all_tenants.filter(
        tenant_id=master.tenant_id,
        master_id=master.id,
        date__gte=from_date,
        date__lte=to_date,
    )
    wh_qs = WorkingHours.all_tenants.filter(
        tenant_id=master.tenant_id,
        master_id=master.id,
    )
    return (
        {wh.day_of_week: wh for wh in wh_qs},
        {e.date: e for e in exceptions_qs},
        {},
    )


# ─── Ayla (flag ON — the source of truth) ────────────────────────────────────


def _load_ayla(master: Any, *, from_date: date_cls, to_date: date_cls, tz: ZoneInfo) -> DayFrame:
    tenant = master.tenant
    actor_user = _ayla_read_actor(tenant)
    if actor_user is None:
        # The day/schedule views check the RIGHTS of the person named in
        # X-External-User-ID, so the read needs a real staff row. A tenant
        # without an active owner/admin cannot be read at all — that is a
        # configuration fact, not an outage, and it must not render as an
        # empty (i.e. fully free) schedule.
        raise SalonNotConfigured(
            f"no active owner/admin staff to read the schedule frame for {tenant.slug}"
        )

    actor = external_user_id_for(actor_user)
    client = get_salon_client()
    slug = tenant.slug
    specialist_id = str(master.id)

    wh_by_weekday = _weekly_template(
        client.get_master_schedule(
            actor_external_id=actor,
            tenant_slug=slug,
            specialist_id=specialist_id,
        )
    )
    exceptions_by_date = _exceptions(
        client.list_schedule_exceptions(
            actor_external_id=actor,
            tenant_slug=slug,
            specialist_id=specialist_id,
            date_from=from_date.isoformat(),
            date_to=to_date.isoformat(),
        )
    )
    extra_blocks = _time_off(
        client.list_time_off(
            actor_external_id=actor,
            tenant_slug=slug,
            specialist_id=specialist_id,
            date_from=from_date.isoformat(),
            date_to=to_date.isoformat(),
        ),
        exceptions_by_date=exceptions_by_date,
        tz=tz,
    )
    return wh_by_weekday, exceptions_by_date, extra_blocks


def _ayla_read_actor(tenant: Any) -> Any | None:
    """The human the read names to Ayla: active Owner, else active Admin.

    Same rule as the mirror reconciliation sweep (DRF-1111). Duplicated
    here, small and deliberate, because that one lives on an unmerged
    branch — when #1288 lands, the two converge on one helper.
    """

    from apps.tenancy.models import TenantStaff

    for role in (TenantStaff.Role.OWNER, TenantStaff.Role.ADMIN):
        staff = (
            TenantStaff.all_tenants.filter(tenant=tenant, role=role, deactivated_at__isnull=True)
            .select_related("bot_user")
            .order_by("id")
            .first()
        )
        if staff is not None:
            return staff.bot_user
    return None


# ─── wire → frame mapping ────────────────────────────────────────────────────


def _hhmm(value: Any) -> time | None:
    """``"HH:MM"`` from the wire, or None. Anything else is a loud failure —
    a frame that cannot be parsed must not silently become a free day."""

    if value is None:
        return None
    if isinstance(value, str):
        try:
            return time.fromisoformat(value)
        except ValueError:
            pass
    raise SalonUnavailable(f"unrecognised schedule time: {value!r}")


def _weekly_template(rows: list[dict[str, Any]]) -> dict[int, WorkingHoursLike]:
    out: dict[int, WorkingHoursLike] = {}
    for row in rows:
        dow = row.get("day_of_week")
        if not isinstance(dow, int) or not 0 <= dow <= 6:
            raise SalonUnavailable(f"unrecognised weekly-template row: {row!r}")
        out[dow] = FrameHours(
            is_working=bool(row.get("is_working_day")),
            start_time=_hhmm(row.get("start_time")),
            end_time=_hhmm(row.get("end_time")),
        )
    return out


def _exceptions(rows: list[dict[str, Any]]) -> dict[date_cls, ExceptionLike]:
    out: dict[date_cls, ExceptionLike] = {}
    for row in rows:
        try:
            day = date_cls.fromisoformat(str(row.get("date") or ""))
        except ValueError:
            raise SalonUnavailable(f"unrecognised schedule-exception row: {row!r}") from None
        if row.get("is_working_day"):
            out[day] = FrameException(
                id=str(row.get("id") or f"exc-{day}"),
                type=ScheduleException.Type.CUSTOM_HOURS,
                start_time=_hhmm(row.get("start_time")),
                end_time=_hhmm(row.get("end_time")),
            )
        else:
            out[day] = FrameException(
                id=str(row.get("id") or f"exc-{day}"),
                type=ScheduleException.Type.EVENT,
                start_time=None,
                end_time=None,
            )
    return out


def _time_off(
    rows: list[dict[str, Any]],
    *,
    exceptions_by_date: dict[date_cls, ExceptionLike],
    tz: ZoneInfo,
) -> dict[date_cls, list[FrameBlock]]:
    """Datetime-ranged absences → full-day overrides or partial blocks.

    A per-date schedule exception wins over a time-off row for the same
    day: it is the more specific statement. A partial absence (inside the
    day) becomes a :class:`FrameBlock`; one covering the whole local day
    becomes a full-day override.
    """

    extra: dict[date_cls, list[FrameBlock]] = {}
    for row in rows:
        start_at = _parse_dt(row.get("start_at"))
        end_at = _parse_dt(row.get("end_at"))
        if start_at is None or end_at is None:
            raise SalonUnavailable(f"unrecognised time-off row: {row!r}")
        local_start = start_at.astimezone(tz)
        local_end = end_at.astimezone(tz)
        day = local_start.date()
        last_day = local_end.date()
        while day <= last_day:
            day_open = datetime.combine(day, time(0, 0), tzinfo=tz)
            day_close = day_open + timedelta(days=1)
            # A «whole day» absence ends at 23:59:59 on the wire, not at
            # the next midnight — allow the one-second slack, or a full
            # day off would read as a one-second working day.
            if local_start <= day_open and local_end >= day_close - timedelta(seconds=1):
                if day not in exceptions_by_date:
                    exceptions_by_date[day] = FrameException(
                        id=str(row.get("id") or f"off-{day}"),
                        type=ScheduleException.Type.EVENT,
                        start_time=None,
                        end_time=None,
                    )
            else:
                block_start = max(local_start, day_open).time().replace(microsecond=0)
                block_end = min(local_end, day_close).time().replace(microsecond=0)
                extra.setdefault(day, []).append(
                    FrameBlock(
                        id=str(row.get("id") or f"off-{day}"),
                        start_local=block_start,
                        end_local=block_end,
                        reason="other",
                    )
                )
            day += timedelta(days=1)
    return extra


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed
