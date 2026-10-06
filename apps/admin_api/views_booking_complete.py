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

from apps.admin_api.auth import require_booking_desk
from apps.admin_api.services.visit_settle import own_booking as _own_booking
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


@require_http_methods(["GET"])
@require_booking_desk
def booking_version(request: HttpRequest, appointment_id: str) -> HttpResponse:
    """The canonical facts about one booking, straight from Ayla."""

    from apps.admin_api.services.visit_settle import VersionUnavailable, read_version

    tenant = request.tenant  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    # DRF-2784: the read lives in the service — the salon bot's chat shows
    # the operator the same version before its «Да, состоялся».
    record = read_version(tenant=tenant, bot_user=bot_user, appointment_id=appointment_id)
    if isinstance(record, VersionUnavailable):
        # No version means no action: the screen must not offer a button
        # it would have to aim blind.
        return _error(record.slug, record.detail, record.status, hint=record.hint)

    return JsonResponse(
        {
            "id": record.id,
            "version": record.version,
            "status": record.status,
            "start_datetime": record.start_datetime,
        }
    )


def _settle_visit(request: HttpRequest, appointment_id: str, *, write: str) -> HttpResponse:
    """Settle a visit through Ayla's state machine on behalf of the admin.

    ``write`` is the salon-client method: ``complete_appointment`` or
    ``mark_no_show``. The bot never sets a status itself — Ayla re-checks
    the transition on the locked row and the mirror follows its event.
    """

    from apps.admin_api.services.visit_settle import settle_visit

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

    # DRF-2784: Ayla's answers are mapped once, in the service, for both
    # doors (this view and the salon bot's chat).
    settled = settle_visit(
        tenant=tenant,
        bot_user=bot_user,
        appointment_id=appointment_id,
        expected_version=raw_version,
        write=write,
    )
    if settled.outcome == "not_found":
        return _error("not_found", settled.detail, settled.status)
    if settled.outcome == "committed":
        return _outcome(
            "committed", settled.detail, settled.status, appointment_id=str(appointment_id)
        )
    return _outcome(settled.outcome, settled.detail, settled.status, hint=settled.hint)


@csrf_exempt
@require_http_methods(["POST"])
@require_booking_desk
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
@require_booking_desk
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
@require_booking_desk
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
