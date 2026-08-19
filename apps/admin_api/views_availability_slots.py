"""Admin REST view — bookable slots for the manual-booking flow.

``GET /api/v1/admin/booking-slots/?master_id=&service_id=&date=``

Wraps Ayla's canonical slots read
(:meth:`AylaBookingHTTPClient.get_available_times`). The salon surface
speaks in catalog ids; the translation to Ayla's ids happens here, so no
Ayla identifier ever has to travel through the Mini App.

### Why this endpoint refuses instead of returning an empty list

UX contract §16 requires the picker to distinguish available, blocked,
non-working and **stale** intervals; §17 forbids the client from
computing an authoritative slot from stale local data. Both collapse into
one implementation rule: **an upstream failure must not be serialised as
«no slots»**.

An empty list and an unreachable schedule look identical to the person
holding the phone, and only one of them means «offer another day». The
booking client already learned this the hard way — its own docstring says
«no "error day" == "busy day"» — and this view keeps the same promise at
the HTTP boundary: 503 with a slug, never 200 with an empty array.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_role
from apps.admin_api.services.salon_day import tenant_tz
from apps.admin_api.views import _get_master_or_404
from apps.catalog.models import CatalogService

logger = logging.getLogger(__name__)


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


def _slot_payload(slot: Any) -> dict[str, Any]:
    duration_s = getattr(slot, "duration_s", None)
    return {
        "time": getattr(slot, "time", ""),
        # Ayla does not always send a full timestamp; the field stays
        # nullable rather than being reconstructed here. Reconstructing it
        # would mean the client picking a timezone, which is precisely the
        # local computation §17 rules out.
        "start_at": getattr(slot, "datetime", None),
        "duration_min": int(duration_s // 60) if duration_s else None,
    }


@require_http_methods(["GET"])
@require_admin_role
def booking_slots(request: HttpRequest) -> HttpResponse:
    """Bookable starts for one master, one service, one day.

    All three parameters are required. ``service_id`` is deliberately not
    optional: Ayla's slots action rejects a service-less query, and more
    importantly a slot list that does not know the duration is a list of
    times that mean nothing (UX contract §12).
    """

    tenant = request.tenant  # type: ignore[attr-defined]

    master_id = (request.GET.get("master_id") or "").strip()
    service_id = (request.GET.get("service_id") or "").strip()
    raw_date = (request.GET.get("date") or "").strip()

    missing = [
        name
        for name, value in (
            ("master_id", master_id),
            ("service_id", service_id),
            ("date", raw_date),
        )
        if not value
    ]
    if missing:
        return _error("bad_request", f"required: {', '.join(missing)}", 400)

    try:
        day = datetime.strptime(raw_date, "%Y-%m-%d").date()
    except ValueError:
        return _error("bad_request", "date must be YYYY-MM-DD", 400)

    master = _get_master_or_404(tenant.id, master_id)
    if master is None:
        return _error("not_found", "master not found", 404)

    service = CatalogService.objects.filter(tenant_id=tenant.id, id=service_id).first()
    if service is None:
        return _error("not_found", "service not found", 404)
    if not service.ayla_service_id:
        # A catalog row that was never bridged to Ayla cannot be booked
        # there. Saying so is better than asking for slots with an empty
        # id and reporting the resulting emptiness as «no free time».
        logger.warning(
            "admin_api.booking_slots.service_not_bridged service=%s tenant=%s",
            service.id,
            tenant.id,
        )
        return _error(
            "service_not_bookable",
            "this service is not linked to the booking system yet",
            409,
        )

    from apps.integrations.ayla.booking_client import (
        BookingAPIError,
        BookingUnavailableError,
        get_ayla_booking_client,
    )

    try:
        slots = get_ayla_booking_client().get_available_times(
            specialist_id=str(master.id),
            date=day.isoformat(),
            service_id=str(service.ayla_service_id),
        )
    except BookingUnavailableError as exc:
        # Transient: timeout, network, open circuit, exhausted retries.
        logger.warning(
            "admin_api.booking_slots.upstream_unavailable master=%s date=%s err=%s",
            master.id,
            day,
            exc,
        )
        return _error(
            "schedule_unavailable",
            "the schedule is temporarily unreachable — try again in a moment",
            503,
        )
    except BookingAPIError as exc:
        logger.warning(
            "admin_api.booking_slots.upstream_error master=%s date=%s err=%s",
            master.id,
            day,
            exc,
        )
        return _error(
            "schedule_error",
            "the schedule refused the request",
            502,
        )

    return JsonResponse(
        {
            "date": day.isoformat(),
            # The review screen has to state the timezone the appointment
            # is in (UX contract §18). It comes from the server because
            # the salon's timezone is the salon's fact, not the device's.
            "timezone": str(tenant_tz(tenant)),
            "master_id": str(master.id),
            "service_id": str(service.id),
            "duration_min": service.duration_min,
            "slots": [_slot_payload(s) for s in slots],
        }
    )


__all__ = ["booking_slots"]
