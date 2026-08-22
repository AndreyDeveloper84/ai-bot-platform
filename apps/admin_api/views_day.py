"""Admin REST view — the salon's day (Phase 2).

``GET /api/v1/admin/day/?date=YYYY-MM-DD``

Owner / Admin / — via :func:`require_admin_role` — read-only. Receptionist
is rejected by that decorator today; when the front-desk role is opened up
this is the first endpoint it should get, because reading the day is
exactly the receptionist's job.

The business logic lives in :mod:`apps.admin_api.services.salon_day`; this
is a thin HTTP shell that parses one query parameter and serialises one
dataclass.
"""

from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import datetime
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone as dj_timezone
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_role
from apps.admin_api.services.salon_day import (
    DayVisit,
    SalonDay,
    build_salon_day,
    tenant_tz,
)

logger = logging.getLogger(__name__)


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


def _visit_payload(v: DayVisit) -> dict[str, Any]:
    return {
        "id": v.id,
        "start_at": v.start_at.isoformat() if v.start_at else None,
        "end_at": v.end_at.isoformat() if v.end_at else None,
        "duration_min": v.duration_min,
        "status": v.status,
        "service_id": v.service_id,
        "service_name": v.service_name,
        # First name + last initial only. The administrator needs to
        # recognise the client, not to hold their identity documents, and
        # no phone crosses this boundary in any shape (DRF-1039).
        "client_first_name": v.client_first_name,
        "client_last_initial": v.client_last_initial,
        "is_in_progress": v.is_in_progress,
    }


def _day_payload(day: SalonDay) -> dict[str, Any]:
    return {
        "date": day.date.isoformat(),
        "timezone": day.timezone_name,
        "summary": {
            "total": day.summary.total,
            "upcoming": day.summary.upcoming,
            "completed": day.summary.completed,
            "released": day.summary.released,
        },
        "masters": [
            {
                "master_id": m.master_id,
                "name": m.name,
                "is_active": m.is_active,
                "visits": [_visit_payload(v) for v in m.visits],
            }
            for m in day.masters
        ],
        # Present even when empty so the frontend never has to guess
        # whether the key is missing or the list is.
        "orphan_visits": [_visit_payload(v) for v in day.orphan_visits],
    }


@require_http_methods(["GET"])
@require_admin_role
def salon_day(request: HttpRequest) -> HttpResponse:
    """Read the salon's day for one tenant-local calendar date.

    ``date`` defaults to today **in the tenant's timezone**, not the
    server's: a salon in Kaliningrad opening at 09:00 must not be shown
    yesterday because the container runs on UTC.
    """

    tenant = request.tenant  # type: ignore[attr-defined]

    raw_date = (request.GET.get("date") or "").strip()
    if raw_date:
        try:
            day = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            return _error("bad_request", "date must be YYYY-MM-DD", 400)
    else:
        day = dj_timezone.now().astimezone(tenant_tz(tenant)).date()

    if not isinstance(day, date_cls):  # pragma: no cover — defensive
        return _error("bad_request", "date must be YYYY-MM-DD", 400)

    result = build_salon_day(tenant, day=day)
    return JsonResponse(_day_payload(result))


__all__ = ["salon_day"]
