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

from apps.catalog.specialist_ref import CatalogSpecialistUnresolved, catalog_specialist_id
from apps.integrations.ayla.salon_client import (
    SalonNotConfigured,
    SalonUnavailable,
    get_salon_client,
)
from apps.integrations.ayla.user_proxy import external_user_id_for
from apps.scheduling.models import ScheduleException

logger = logging.getLogger(__name__)


class WorkingHoursLike(Protocol):
    """Минимум, который есть у ОБОИХ источников рабочей строки.

    Перерыва здесь НЕТ намеренно (DRF-1638). Он есть только у ayla-строки:
    локальная ``apps.scheduling.WorkingHours`` колонок перерыва не имеет
    вовсе — в боте перерыв живёт только на проводе Ayla.

    Объявить его здесь значило бы потребовать от локальной модели поля,
    которого у неё нет, то есть сломать ветку с выключенным флагом ради
    ветки с включённым. Поэтому протокол описывает пересечение, а перерыв
    читается у потребителя защищённо (``getattr(row, "break_start", None)``
    в ``schedule._lunch_of``) — «поля нет» и «перерыв не назначен» там
    сходятся в один ответ, и это единственное место, где они смеют
    сойтись.
    """

    is_working: bool
    start_time: time | None
    end_time: time | None


class ExceptionLike(Protocol):
    """Минимум, который есть у ОБОИХ источников строки-исключения.

    Перерыва здесь нет по той же причине, что у рабочей строки: локальная
    ``ScheduleException`` колонок перерыва не имеет. См. выше.
    """

    id: Any
    type: str
    start_time: time | None
    end_time: time | None


@dataclass
class FrameHours:
    """One weekday of the weekly template, Ayla-wire flavour.

    DRF-1638: перерыв несут, а не роняют. До 11.09.2026 этот класс нёс три
    поля из семи, и ``break_start`` / ``break_end`` терялись здесь молча —
    провод их отдавал, расчёт свободных окон их не видел, и обед показывался
    свободным временем. Экспозиция была нулевой (на пилоте перерыв не
    заполнен ни у одного из девяти мастеров), то есть дефект не спал, а ждал
    первой строки.
    """

    is_working: bool
    start_time: time | None
    end_time: time | None
    break_start: time | None = None
    break_end: time | None = None


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
    break_start: time | None = None
    break_end: time | None = None


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
    try:
        catalog_specialist_id(master)
    except CatalogSpecialistUnresolved as exc:
        # DRF-1933: у строки нет id профиля в каталоге — читать не по чему.
        # Класс, который вызывающие уже разбирают: пустота не становится
        # «весь день свободен».
        raise SalonNotConfigured(str(exc)) from exc

    wh_by_weekday = _weekly_template(
        client.get_master_schedule(
            actor_external_id=actor,
            tenant_slug=slug,
            specialist_id=catalog_specialist_id(master),
        )
    )
    exceptions_by_date = _exceptions(
        client.list_schedule_exceptions(
            actor_external_id=actor,
            tenant_slug=slug,
            specialist_id=catalog_specialist_id(master),
            date_from=from_date.isoformat(),
            date_to=to_date.isoformat(),
        )
    )
    extra_blocks = _time_off(
        client.list_time_off(
            actor_external_id=actor,
            tenant_slug=slug,
            specialist_id=catalog_specialist_id(master),
            date_from=from_date.isoformat(),
            date_to=to_date.isoformat(),
        ),
        exceptions_by_date=exceptions_by_date,
        tz=tz,
    )
    _name_reasons_from_own_requests(
        master, extra_blocks, exceptions_by_date, from_date=from_date, to_date=to_date, tz=tz
    )
    return wh_by_weekday, exceptions_by_date, extra_blocks


#: ``reason_class`` заявки → тип исключения дня (для блоков на весь день):
#: ``_EXCEPTION_TYPE_TO_REASON`` в ``schedule.py`` переводит его обратно в
#: тот же ``reason_class``.
_REASON_CLASS_TO_EXCEPTION_TYPE: dict[str, str] = {
    "vacation": ScheduleException.Type.VACATION,
    "sick": ScheduleException.Type.SICK_LEAVE,
    "personal": ScheduleException.Type.DAY_OFF,
}


def _name_reasons_from_own_requests(
    master: Any,
    extra_blocks: dict[date_cls, list[FrameBlock]],
    exceptions_by_date: dict[date_cls, ExceptionLike],
    *,
    from_date: date_cls,
    to_date: date_cls,
    tz: ZoneInfo,
) -> None:
    """Причина блока — из собственной одобренной заявки, а не «other» (11.11.2026).

    Провод каталога (``time-off``) вид отсутствия не несёт, и угаданная
    подпись лгала бы — поэтому выше стоит ``other``. Но отсутствие, которое
    завёл сам этот бот (``ScheduleChangeRequest`` → ``create_specialist_time_off``),
    он знает по своей записи: ``reason_class`` одобренной заявки с тем же
    интервалом. Совпадение — по накрытию: интервал заявки накрывает блок (блок
    режется по дням, заявка — нет). Ничего не совпало — остаётся ``other``.
    Запрос один на диапазон; у исключений на весь день меняется ``type``,
    у частичных блоков — ``reason``.
    """
    from apps.scheduling.models import ScheduleChangeRequest

    range_start = datetime.combine(from_date, time(0, 0), tzinfo=tz)
    range_end = datetime.combine(to_date, time(0, 0), tzinfo=tz) + timedelta(days=1)
    requests: list[tuple[datetime, datetime, str]] = [
        (req_start, req_end, str(reason_class))
        for req_start, req_end, reason_class in ScheduleChangeRequest.all_tenants.filter(
            tenant_id=master.tenant_id,
            master_id=master.id,
            status=ScheduleChangeRequest.Status.APPROVED,
            requested_start__lt=range_end,
            requested_end__gt=range_start,
        )
        .exclude(reason_class__in=("", "other"))
        .values_list("requested_start", "requested_end", "reason_class")
        if req_start is not None and req_end is not None
    ]
    if not requests:
        return

    def covering(start: datetime, end: datetime) -> str | None:
        for req_start, req_end, reason_class in requests:
            if req_start <= start and req_end >= end:
                return reason_class
        return None

    for day, blocks in extra_blocks.items():
        for i, block in enumerate(blocks):
            if block.reason != "other":
                continue
            start = datetime.combine(day, block.start_local, tzinfo=tz)
            end = datetime.combine(day, block.end_local, tzinfo=tz)
            reason = covering(start, end)
            if reason is not None:
                blocks[i] = FrameBlock(
                    id=block.id,
                    start_local=block.start_local,
                    end_local=block.end_local,
                    reason=reason,
                )
    for day, exc in list(exceptions_by_date.items()):
        if exc.type != ScheduleException.Type.EVENT or exc.start_time is not None:
            continue
        day_open = datetime.combine(day, time(0, 0), tzinfo=tz)
        reason = covering(day_open, day_open + timedelta(days=1) - timedelta(seconds=1))
        new_type = _REASON_CLASS_TO_EXCEPTION_TYPE.get(reason or "")
        if new_type is not None:
            exceptions_by_date[day] = FrameException(
                id=exc.id,
                type=new_type,
                start_time=None,
                end_time=None,
                break_start=getattr(exc, "break_start", None),
                break_end=getattr(exc, "break_end", None),
            )


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
            break_start=_hhmm(row.get("break_start")),
            break_end=_hhmm(row.get("break_end")),
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
                break_start=_hhmm(row.get("break_start")),
                break_end=_hhmm(row.get("break_end")),
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
