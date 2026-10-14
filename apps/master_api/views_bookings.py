"""Ручки мастера для записей (DRF-2154, М-2 эпика DRF-2150).

``GET  /api/v1/master/bookings/<uuid>``   — детали с временным состоянием (DRF-1185)
``POST /api/v1/master/bookings``          — создать запись (DRF-1184)
``GET  /api/v1/master/booking-slots``     — окна под услугу на день
``GET  /api/v1/master/customers?q=``      — поиск клиента: имя + инициал + дата
                                            последнего визита (в views.py; поиск
                                            здесь — :func:`search_customers`)

Субъект — мастер из initData (``request.master``); мастер = актор. Ни в
путях, ни в телах нет ``master_id``: чужую запись нельзя даже назвать.
Соло-мастер — тот же код: он мастер своего салона.

Создание, слоты и поиск — через :mod:`apps.admin_api.services.booking`, тем
же кодом, что у салонной стойки; ответы §18 те же, кроме двух форм листа:
время занято → 409 ``slot_taken`` с ближайшими ``alternatives`` (DRF-1184:
«другие варианты», форма не сбрасывается), ответ не пришёл → 202
``result_pending`` с ``idempotency_key`` («Проверяем результат. Не создавайте
запись повторно»).

**Телефон клиента** — только вход для нового гостя (DRF-1184: «имя +
телефон»; Ayla без него не заводит клиента); в ответах его нет нигде, в
журнал не пишется. Поиск по номеру закрыт (400) до слова владельца.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone as dj_timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.admin_api.services.booking import (
    Outcome,
    Refusal,
    bookable_service,
    bookable_starts,
    create_appointment_as,
    search_customers_as,
    slot_payload,
)
from apps.catalog.models import CatalogMaster
from apps.catalog.specialist_ref import CatalogSpecialistUnresolved, catalog_specialist_id
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import (
    BookingBadRequestError,
    BookingUnavailableError,
    get_ayla_booking_client,
)
from apps.integrations.ayla.user_proxy import external_user_id_for
from apps.master_api.auth import require_master_init_data
from apps.master_api.services.bookings import (
    ALTERNATIVES_LIMIT,
    booking_detail,
    enrich_customer_rows,
    looks_like_phone,
    own_booking,
)
from apps.tenancy.timezones import salon_zone

logger = logging.getLogger(__name__)

MAX_NAME_LEN = 150
MAX_PHONE_LEN = 20
MAX_QUERY_LEN = 100


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


def _outcome(result: Outcome, **extra: Any) -> JsonResponse:
    """Исход §18 наружу — ``outcome`` полем, не выводом из кода ответа."""

    return JsonResponse(
        {"outcome": result.outcome, "detail": result.detail, **result.extra, **extra},
        status=result.status,
    )


# ─── GET bookings/<id> ──────────────────────────────────────────────────────


@require_http_methods(["GET"])
@require_master_init_data
def booking_detail_view(request: HttpRequest, appointment_id: uuid.UUID) -> HttpResponse:
    """Своя запись с состоянием по часам сервера; чужая → 404 тем же телом."""

    master: CatalogMaster = request.master  # type: ignore[attr-defined]
    detail = booking_detail(master, appointment_id, now=dj_timezone.now())
    if detail is None:
        return _error("not_found", "booking not found", 404)
    return JsonResponse(detail.to_dict(salon_zone(master.tenant)))


# ─── POST bookings/<id>/action ───────────────────────────────────────────────

MASTER_BOOKING_ACTIONS = frozenset({"acknowledge", "cancel", "complete", "no-show", "reschedule"})


@csrf_exempt
@require_http_methods(["POST"])
@require_master_init_data
def booking_action(request: HttpRequest, appointment_id: uuid.UUID) -> HttpResponse:
    """Действие мастера над СВОЕЙ записью через canonical specialist endpoint.

    DRF-2943 поверх DRF-2785: Mini App не получает direct internal bearer.
    expected_version приходит из detail, который оператор реально видел;
    backend не заменяет её свежей версией перед write.
    """

    master: CatalogMaster = request.master  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    if own_booking(master, appointment_id) is None:
        return _error("not_found", "booking not found", 404)

    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return _error("bad_request", "invalid JSON body", 400)
    if not isinstance(body, dict):
        return _error("bad_request", "body must be a JSON object", 400)

    action = body.get("action")
    if not isinstance(action, str) or action not in MASTER_BOOKING_ACTIONS:
        return _error("bad_request", "unsupported action", 400)

    expected_version = body.get("expected_version")
    if action != "cancel":
        if (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 1
        ):
            return JsonResponse(
                {"outcome": "conflict", "reason_code": "version_unknown"},
                status=409,
            )
    elif expected_version is not None and (
        isinstance(expected_version, bool)
        or not isinstance(expected_version, int)
        or expected_version < 1
    ):
        return _error("bad_request", "expected_version must be a positive integer", 400)

    new_start_datetime = body.get("new_start_datetime")
    if action == "reschedule":
        if not isinstance(new_start_datetime, str) or not new_start_datetime.strip():
            return _error("bad_request", "new_start_datetime is required", 400)
        new_start_datetime = new_start_datetime.strip()
    else:
        new_start_datetime = None

    reason = body.get("reason")
    if reason is not None and not isinstance(reason, str):
        return _error("bad_request", "reason must be a string", 400)

    try:
        result = get_ayla_booking_client().act_as_specialist(
            external_user_id=external_user_id_for(bot_user),
            specialist_id=catalog_specialist_id(master),
            appointment_id=str(appointment_id),
            action=action,
            expected_version=expected_version,
            reason=(reason.strip() if isinstance(reason, str) and reason.strip() else None),
            new_start_datetime=new_start_datetime,
        )
    except CatalogSpecialistUnresolved:
        logger.warning("master_api.booking_action.no_specialist_id master=%s", master.pk)
        return JsonResponse(
            {"outcome": "pending", "reason_code": "result_pending"},
            status=202,
        )
    except BookingUnavailableError:
        logger.warning(
            "master_api.booking_action.unknown_result action=%s appointment=%s",
            action,
            appointment_id,
        )
        return JsonResponse(
            {"outcome": "pending", "reason_code": "result_pending"},
            status=202,
        )
    except BookingBadRequestError as exc:
        code = str(getattr(exc, "code", "") or "")
        status = int(getattr(exc, "status_code", 400) or 400)
        if status == 404:
            return _error("not_found", "booking not found", 404)
        if status == 403:
            return _error("forbidden", "action unavailable", 403)
        if code == "STALE_VERSION":
            return JsonResponse(
                {"outcome": "conflict", "reason_code": "stale_version"},
                status=409,
            )
        if code in {
            "INVALID_STATUS",
            "APPOINTMENT_TERMINAL",
            "CANCELLATION_NOT_ALLOWED",
        }:
            return JsonResponse(
                {"outcome": "conflict", "reason_code": "state_changed"},
                status=409,
            )
        logger.info(
            "master_api.booking_action.refused action=%s appointment=%s status=%s code=%s",
            action,
            appointment_id,
            status,
            code,
        )
        return JsonResponse(
            {"outcome": "blocked", "reason_code": "action_unavailable"},
            status=400,
        )

    return JsonResponse(
        {
            "outcome": "committed",
            "appointment_id": result.appointment_id,
            "status": result.status,
            "version": result.version,
            "start_at": result.start_at,
        }
    )


# ─── POST bookings ──────────────────────────────────────────────────────────


def _alternatives(
    master: CatalogMaster, service, start_at: str
) -> tuple[list[dict[str, Any]] | None, bool]:
    """Ближайшие окна того дня, кроме занятого. ``(None, True)`` — слоты недоступны.

    Второй вызов после отказа «занято» — потому что Ayla в 409 других
    вариантов не шлёт. Недоступность слотов не превращает 409 в 503: форма
    остаётся, человек выберет время сам.
    """

    tz = salon_zone(master.tenant)
    try:
        when = datetime.fromisoformat(start_at)
    except ValueError:
        return None, True
    if when.tzinfo is None:
        # Без смещения — день салона, не день хоста.
        when = when.replace(tzinfo=tz)
    day = when.astimezone(tz).date()
    slots = bookable_starts(
        master=master,
        service=service,
        day=day,
        log="master_api.create_booking.alternatives",
        journal=logger,
    )
    if isinstance(slots, Refusal):
        return None, True
    out = [slot_payload(s) for s in slots]
    out = [s for s in out if s.get("start_at") != start_at][:ALTERNATIVES_LIMIT]
    return out, False


@csrf_exempt
@require_http_methods(["POST"])
@require_master_init_data
def create_booking(request: HttpRequest) -> HttpResponse:
    """Записать клиента к себе. Тело: ``{client_id | client_name+client_phone, service_id, start_at}``."""

    master: CatalogMaster = request.master  # type: ignore[attr-defined]
    tenant = request.tenant  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return _error("bad_request", "invalid JSON body", 400)
    if not isinstance(body, dict):
        return _error("bad_request", "body must be a JSON object", 400)

    # DRF-2666: свободный текст из тела хранится или уходит наружу — нестроковое
    # значение получает отказ этой двери, а не становится текстом "{'a': 1}".
    for field in ("client_name", "client_phone", "idempotency_key"):
        if body.get(field) is not None and not isinstance(body.get(field), str):
            return _error("bad_request", f"{field} must be a string", 400)
    service_id = str(body.get("service_id") or "").strip()
    start_at = str(body.get("start_at") or "").strip()
    idempotency_key = str(body.get("idempotency_key") or "").strip()
    missing = [n for n, v in (("service_id", service_id), ("start_at", start_at)) if not v]
    if missing:
        return _error("bad_request", f"required: {', '.join(missing)}", 400)

    client_id = str(body.get("client_id") or "").strip() or None
    client_name = str(body.get("client_name") or "").strip() or None
    client_phone = str(body.get("client_phone") or "").strip() or None
    if bool(client_id) == bool(client_name):
        return _error("bad_request", "provide exactly one of client_id or client_name", 400)
    if client_name:
        if not client_phone:
            return _error("bad_request", "a new guest needs a name and a phone", 400)
        if len(client_name) > MAX_NAME_LEN or len(client_phone) > MAX_PHONE_LEN:
            return _error("bad_request", "name or phone too long", 400)

    service = bookable_service(tenant.id, service_id)
    if isinstance(service, Refusal):
        return _error(service.slug, service.detail, service.status)

    # Повтор отправки обязан повторить ключ, иначе повтор — вторая запись.
    if not idempotency_key:
        idempotency_key = str(uuid.uuid4())

    result = create_appointment_as(
        actor=external_user_id_for(bot_user),
        tenant=tenant,
        master=master,
        service=service,
        start_at=start_at,
        idempotency_key=idempotency_key,
        client_id=client_id,
        client_name=client_name,
        client_phone=client_phone,
        log="master_api.create_booking",
        journal=logger,
    )

    if result.outcome == "conflict" and result.status == 409:
        # DRF-1184: «Это время занято» — с другими вариантами, черновик цел.
        alternatives, unavailable = _alternatives(master, service, start_at)
        return _outcome(
            result,
            reason_code="slot_taken",
            alternatives=alternatives,
            alternatives_unavailable=unavailable,
        )
    if result.outcome == "pending":
        # «Проверяем результат. Не создавайте запись повторно» — 202, не
        # ошибка: запись могла лечь, ключ вернётся с повтором.
        return JsonResponse(
            {
                "outcome": "pending",
                "reason_code": "result_pending",
                "detail": result.detail,
                **result.extra,
            },
            status=202,
        )
    return _outcome(result)


# ─── GET booking-slots ──────────────────────────────────────────────────────


@require_http_methods(["GET"])
@require_master_init_data
def booking_slots(request: HttpRequest) -> HttpResponse:
    """Окна своего дня под одну услугу. ``service_id`` обязателен (§12)."""

    master: CatalogMaster = request.master  # type: ignore[attr-defined]
    tenant = request.tenant  # type: ignore[attr-defined]

    service_id = (request.GET.get("service_id") or "").strip()
    raw_date = (request.GET.get("date") or "").strip()
    missing = [n for n, v in (("service_id", service_id), ("date", raw_date)) if not v]
    if missing:
        return _error("bad_request", f"required: {', '.join(missing)}", 400)
    try:
        day = datetime.strptime(raw_date, "%Y-%m-%d").date()
    except ValueError:
        return _error("bad_request", "date must be YYYY-MM-DD", 400)

    service = bookable_service(
        tenant.id, service_id, log="master_api.booking_slots", journal=logger
    )
    if isinstance(service, Refusal):
        return _error(service.slug, service.detail, service.status)

    slots = bookable_starts(
        master=master, service=service, day=day, log="master_api.booking_slots", journal=logger
    )
    if isinstance(slots, Refusal):
        return _error(slots.slug, slots.detail, slots.status)

    return JsonResponse(
        {
            "date": day.isoformat(),
            "timezone": str(salon_zone(tenant)),
            "service_id": str(service.id),
            "duration_min": service.duration_min,
            "slots": [slot_payload(s) for s in slots],
        }
    )


# ─── GET customers?q= ───────────────────────────────────────────────────────


def search_customers(request: HttpRequest, query: str) -> HttpResponse:
    """Поиск клиента для записи: «Анна П. · была 12.05» / «· новый клиент».

    Вызывается из ``views.customers_list`` при наличии ``q`` — один маршрут
    ``customers`` на ростер и поиск. Телефон — ни на входе, ни на выходе.
    """

    master: CatalogMaster = request.master  # type: ignore[attr-defined]
    tenant = request.tenant  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    if len(query) > MAX_QUERY_LEN:
        return _error("bad_request", "query too long", 400)
    if looks_like_phone(query):
        # Закрыто до слова владельца: DRF-1039 не оставляет исключения
        # частичному номеру, а поиск по номеру — способ узнать, чей он.
        return _error("phone_search_closed", "search by name, not by phone", 400)

    rows = search_customers_as(
        actor=external_user_id_for(bot_user),
        tenant=tenant,
        query=query,
        log="master_api.search_customers",
        journal=logger,
    )
    if isinstance(rows, Refusal):
        return _error(rows.slug, rows.detail, rows.status)

    results = enrich_customer_rows(master, rows)
    logger.info("master_api.search_customers master=%s hits=%d", master.id, len(results))
    return JsonResponse({"results": results})


__all__ = [
    "booking_action",
    "booking_detail_view",
    "booking_slots",
    "create_booking",
    "search_customers",
]
