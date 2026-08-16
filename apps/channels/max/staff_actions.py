"""What the salon bot's buttons actually do (DRF-1061).

Two answers, both read-only, both built from data that already exists:

* **the day** — who is coming, when, to whom. The salon had no way to see
  this at all: the admin surface returns an empty queryset for a
  non-specialist actor, so «кто сегодня придёт» was answered by asking the
  owner (audit §4.3).
* **pending requests** — masters asking to change their schedule. The
  approve/reject endpoints exist and work; what was missing was any way for
  the admin to learn a request had been filed. Nothing notified them.

Both read the mirror that actually holds pilot data — ``RemoteBookingProxy``
via ``apps.master_api.services.visit_source`` — not the local
``BookingRequest``, which on the pilot has four rows and no master on any of
them (DRF-1085).

Client phone numbers are never included, by any path (DRF-1039).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone as dt_timezone

from django.utils import timezone

logger = logging.getLogger(__name__)

MAX_LISTED = 12
"""Cap on lines in one reply. A salon day beyond this is a Mini App job —
a chat message with forty rows is not readable on a phone."""


def _tenant_tz(tenant):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo(getattr(tenant, "timezone", "") or "Europe/Moscow")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Europe/Moscow")


def _day_bounds(now: datetime, tz) -> tuple[datetime, datetime]:
    local = now.astimezone(tz)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(dt_timezone.utc), (
        start_local + timedelta(days=1) - timedelta(microseconds=1)
    ).astimezone(dt_timezone.utc)


def salon_day(tenant, *, now: datetime | None = None) -> str:
    """Today across every master of the salon.

    Grouped by master, because that is how a salon reads its day: the
    question is "who is busy when", not a flat chronological list.
    """

    from apps.catalog.models import CatalogMaster
    from apps.master_api.services.visit_source import master_visits

    now = now or timezone.now()
    tz = _tenant_tz(tenant)
    start, end = _day_bounds(now, tz)

    # `.objects` — the callers run inside tenant_scope (the consumer enters
    # it for the bot's tenant), so the scoped manager applies and a
    # cross-tenant read is impossible rather than just unintended.
    masters = list(
        CatalogMaster.objects.filter(archived_at__isnull=True, is_active=True).order_by("name")
    )
    if not masters:
        return "В салоне пока нет мастеров."

    blocks: list[str] = []
    total = 0
    for master in masters:
        visits = master_visits(master, start=start, end=end)
        if not visits:
            continue
        total += len(visits)
        lines = [f"*{master.name}*"]
        for visit in visits[:MAX_LISTED]:
            when = visit.visit_at.astimezone(tz).strftime("%H:%M") if visit.visit_at else "—"
            service = visit.service_name or "услуга не указана"
            lines.append(f"  {when} · {visit.client_name} · {service}")
        if len(visits) > MAX_LISTED:
            lines.append(f"  …и ещё {len(visits) - MAX_LISTED}")
        blocks.append("\n".join(lines))

    date_label = now.astimezone(tz).strftime("%d.%m")
    if not blocks:
        return f"На {date_label} записей нет."

    plural = "запись" if total == 1 else ("записи" if 2 <= total <= 4 else "записей")
    return f"*{date_label}* — {total} {plural}.\n\n" + "\n\n".join(blocks)


def master_day(master, *, now: datetime | None = None) -> str:
    """Today for one master."""

    from apps.master_api.services.visit_source import master_visits

    now = now or timezone.now()
    tz = _tenant_tz(master.tenant)
    start, end = _day_bounds(now, tz)

    visits = master_visits(master, start=start, end=end)
    date_label = now.astimezone(tz).strftime("%d.%m")
    if not visits:
        return f"На {date_label} записей нет."

    lines = [f"*{date_label}* — {len(visits)}:"]
    for visit in visits[:MAX_LISTED]:
        when = visit.visit_at.astimezone(tz).strftime("%H:%M") if visit.visit_at else "—"
        service = visit.service_name or "услуга не указана"
        lines.append(f"{when} · {visit.client_name} · {service}")
    if len(visits) > MAX_LISTED:
        lines.append(f"…и ещё {len(visits) - MAX_LISTED}")
    return "\n".join(lines)


def pending_requests(tenant) -> str:
    """Schedule-change requests waiting on an admin.

    The approve/reject endpoints have existed and worked all along; what
    was missing was any way for an admin to find out a request was filed
    (nothing notified them). This is that missing half — read-only for now:
    deciding still happens in the Mini App, where the confirmation and the
    audit trail already live.
    """

    from apps.scheduling.models import ScheduleChangeRequest

    tz = _tenant_tz(tenant)
    rows = list(
        ScheduleChangeRequest.objects.filter(
            status=ScheduleChangeRequest.Status.PENDING,
        )
        .select_related("master")
        .order_by("created_at")[: MAX_LISTED + 1]
    )
    if not rows:
        return "Заявок от мастеров нет."

    lines = ["*Заявки от мастеров*"]
    for row in rows[:MAX_LISTED]:
        master_name = getattr(row.master, "name", "мастер")
        when = row.created_at.astimezone(tz).strftime("%d.%m") if row.created_at else ""
        lines.append(f"• {master_name} · {when}")
    if len(rows) > MAX_LISTED:
        lines.append("…и ещё")
    lines.append("\nОдобрить или отклонить — в кабинете салона.")
    return "\n".join(lines)
