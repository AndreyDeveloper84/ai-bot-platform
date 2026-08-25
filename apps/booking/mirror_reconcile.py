"""Mirror ↔ canon reconciliation sweep (DRF-1111 + DRF-1161).

### Why this exists

``RemoteBookingProxy`` is filled by events out of Ayla. A booking created
by a path that emits no event — or whose event dead-lettered — never
reaches the mirror, and the salon day then silently disagrees with the
canonical state. From inside the salon there is no way to tell which side
is lying. This module is the detector the DRF-1161 acceptance of the
«вариант B» day-journal design was conditioned on: a periodic,
identifier-based comparison of live bookings on both sides.

### What it deliberately is NOT

* **No autofix.** A divergence is a symptom whose causes differ (missed
  event, consumer lag, an out-of-scope creation path), so the sweep logs
  an event and pages a human; it never writes to either side.
* **Not a counter.** «24 против 8» with equal future counts is invisible
  to a count comparison, so the comparison is by ``appointment_id``, with
  status and start checked per shared id.
* **Not a proof of consumer completeness.** It catches any creation path
  we did not think of, including one added next month — that is the
  property a completeness proof cannot have.

### The canonical read

Ayla exposes exactly one tenant-wide booking read to the service:
``GET /api/v1/tenants/me/day/?date=…`` (DRF-1063, service-bearer auth
since DRF-1297). The internal surface is per-user, and per-user
enumeration cannot see a booking whose creation event never arrived — the
precise failure this detector exists for. The sweep therefore fans the
day endpoint out over a bounded window of ``AYLA_MIRROR_RECONCILE_WINDOW_DAYS``
(default 45) tenant-local days, starting today. Rows beyond the window
are excluded on BOTH sides, so the comparison stays fair and the fan-out
stays bounded.

The window's lower edge is the start of the tenant-local today, not
``now``: a visit that started this morning and is still ``confirmed``
is live on both sides and must compare cleanly.

### Actor

The day endpoint requires a named human who administers the salon
(``IsTenantAdmin``). The sweep names the tenant's active Owner, falling
back to an active Admin (``TenantStaff``). A tenant with neither is
skipped with a warning — the detector is blind there, and the log says so.

### Threshold (порог)

A divergence **logs on every tick** but **pages only when the identical
fingerprint repeats on two consecutive ticks**. A booking whose event is
still in flight between Ayla and the consumer looks like a divergence for
a few seconds; requiring persistence across two hourly ticks filters that
without hiding anything real. State lives in the Django cache
(``PERSIST_TTL_S``), so a worker restart re-arms the two-tick clock rather
than corrupting it.

A failed read (Ayla down, actor lost rights) is NOT a divergence: the
tenant is reported as ``unchecked`` and no page fires — the detector must
never cry «расхождение» off an unreachable canon.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, time, timedelta
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.booking.mirror_status import LIVE_STATUSES, TERMINAL_STATUSES
from apps.booking.models import RemoteBookingProxy
from apps.integrations.ayla.salon_client import (
    SalonAPIError,
    SalonNotConfigured,
    SalonUnavailable,
    get_salon_client,
)
from apps.integrations.ayla.user_proxy import external_user_id_for
from apps.observability.alerting import page as alert_page
from apps.tenancy.models import Tenant, TenantStaff

logger = logging.getLogger(__name__)

DEFAULT_TZ = "Europe/Moscow"

#: Start times on both sides name the same instant; 60 s of slack covers
#: wire rounding without letting a real reschedule through.
START_TOLERANCE_S = 60

#: How long the divergence fingerprint survives between ticks. Well past
#: the hourly cadence so a couple of missed beats don't re-arm the
#: two-tick clock; bounded so a long-dead beat doesn't hold stale state.
PERSIST_TTL_S = 6 * 3600

_CACHE_KEY_PREFIX = "booking:mirror_reconcile:fp"

#: ``page`` argument default marker — see :func:`run_mirror_reconciliation`.
_USE_MODULE_PAGE = object()


def _is_live(status: str) -> bool:
    """Whether the booking is still expected to happen.

    Same rule applied to BOTH sides, so a status slug this code has never
    seen classifies identically in the mirror and in Ayla. Unknown counts
    as live: a slug we don't know must wake the detector, never read as
    «запись завершена».
    """

    return status in LIVE_STATUSES or status not in TERMINAL_STATUSES


@dataclass(frozen=True)
class BookingFact:
    """The three facts the comparison needs about one booking."""

    appointment_id: str
    status: str
    start_at: datetime


@dataclass(frozen=True)
class TenantDivergence:
    """Identifier-level divergence between the mirror and the canon.

    Every tuple carries bare appointment ids and status/time values — no
    PII, so the report is safe to log and page verbatim.
    """

    tenant_slug: str
    #: Live in Ayla, no live mirror row (missed creation event — DRF-1111).
    ayla_only: tuple[str, ...] = ()
    #: Live mirror row, absent from Ayla entirely (hard delete — DRF-1034).
    mirror_only: tuple[str, ...] = ()
    #: ``(id, ayla_status, mirror_status)`` — liveness disagrees, or both
    #: are live under different slugs.
    status_mismatch: tuple[tuple[str, str, str], ...] = ()
    #: ``(id, ayla_start_iso, mirror_start_iso)`` — both live, different
    #: start (missed reschedule event).
    start_mismatch: tuple[tuple[str, str, str], ...] = ()

    def is_clean(self) -> bool:
        return not (
            self.ayla_only or self.mirror_only or self.status_mismatch or self.start_mismatch
        )

    def fingerprint(self) -> str:
        """Stable hash of the full divergence content.

        Two ticks page only when their fingerprints are equal — so the
        fingerprint must change when ANY id or detail changes. Empty for a
        clean tenant.
        """

        if self.is_clean():
            return ""
        parts = [f"ayla_only:{i}" for i in self.ayla_only]
        parts += [f"mirror_only:{i}" for i in self.mirror_only]
        parts += ["status:" + ":".join(row) for row in self.status_mismatch]
        parts += ["start:" + ":".join(row) for row in self.start_mismatch]
        return hashlib.sha256("|".join(sorted(parts)).encode()).hexdigest()


def _tenant_tz(tenant: Tenant) -> ZoneInfo:
    """The tenant's timezone, falling back to Moscow.

    «Сегодня» means today for the salon, not for UTC — the same rule the
    salon-day projection applies.
    """

    try:
        return ZoneInfo(getattr(tenant, "timezone", "") or DEFAULT_TZ)
    except Exception:  # noqa: BLE001 — a bad tz string must not blind the sweep
        return ZoneInfo(DEFAULT_TZ)


def _window(
    tenant: Tenant, now: datetime, window_days: int
) -> tuple[datetime, datetime, list[date_cls]]:
    """``[start, end)`` UTC instants + the tenant-local dates to fan out."""

    tz = _tenant_tz(tenant)
    today = now.astimezone(tz).date()
    start = datetime.combine(today, time.min, tzinfo=tz)
    end = datetime.combine(today + timedelta(days=window_days), time.min, tzinfo=tz)
    return start, end, [today + timedelta(days=i) for i in range(window_days)]


def collect_mirror_facts(tenant: Tenant, start: datetime, end: datetime) -> dict[str, BookingFact]:
    """Every mirror row starting inside the window, any status.

    Terminal rows are collected on purpose: a row cancelled in the mirror
    but still live in Ayla is a divergence, and the comparison can only
    say so if it sees the terminal row.
    """

    facts: dict[str, BookingFact] = {}
    for row in RemoteBookingProxy.all_tenants.filter(
        tenant=tenant, start_at__gte=start, start_at__lt=end
    ):
        key = str(row.appointment_id)
        facts[key] = BookingFact(key, str(row.status or ""), row.start_at)
    return facts


def collect_ayla_facts(
    client: Any,
    *,
    actor_external_id: str,
    tenant_slug: str,
    dates: list[date_cls],
    start: datetime,
    end: datetime,
) -> dict[str, BookingFact]:
    """The canon's bookings across the day fan-out, deduped by id.

    Rows starting before the window are dropped: the day endpoint reports
    overnight bookings on the day they bleed into, and comparing those
    against a ``start >= window`` mirror filter would invent a divergence
    out of a bookkeeping artefact.
    """

    facts: dict[str, BookingFact] = {}
    for day in dates:
        # ``get_day`` is the registered route row (SALON_ROUTES) — the
        # detector must not grow a second, undeclared name for the same
        # endpoint. The envelope is unwrapped and fail-loud inside the
        # client; the one residual shape fact the reconciliation itself
        # depends on — ``masters`` being a list — is checked here, because
        # a day payload without it must never read as «у салона нет
        # записей»: that would silence the detector exactly when the
        # contract moved under it.
        payload = client.get_day(
            actor_external_id=actor_external_id,
            tenant_slug=tenant_slug,
            date=day.isoformat(),
        )
        if not isinstance(payload.get("masters"), list):
            raise SalonUnavailable("upstream returned an unrecognised day payload")
        for master in payload.get("masters") or []:
            for raw in (master or {}).get("bookings") or []:
                if not isinstance(raw, dict):
                    continue
                appointment_id = str(raw.get("appointment_id") or "")
                start_at = parse_datetime(str(raw.get("start_at") or ""))
                # Naive would TypeError against the aware window bounds
                # below and crash the whole tenant's tick — treat as
                # unparseable instead.
                if not appointment_id or start_at is None or start_at.tzinfo is None:
                    # A booking we cannot identify cannot be compared;
                    # skipping it here would hide a shape drift, so say so.
                    logger.warning(
                        "booking.mirror_reconcile.unparseable_row tenant=%s date=%s",
                        tenant_slug,
                        day.isoformat(),
                    )
                    continue
                if start_at < start or start_at >= end:
                    continue
                facts.setdefault(
                    appointment_id,
                    BookingFact(appointment_id, str(raw.get("status") or ""), start_at),
                )
    return facts


def compare(
    tenant: Tenant,
    mirror: Mapping[str, BookingFact],
    ayla: Mapping[str, BookingFact],
) -> TenantDivergence:
    """Pair the two fact sets by ``appointment_id``."""

    ayla_only: list[str] = []
    mirror_only: list[str] = []
    status_mismatch: list[tuple[str, str, str]] = []
    start_mismatch: list[tuple[str, str, str]] = []

    for appointment_id in sorted(set(mirror) | set(ayla)):
        m = mirror.get(appointment_id)
        a = ayla.get(appointment_id)

        if a is None:
            assert m is not None  # the id came from the union
            if _is_live(m.status):
                mirror_only.append(appointment_id)
            continue
        if m is None:
            if _is_live(a.status):
                ayla_only.append(appointment_id)
            continue

        m_live = _is_live(m.status)
        a_live = _is_live(a.status)
        if m_live != a_live:
            # Liveness disagrees — a missed cancel/confirm event whichever
            # way it points.
            status_mismatch.append((appointment_id, a.status, m.status))
        elif m_live and m.status != a.status:
            # Both live but under different slugs (confirmed vs
            # awaiting_payment) — the day screens still show the visit,
            # yet the mirror is stale and the detector should say so.
            status_mismatch.append((appointment_id, a.status, m.status))
        elif m_live and abs((a.start_at - m.start_at).total_seconds()) > START_TOLERANCE_S:
            start_mismatch.append((appointment_id, a.start_at.isoformat(), m.start_at.isoformat()))

    return TenantDivergence(
        tenant_slug=tenant.slug,
        ayla_only=tuple(sorted(ayla_only)),
        mirror_only=tuple(sorted(mirror_only)),
        status_mismatch=tuple(sorted(status_mismatch)),
        start_mismatch=tuple(sorted(start_mismatch)),
    )


def reconcile_tenant(
    tenant: Tenant,
    *,
    client: Any,
    actor_external_id: str,
    now: datetime | None = None,
    window_days: int | None = None,
) -> TenantDivergence:
    """Compare one tenant's live bookings on both sides. Read-only."""

    now = now or timezone.now()
    days = (
        window_days
        if window_days is not None
        else int(getattr(settings, "AYLA_MIRROR_RECONCILE_WINDOW_DAYS", 45))
    )
    start, end, dates = _window(tenant, now, days)
    mirror = collect_mirror_facts(tenant, start, end)
    ayla = collect_ayla_facts(
        client,
        actor_external_id=actor_external_id,
        tenant_slug=tenant.slug,
        dates=dates,
        start=start,
        end=end,
    )
    return compare(tenant, mirror, ayla)


