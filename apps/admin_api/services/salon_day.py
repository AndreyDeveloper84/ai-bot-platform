"""The salon's day, as the administrator needs to see it (Phase 2).

### What this is for

The salon has had the operational backend since 15.08 and no button to
reach it. This is the projection the front desk actually works from:
every master's day on one screen, in the tenant's own timezone, with
enough per-visit context to act — and nothing more than that.

### Where the visits come from

``RemoteBookingProxy`` — bot-platform's mirror of Ayla's canonical
``Appointment`` (ADR-0009). The same source the master surface reads
since DRF-1085.

Reading the mirror rather than calling Ayla REST per render is a
deliberate choice, and the reason is not performance:

* **The two days must agree.** The administrator opens the salon's day,
  the master opens their own. Built from different sources they will
  drift, and from inside the salon there is no way to tell which one is
  lying. One source is worth more here than one extra hop of freshness.
* ``BookingRequest`` is not an option and never was: on the pilot it
  holds six rows with ``master_id IS NULL`` on every one. That table has
  already produced two silent-empty defects (DRF-1085, DRF-1139).

Mutations are the other half of the same rule and do NOT belong here:
manual booking, reschedule, cancel and visit closure go to Ayla over
REST, because Ayla owns booking state (ADR-0009 rule 5).

### What is deliberately absent

**No customer phone, by any path.** DRF-1039. The projection resolves a
first name and a last initial and stops there; there is no code path in
this module that reads ``BotUser.phone``, so the rule holds by
construction rather than by everyone remembering it at each call site.

### Known duplication

The service/client name lookups below mirror the private helpers in
``apps.master_api.services.visit_source``. That module is another
window's territory (brief §5 anti-touch) and its queries are per-master,
while this projection needs the whole tenant in one pass. The public
parts that matter — which statuses count as booked, released, upcoming —
are imported from it rather than restated, so the two cannot disagree
about the thing that would actually break. Follow-up candidate K-7:
extract a shared visit source once the salon-bot window lands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import datetime, time, timedelta
from typing import Iterable
from uuid import UUID
from zoneinfo import ZoneInfo

from django.utils import timezone as dj_timezone

from apps.booking.models import RemoteBookingProxy
from apps.catalog.models import CatalogMaster, CatalogService
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope

# Imported, not restated: a disagreement about which statuses occupy a
# slot is the kind of drift that makes two screens show two different
# days. Names are duplicated below; this is not.
from apps.master_api.services.visit_source import (
    GUEST_NAME,
    RELEASED_STATUSES,
    UPCOMING_STATUSES,
)

DEFAULT_TZ = "Europe/Moscow"


def tenant_tz(tenant) -> ZoneInfo:
    """The tenant's timezone, falling back to Moscow.

    «Today» has to mean today for the salon, not for UTC — a visit at
    01:00 MSK belongs to the day the receptionist calls today, not to the
    previous UTC date.
    """

    try:
        return ZoneInfo(getattr(tenant, "timezone", "") or DEFAULT_TZ)
    except Exception:  # noqa: BLE001 — a bad tz string must not 500 the day
        return ZoneInfo(DEFAULT_TZ)


def day_bounds_utc(day: date_cls, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[start, end) of a tenant-local calendar day, in UTC."""

    start_local = datetime.combine(day, time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(ZoneInfo("UTC")), end_local.astimezone(ZoneInfo("UTC"))


def _first_name(full_name: str) -> str:
    parts = (full_name or "").strip().split()
    return parts[0] if parts else ""


def _last_initial(full_name: str) -> str:
    parts = (full_name or "").strip().split()
    if len(parts) < 2 or not parts[1]:
        return ""
    return f"{parts[1][:1]}."


def _resolve_service_names(service_ids: Iterable[UUID | None], tenant_id) -> dict[UUID, str]:
    """Batch ``ayla_service_id`` → catalog name. Missing → empty string.

    Uses the tenant-scoped default manager, not ``all_tenants``: cross-
    tenant catalog reads belong to marketplace discovery alone (MKT1 /
    #1018). :func:`build_salon_day` enters ``tenant_scope`` so this is
    bound; the explicit ``tenant_id`` filter is defence in depth.
    """

    ids = {sid for sid in service_ids if sid}
    if not ids:
        return {}
    rows = CatalogService.objects.filter(tenant_id=tenant_id, ayla_service_id__in=ids).values_list(
        "ayla_service_id", "name"
    )
    return {sid: name for sid, name in rows if sid is not None}


def _resolve_client_names(bot_user_ids: Iterable[UUID | None], tenant_id) -> dict[UUID, str]:
    """Batch BotUser id → display name. Never touches ``phone`` (DRF-1039)."""

    ids = {bid for bid in bot_user_ids if bid}
    if not ids:
        return {}
    out: dict[UUID, str] = {}
    for pk, client_name, display_name in BotUser.objects.filter(
        tenant_id=tenant_id, id__in=ids
    ).values_list("id", "client_name", "display_name"):
        out[pk] = (client_name or "").strip() or (display_name or "").strip() or GUEST_NAME
    return out


@dataclass(frozen=True)
class DayVisit:
    """One visit on the salon's day."""

    id: str
    start_at: datetime
    end_at: datetime | None
    duration_min: int
    status: str
    service_name: str
    client_first_name: str
    client_last_initial: str
    is_in_progress: bool

    @property
    def is_released(self) -> bool:
        """Cancelled / no-show — the slot is free again."""
        return self.status in RELEASED_STATUSES


@dataclass(frozen=True)
class DayMaster:
    """One master's column on the day."""

    master_id: str
    name: str
    is_active: bool
    visits: list[DayVisit] = field(default_factory=list)


@dataclass(frozen=True)
class DaySummary:
    total: int
    upcoming: int
    completed: int
    released: int


@dataclass(frozen=True)
class SalonDay:
    date: date_cls
    timezone_name: str
    masters: list[DayMaster]
    summary: DaySummary
    #: Visits whose ``specialist_id`` matches no catalog master. Surfaced
    #: rather than dropped: a booking nobody can see is exactly the
    #: failure mode this whole window exists to stop.
    orphan_visits: list[DayVisit] = field(default_factory=list)


def _build_visit(proxy: RemoteBookingProxy, *, service_names, client_names, now) -> DayVisit:
    duration = 0
    if proxy.start_at and proxy.end_at:
        duration = max(int((proxy.end_at - proxy.start_at).total_seconds() // 60), 0)

    client_name = (
        client_names.get(proxy.bot_user_id, GUEST_NAME) if proxy.bot_user_id else GUEST_NAME
    )
    in_progress = (
        proxy.status not in RELEASED_STATUSES
        and proxy.start_at is not None
        and proxy.end_at is not None
        and proxy.start_at <= now < proxy.end_at
    )
    return DayVisit(
        id=str(proxy.appointment_id),
        start_at=proxy.start_at,
        end_at=proxy.end_at,
        duration_min=duration,
        status=proxy.status,
        service_name=service_names.get(proxy.service_id, "") if proxy.service_id else "",
        client_first_name=_first_name(client_name),
        client_last_initial=_last_initial(client_name),
        is_in_progress=in_progress,
    )


def build_salon_day(tenant, *, day: date_cls, now: datetime | None = None) -> SalonDay:
    """Every master's visits for one tenant-local calendar day.

    Cancelled and no-show visits are included rather than filtered. The
    front desk needs to see that a slot freed up — an absent row and a
    cancelled row look identical on screen otherwise, and the difference
    is the whole point of asking.

    Masters with nothing booked are included too, as empty columns: «Инна
    сегодня свободна» is an answer, and a missing column is not.
    """

    if now is None:
        now = dj_timezone.now()
    tz = tenant_tz(tenant)
    start_utc, end_utc = day_bounds_utc(day, tz)

    # Enter the scope explicitly rather than relying on the caller. The
    # HTTP path already runs inside `require_admin_role`'s tenant_scope,
    # but this projection is also called directly (tests, and any future
    # digest job), and a tenant-scoped read that silently depends on an
    # ambient ContextVar is one refactor away from reading everyone's day.
    with tenant_scope(tenant):
        proxies = list(
            RemoteBookingProxy.objects.filter(
                tenant_id=tenant.id,
                start_at__gte=start_utc,
                start_at__lt=end_utc,
            ).order_by("start_at")
        )

        service_names = _resolve_service_names((p.service_id for p in proxies), tenant.id)
        client_names = _resolve_client_names((p.bot_user_id for p in proxies), tenant.id)

        masters = list(
            CatalogMaster.objects.filter(tenant_id=tenant.id, archived_at__isnull=True).order_by(
                "name"
            )
        )
    by_master: dict[UUID, list[DayVisit]] = {m.id: [] for m in masters}
    orphans: list[DayVisit] = []

    for proxy in proxies:
        visit = _build_visit(proxy, service_names=service_names, client_names=client_names, now=now)
        bucket = by_master.get(proxy.specialist_id) if proxy.specialist_id else None
        if bucket is None:
            orphans.append(visit)
        else:
            bucket.append(visit)

    day_masters = [
        DayMaster(
            master_id=str(m.id),
            name=m.name,
            is_active=m.is_active,
            visits=by_master[m.id],
        )
        for m in masters
    ]

    every_visit = [v for m in day_masters for v in m.visits] + orphans
    summary = DaySummary(
        total=len(every_visit),
        upcoming=sum(1 for v in every_visit if v.status in UPCOMING_STATUSES),
        completed=sum(1 for v in every_visit if v.status == "completed"),
        released=sum(1 for v in every_visit if v.is_released),
    )

    return SalonDay(
        date=day,
        timezone_name=str(tz),
        masters=day_masters,
        summary=summary,
        orphan_visits=orphans,
    )


__all__ = [
    "DayMaster",
    "DaySummary",
    "DayVisit",
    "SalonDay",
    "build_salon_day",
    "day_bounds_utc",
    "tenant_tz",
]
