"""Admin REST views — read a booking's canonical version, and close the visit.

``GET  /api/v1/admin/bookings/<appointment_id>/``
``POST /api/v1/admin/bookings/<appointment_id>/complete/``
``POST /api/v1/admin/bookings/<appointment_id>/reschedule/``

### Why these are two endpoints and not one

The obvious shape — «close it, and fetch the version yourself on the way»
— destroys the thing the version is for. `expected_version` exists so a
booking that changed since the operator looked at it cannot be acted on
blind. A server that reads the version inside the same request that
writes would always send the current one, and the guard would never fire:
machinery that runs and matches nothing, by construction.

That is the exact defect DRF-1232 fixed on the Ayla side, where a fresh
idempotency key was invented per request and a unique constraint stood
without ever triggering. Repeating it here would be worse for having been
seen once already.

So the version travels **through the operator**: read it, show them the
visit it describes, and send back the value they were shown. The pause
between the two is a human one, and that pause is precisely the window
the guard protects.

### Why the read is not folded into the day journal

The day is built from the local mirror and shows every visit of every
master. Fetching a canonical version for each would be one cross-service
call per row, on the screen the front desk opens most often, to support
an action they take on one row at a time.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_role
from apps.booking.models import RemoteBookingProxy
from apps.identity.models import BotUser
from apps.integrations.ayla.user_proxy import external_user_id_for

logger = logging.getLogger(__name__)


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


def _outcome(outcome: str, detail: str, status: int, **extra: Any) -> JsonResponse:
    return JsonResponse({"outcome": outcome, "detail": detail, **extra}, status=status)


def _own_booking(tenant_id, appointment_id) -> RemoteBookingProxy | None:
    """This salon's booking, or None.

    Checked against the mirror before anything is forwarded, so an id
    belonging to another salon cannot be confirmed as existing by the
    shape of the refusal.
    """

    return RemoteBookingProxy.objects.filter(
        tenant_id=tenant_id, appointment_id=appointment_id
    ).first()


@require_http_methods(["GET"])
@require_admin_role
def booking_version(request: HttpRequest, appointment_id: str) -> HttpResponse:
    """The canonical facts about one booking, straight from Ayla."""

    tenant = request.tenant  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    if _own_booking(tenant.id, appointment_id) is None:
        return _error("not_found", "booking not found", 404)

    from apps.integrations.ayla.booking_client import (
        BookingAPIError,
        BookingUnavailableError,
        get_ayla_booking_client,
    )

    actor = external_user_id_for(bot_user)

    try:
        record = get_ayla_booking_client().get_appointment_version(
            external_user_id=actor,
            booking_id=str(appointment_id),
        )
    except BookingUnavailableError as exc:
        # No version means no action: the screen must not offer a button
        # it would have to aim blind.
        logger.warning("admin_api.booking_version.unavailable err=%s", exc)
        return _error("unavailable", "расписание не ответило — попробуйте ещё раз", 503)
    except BookingAPIError as exc:
        logger.warning("admin_api.booking_version.error err=%s", exc)
        return _error("unavailable", "не удалось прочитать запись", 503)

    return JsonResponse(
        {
            "id": record.id,
            "version": record.version,
            "status": record.status,
            "start_datetime": record.start_datetime,
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def complete_booking(request: HttpRequest, appointment_id: str) -> HttpResponse:
    """Close a visit on behalf of the calling administrator.

    Everything that hangs off closure — commission, payment capture, the
    review request, RFM — starts from Ayla's ``booking.completed``. None
    of it had ever run in production, because the only people entitled to
    close a visit had no way to reach the endpoint.
    """

    tenant = request.tenant  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return _error("bad_request", "invalid JSON body", 400)
    if not isinstance(body, dict):
        return _error("bad_request", "body must be a JSON object", 400)

    raw_version = body.get("expected_version")
    if isinstance(raw_version, bool) or not isinstance(raw_version, int):
        # Required, and never defaulted: see the module docstring. A
        # generated value would make the guard unfireable.
        return _error("bad_request", "expected_version is required", 400)
    if raw_version < 1:
        return _error("bad_request", "expected_version must be positive", 400)

    if _own_booking(tenant.id, appointment_id) is None:
        return _error("not_found", "booking not found", 404)

    from apps.integrations.ayla.salon_client import (
        SalonAPIError,
        SalonForbidden,
        SalonNotAllowed,
        SalonNotConfigured,
        SalonNotFound,
        SalonSlotTaken,
        SalonStaleVersion,
        SalonUnauthorized,
        SalonUnavailable,
        SalonValidationError,
        get_salon_client,
    )

    actor = external_user_id_for(bot_user)

    try:
        get_salon_client().complete_appointment(
            actor_external_id=actor,
            tenant_slug=tenant.slug,
            appointment_id=str(appointment_id),
            expected_version=raw_version,
        )
    except SalonValidationError as exc:
        return _outcome("blocked", str(exc), 400)
    except SalonNotConfigured as exc:
        logger.error("admin_api.complete_booking.not_configured err=%s", exc)
        return _outcome("blocked", "закрытие визита не настроено", 503)
    except SalonUnauthorized as exc:
        logger.error(
            "admin_api.complete_booking.upstream_unauthorized tenant=%s err=%s",
            tenant.id,
            exc,
        )
        return _outcome("blocked", "закрытие сейчас недоступно — обратитесь к поддержке", 503)
    except SalonForbidden as exc:
        logger.warning(
            "admin_api.complete_booking.forbidden actor=%s tenant=%s err=%s",
            actor,
            tenant.id,
            exc,
        )
        return _outcome("blocked", str(exc), 403)
    except SalonStaleVersion:
        # The guard fired: the booking changed after the operator looked.
        # Not an error on their part — send them back to a fresh read.
        return _outcome(
            "conflict",
            "запись изменилась — обновите день и попробуйте снова",
            409,
        )
    except SalonNotAllowed as exc:
        # Cancelled, or already closed. Settled, not contended.
        return _outcome("blocked", str(exc), 409)
    except SalonSlotTaken as exc:
        return _outcome("conflict", str(exc), 409)
    except SalonNotFound as exc:
        logger.warning(
            "admin_api.complete_booking.mirror_divergence appointment=%s err=%s",
            appointment_id,
            exc,
        )
        return _outcome("conflict", "запись не найдена в расписании — обновите день", 409)
    except SalonUnavailable as exc:
        # May have been applied. Never a failure — a second press on an
        # already-closed visit is refused, but the operator should be
        # told to look rather than to retry blindly.
        logger.warning("admin_api.complete_booking.unknown actor=%s err=%s", actor, exc)
        return _outcome(
            "pending",
            "расписание не ответило — обновите день, прежде чем повторять",
            504,
        )
    except SalonAPIError as exc:
        logger.warning("admin_api.complete_booking.error actor=%s err=%s", actor, exc)
        return _outcome("failed", str(exc), 502)

    logger.info(
        "admin_api.complete_booking.committed appointment=%s actor=%s tenant=%s",
        appointment_id,
        actor,
        tenant.id,
    )
    return _outcome("committed", "visit closed", 200, appointment_id=str(appointment_id))


__all__ = ["booking_version", "complete_booking", "reschedule_booking"]


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def reschedule_booking(request: HttpRequest, appointment_id: str) -> HttpResponse:
    """Move a booking to a new start, on behalf of the acting administrator.

    Shares the version rule with closure above, and needs it more: two
    people moving the same booking is the concrete accident
    ``expected_version`` was added for. The new start must be one the
    schedule offered — the screen picks it from the slots endpoint, and
    Ayla re-checks availability at commit regardless (UX contract §17:
    never silently shift a start).
    """

    tenant = request.tenant  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return _error("bad_request", "invalid JSON body", 400)
    if not isinstance(body, dict):
        return _error("bad_request", "body must be a JSON object", 400)

    raw_version = body.get("expected_version")
    if isinstance(raw_version, bool) or not isinstance(raw_version, int):
        return _error("bad_request", "expected_version is required", 400)
    if raw_version < 1:
        return _error("bad_request", "expected_version must be positive", 400)

    new_start = str(body.get("new_start_at") or "").strip()
    if not new_start:
        return _error("bad_request", "new_start_at is required", 400)

    if _own_booking(tenant.id, appointment_id) is None:
        return _error("not_found", "booking not found", 404)

    from apps.integrations.ayla.salon_client import (
        SalonAPIError,
        SalonForbidden,
        SalonNotAllowed,
        SalonNotConfigured,
        SalonNotFound,
        SalonSlotTaken,
        SalonStaleVersion,
        SalonUnauthorized,
        SalonUnavailable,
        SalonValidationError,
        get_salon_client,
    )

    actor = external_user_id_for(bot_user)

    try:
        get_salon_client().reschedule_appointment(
            actor_external_id=actor,
            tenant_slug=tenant.slug,
            appointment_id=str(appointment_id),
            new_start_datetime=new_start,
            expected_version=raw_version,
        )
    except SalonValidationError as exc:
        return _outcome("blocked", str(exc), 400)
    except SalonNotConfigured as exc:
        logger.error("admin_api.reschedule_booking.not_configured err=%s", exc)
        return _outcome("blocked", "перенос не настроен", 503)
    except SalonUnauthorized as exc:
        logger.error(
            "admin_api.reschedule_booking.upstream_unauthorized tenant=%s err=%s",
            tenant.id,
            exc,
        )
        return _outcome("blocked", "перенос сейчас недоступен — обратитесь к поддержке", 503)
    except SalonForbidden as exc:
        logger.warning(
            "admin_api.reschedule_booking.forbidden actor=%s tenant=%s err=%s",
            actor,
            tenant.id,
            exc,
        )
        return _outcome("blocked", str(exc), 403)
    except SalonStaleVersion:
        # Somebody moved it first. The operator is looking at a booking
        # that no longer exists in that shape — send them back to read.
        return _outcome(
            "conflict",
            "запись уже перенесли — обновите день и посмотрите заново",
            409,
        )
    except SalonSlotTaken:
        # Different fact, different instruction: the booking is as they
        # left it, the TIME went.
        return _outcome(
            "conflict",
            "это время успели занять — выберите другое",
            409,
        )
    except SalonNotAllowed as exc:
        return _outcome("blocked", str(exc), 409)
    except SalonNotFound as exc:
        logger.warning(
            "admin_api.reschedule_booking.mirror_divergence appointment=%s err=%s",
            appointment_id,
            exc,
        )
        return _outcome("conflict", "запись не найдена в расписании — обновите день", 409)
    except SalonUnavailable as exc:
        # May have been applied. A blind retry could move it twice.
        logger.warning("admin_api.reschedule_booking.unknown actor=%s err=%s", actor, exc)
        return _outcome(
            "pending",
            "расписание не ответило — обновите день, прежде чем повторять",
            504,
        )
    except SalonAPIError as exc:
        logger.warning("admin_api.reschedule_booking.error actor=%s err=%s", actor, exc)
        return _outcome("failed", str(exc), 502)

    logger.info(
        "admin_api.reschedule_booking.committed appointment=%s actor=%s tenant=%s",
        appointment_id,
        actor,
        tenant.id,
    )
    return _outcome("committed", "booking moved", 200, appointment_id=str(appointment_id))