def find_reconcile_actor(tenant: Tenant) -> Any | None:
    """The human the sweep names to Ayla: active Owner, else active Admin.

    The day endpoint resolves ``X-External-User-ID`` and checks THAT
    person's rights in the tenant, so the sweep needs a real staff row —
    the same attribution rule the salon console's writes live under.
    """

    for role in (TenantStaff.Role.OWNER, TenantStaff.Role.ADMIN):
        staff = (
            TenantStaff.all_tenants.filter(tenant=tenant, role=role, deactivated_at__isnull=True)
            .select_related("bot_user")
            .first()
        )
        if staff is not None:
            return staff.bot_user
    return None


def _cache_key(tenant: Tenant) -> str:
    return f"{_CACHE_KEY_PREFIX}:{tenant.id}"


def _record_clean(tenant: Tenant) -> None:
    key = _cache_key(tenant)
    if cache.get(key):
        cache.delete(key)
        logger.info("booking.mirror_reconcile.recovered tenant=%s", tenant.slug)


def _record_divergence(
    tenant: Tenant,
    report: TenantDivergence,
    page_fn: Callable[..., Any] | None,
) -> None:
    logger.warning(
        "booking.mirror_reconcile.diverged tenant=%s ayla_only=%d mirror_only=%d "
        "status_mismatch=%d start_mismatch=%d ayla_only_ids=%s mirror_only_ids=%s "
        "status_mismatches=%s start_mismatches=%s",
        tenant.slug,
        len(report.ayla_only),
        len(report.mirror_only),
        len(report.status_mismatch),
        len(report.start_mismatch),
        list(report.ayla_only),
        list(report.mirror_only),
        list(report.status_mismatch),
        list(report.start_mismatch),
    )

    fingerprint = report.fingerprint()
    key = _cache_key(tenant)
    repeated = bool(fingerprint) and cache.get(key) == fingerprint
    cache.set(key, fingerprint, timeout=PERSIST_TTL_S)
    if not repeated:
        # First sighting (or a changed divergence): the event-in-flight
        # window has not closed yet. Logged above; pages on the next tick
        # if it persists unchanged.
        return
    if page_fn is None:
        return
    page_fn(
        "error",
        f"Зеркало броней разошлось с Ayla: {tenant.slug}",
        _page_body(report),
        dedup_key=f"booking.mirror_reconcile:{tenant.id}:{fingerprint[:12]}",
    )


