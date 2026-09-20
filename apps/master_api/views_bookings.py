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
from apps.identity.models import BotUser
from apps.integrations.ayla.user_proxy import external_user_id_for
from apps.master_api.auth import require_master_init_data
from apps.master_api.services.bookings import (
    ALTERNATIVES_LIMIT,
    booking_detail,
    enrich_customer_rows,
    looks_like_phone,
)
from apps.master_api.services.dashboard import get_tenant_tz

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
    return JsonResponse(detail.to_dict(get_tenant_tz(master.tenant)))


# ─── POST bookings ──────────────────────────────────────────────────────────


def _alternatives(
    master: CatalogMaster, service, start_at: str
) -> tuple[list[dict[str, Any]] | None, bool]:
    """Ближайшие окна того дня, кроме занятого. ``(None, True)`` — слоты недоступны.

    Второй вызов после отказа «занято» — потому что Ayla в 409 других
    вариантов не шлёт. Недоступность слотов не превращает 409 в 503: форма
    остаётся, человек выберет время сам.
    """

    tz = get_tenant_tz(master.tenant)
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
            "timezone": str(get_tenant_tz(tenant)),
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


__all__ = ["booking_detail_view", "booking_slots", "create_booking", "search_customers"]
