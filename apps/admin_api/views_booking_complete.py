"""Admin REST views — read a booking's canonical version, and close the visit.

``GET  /api/v1/admin/bookings/<appointment_id>/``
``POST /api/v1/admin/bookings/<appointment_id>/complete/``
``POST /api/v1/admin/bookings/<appointment_id>/no-show/``
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


def _error(
    slug: str,
    detail: str,
    status: int,
    *,
    hint: str | None = None,
) -> JsonResponse:
    """Отказ. ``detail`` — нам в журнал, ``hint`` — слова человеку.

    DRF-2453, тот же разрез, что у ``_outcome`` ниже. В конверте ОШИБКИ
    подсказка едет в ``details.hint`` — так её уже отдаёт
    ``views_staff_role.py:178`` и так её уже объявляет клиент (DRF-2273).
    Четвёртого конверта не заводим.
    """
    body: dict[str, Any] = {"error": slug, "detail": detail}
    if hint:
        body["details"] = {"hint": hint}
    return JsonResponse(body, status=status)


def _outcome(
    outcome: str,
    detail: str,
    status: int,
    *,
    hint: str | None = None,
    **extra: Any,
) -> JsonResponse:
    """Исход операции. ``detail`` — нам в журнал, ``hint`` — слова человеку.

    DRF-2453. Раньше в ``detail`` лежало и то и другое: согласованная
    русская фраза владельца, внутренний английский и ``str(exc)``. Экран
    печатал этот канал целиком — значит показывал человеку и внутреннее
    тоже; а перестать печатать было нельзя, не потеряв слова владельца.

    Разрез не новый: ``hint`` рядом с внутренней причиной уже отдаёт
    ``views_staff_role.py`` (``details={"hint": exc.hint}``), и клиент
    объявляет это поле с DRF-2273. Здесь та же пара в конверте исхода.

    Нет ``hint`` — экран скажет собственную согласованную фразу; выдумывать
    её на сервере не нужно и нельзя.
    """
    body: dict[str, Any] = {"outcome": outcome, "detail": detail, **extra}
    if hint:
        body["hint"] = hint
    return JsonResponse(body, status=status)


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
        return _error(
            "unavailable",
            "booking version unavailable upstream",
            503,
            hint="расписание не ответило — попробуйте ещё раз",
        )
    except BookingAPIError as exc:
        logger.warning("admin_api.booking_version.error err=%s", exc)
        return _error(
            "unavailable", "booking version read failed", 503, hint="не удалось прочитать запись"
        )

    return JsonResponse(
        {
            "id": record.id,
            "version": record.version,
            "status": record.status,
            "start_datetime": record.start_datetime,
        }
    )


#: What each visit-settling write says in its refusals. One mapping of
#: Ayla's answers for both, so «не пришёл» and «состоялся» can never drift
#: into telling the operator different things about the same situation.
_SETTLE_COPY = {
    "complete_appointment": {
        "log": "complete_booking",
        "not_configured": "закрытие визита не настроено",
        "unauthorized": "закрытие сейчас недоступно — обратитесь к поддержке",
        "committed": "visit closed",
    },
    "mark_no_show": {
        "log": "no_show_booking",
        "not_configured": "отметка неявки не настроена",
        "unauthorized": "отметка неявки сейчас недоступна — обратитесь к поддержке",
        "committed": "visit marked no-show",
    },
}


def _settle_visit(request: HttpRequest, appointment_id: str, *, write: str) -> HttpResponse:
    """Settle a visit through Ayla's state machine on behalf of the admin.

    ``write`` is the salon-client method: ``complete_appointment`` or
    ``mark_no_show``. The bot never sets a status itself — Ayla re-checks
    the transition on the locked row and the mirror follows its event.
    """

    copy = _SETTLE_COPY[write]
    log = copy["log"]
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
        getattr(get_salon_client(), write)(
            actor_external_id=actor,
            tenant_slug=tenant.slug,
            appointment_id=str(appointment_id),
            expected_version=raw_version,
        )
    except SalonValidationError as exc:
        return _outcome("blocked", str(exc), 400)
    except SalonNotConfigured as exc:
        logger.error("admin_api.%s.not_configured err=%s", log, exc)
        return _outcome(
            "blocked", "not configured for this write", 503, hint=copy["not_configured"]
        )
    except SalonUnauthorized as exc:
        logger.error(
            "admin_api.%s.upstream_unauthorized tenant=%s err=%s",
            log,
            tenant.id,
            exc,
        )
        return _outcome("blocked", "unauthorized for this write", 503, hint=copy["unauthorized"])
    except SalonForbidden as exc:
        logger.warning(
            "admin_api.%s.forbidden actor=%s tenant=%s err=%s",
            log,
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
            "version conflict: booking changed since it was read",
            409,
            hint="запись изменилась — обновите день и попробуйте снова",
        )
    except SalonNotAllowed as exc:
        # Cancelled, or already settled. Settled, not contended.
        return _outcome("blocked", str(exc), 409)
    except SalonSlotTaken as exc:
        return _outcome("conflict", str(exc), 409)
    except SalonNotFound as exc:
        logger.warning(
            "admin_api.%s.mirror_divergence appointment=%s err=%s",
            log,
            appointment_id,
            exc,
        )
        return _outcome(
            "conflict",
            "mirror diverged: appointment missing upstream",
            409,
            hint="запись не найдена в расписании — обновите день",
        )
    except SalonUnavailable as exc:
        # May have been applied. Never a failure — a second press on an
        # already-settled visit is refused, but the operator should be
        # told to look rather than to retry blindly.
        logger.warning("admin_api.%s.unknown actor=%s err=%s", log, actor, exc)
        return _outcome(
            "pending",
            "salon did not answer; the write may already have applied",
            504,
            hint="расписание не ответило — обновите день, прежде чем повторять",
        )
    except SalonAPIError as exc:
        logger.warning("admin_api.%s.error actor=%s err=%s", log, actor, exc)
        return _outcome("failed", str(exc), 502)

    logger.info(
        "admin_api.%s.committed appointment=%s actor=%s tenant=%s",
        log,
        appointment_id,
        actor,
        tenant.id,
    )
    return _outcome("committed", copy["committed"], 200, appointment_id=str(appointment_id))


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

    return _settle_visit(request, appointment_id, write="complete_appointment")


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def no_show_booking(request: HttpRequest, appointment_id: str) -> HttpResponse:
    """«Не пришёл» on behalf of the calling administrator (DRF-1851, OD-V1).

    Before this the day dialog offered «состоялся / перенести / отменить»,
    and a client who never came could only be cancelled — losing the fact
    that the slot was held. Same version rule and the same answers as
    closure; Ayla's state machine decides, the mirror follows its
    ``booking.cancelled`` + ``reason_code="user_no_show"`` event.
    """

    return _settle_visit(request, appointment_id, write="mark_no_show")


__all__ = ["booking_version", "complete_booking", "no_show_booking", "reschedule_booking"]


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
        return _outcome(
            "blocked",
            "reschedule write is not configured for this tenant",
            503,
            hint="перенос не настроен",
        )
    except SalonUnauthorized as exc:
        logger.error(
            "admin_api.reschedule_booking.upstream_unauthorized tenant=%s err=%s",
            tenant.id,
            exc,
        )
        return _outcome(
            "blocked",
            "salon rejected the reschedule call as unauthorized",
            503,
            hint="перенос сейчас недоступен — обратитесь к поддержке",
        )
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
            "version conflict on reschedule: booking already moved",
            409,
            hint="запись уже перенесли — обновите день и посмотрите заново",
        )
    except SalonSlotTaken:
        # Different fact, different instruction: the booking is as they
        # left it, the TIME went.
        return _outcome(
            "conflict",
            "slot conflict: target time taken upstream",
            409,
            hint="это время успели занять — выберите другое",
        )
    except SalonNotAllowed as exc:
        return _outcome("blocked", str(exc), 409)
    except SalonNotFound as exc:
        logger.warning(
            "admin_api.reschedule_booking.mirror_divergence appointment=%s err=%s",
            appointment_id,
            exc,
        )
        return _outcome(
            "conflict",
            "mirror diverged: appointment missing upstream",
            409,
            hint="запись не найдена в расписании — обновите день",
        )
    except SalonUnavailable as exc:
        # May have been applied. A blind retry could move it twice.
        logger.warning("admin_api.reschedule_booking.unknown actor=%s err=%s", actor, exc)
        return _outcome(
            "pending",
            "salon did not answer; the write may already have applied",
            504,
            hint="расписание не ответило — обновите день, прежде чем повторять",
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