def _page_body(report: TenantDivergence) -> str:
    lines = [
        f"Салон: {report.tenant_slug}",
        f"в Ayla, нет в зеркале: {len(report.ayla_only)} {list(report.ayla_only)}",
        f"в зеркале, нет в Ayla: {len(report.mirror_only)} {list(report.mirror_only)}",
        f"статус разошёлся: {list(report.status_mismatch)}",
        f"время разошлось: {list(report.start_mismatch)}",
        "",
        "Автофикса нет by design: расхождение — симптом, причина разбирается руками.",
    ]
    return "\n".join(lines)


def run_mirror_reconciliation(
    *,
    client_factory: Callable[[], Any] | None = None,
    now: datetime | None = None,
    page: Callable[..., Any] | None | object = _USE_MODULE_PAGE,
    window_days: int | None = None,
) -> dict[str, Any]:
    """Sweep every tenant that participates in the Ayla booking flow.

    «Participates» = has at least one ``RemoteBookingProxy`` row ever OR
    at least one active owner/admin staff row. Mirror history alone is
    not enough: a tenant whose events ALL failed has an empty mirror, and
    excluding it would make the detector blind in exactly the failure it
    exists for. The union stays cheap — tenants with neither (the
    platform-global one, bare onboarding shells) are not swept.

    ``page`` resolution: the sentinel default uses the module-level
    alerting page (the beat task's path); an explicit callable is the
    test seam; an explicit ``None`` disables paging (the management
    command's path — a human is already looking at its output).
    """

    if page is _USE_MODULE_PAGE:
        page_fn: Callable[..., Any] | None = alert_page
    else:
        page_fn = page  # type: ignore[assignment]
    client_factory = client_factory or get_salon_client
    now = now or timezone.now()

    summary: dict[str, Any] = {
        "configured": True,
        "checked": [],
        "diverged": [],
        "skipped_no_actor": [],
        "unchecked": [],
        "reports": {},
    }

    try:
        client = client_factory()
    except SalonNotConfigured:
        # A deploy without the Ayla seam configured has nothing to
        # reconcile against; say so once per tick, don't pretend clean.
        logger.warning("booking.mirror_reconcile.not_configured")
        summary["configured"] = False
        return summary

    mirror_tenant_ids = RemoteBookingProxy.all_tenants.values_list(
        "tenant_id", flat=True
    ).distinct()
    staffed_tenant_ids = (
        TenantStaff.all_tenants.filter(
            role__in=(TenantStaff.Role.OWNER, TenantStaff.Role.ADMIN),
            deactivated_at__isnull=True,
        )
        .values_list("tenant_id", flat=True)
        .distinct()
    )
    tenants = Tenant.objects.filter(
        Q(id__in=mirror_tenant_ids) | Q(id__in=staffed_tenant_ids),
        is_active=True,
    ).order_by("slug")

    for tenant in tenants:
        actor = find_reconcile_actor(tenant)
        if actor is None:
            logger.warning("booking.mirror_reconcile.no_actor tenant=%s", tenant.slug)
            summary["skipped_no_actor"].append(tenant.slug)
            continue
        try:
            report = reconcile_tenant(
                tenant,
                client=client,
                actor_external_id=external_user_id_for(actor),
                now=now,
                window_days=window_days,
            )
        except SalonAPIError as exc:
            # Unreachable canon is not a divergence. The tenant goes
            # unchecked; the log carries why.
            logger.warning(
                "booking.mirror_reconcile.unchecked tenant=%s err=%s",
                tenant.slug,
                exc,
            )
            summary["unchecked"].append(tenant.slug)
            continue
        except Exception:  # noqa: BLE001 — one tenant's bug must not blind the rest
            logger.exception("booking.mirror_reconcile.tenant_failed tenant=%s", tenant.slug)
            summary["unchecked"].append(tenant.slug)
            continue

        summary["reports"][tenant.slug] = report
        if report.is_clean():
            _record_clean(tenant)
            summary["checked"].append(tenant.slug)
        else:
            _record_divergence(tenant, report, page_fn)
            summary["diverged"].append(tenant.slug)

    return summary
