"""Экран 07 «Профиль мастера» — контракт для Mini App (DRF-1814, часть A из трёх).

Три ручки, все под initData самого мастера (``require_master_init_data``) и с
id профиля из зеркала (``_catalog_profile_required`` в ``views``):

* ``GET  profile/card``                     — что рисует экран 07;
* ``GET/POST profile/portfolio``            — работы: список / загрузка;
* ``DELETE profile/portfolio/<item_id>``    — удалить одну.

### Владелец полей — каталог, экран своих чисел не держит

Имя, «о себе», фото и ``limits`` приходят из ``GET internal/specialists/{id}/
profile/`` (каталог #471, DRF-1960): те же константы, по которым каталог
проверяет запись. До этого Mini App держал ``MASTER_PROFILE_BIO_MAX = 280`` при
лимите каталога 500 — два числа на один предмет (`GAP_MAP` §3 «лимиты —
данные контракта»). Зеркало ``CatalogMaster.bio/photo_url`` здесь не читается:
после M20/M21 оно — копия, и копия может отставать на синк.

### «Принимает сегодня» — только из реального слота (§3 карты разрывов)

На макете 6.4 бейдж стоит до настройки расписания — неясность №8. Здесь он
рисуется ровно тогда, когда каталог отдал ≥ 1 свободный слот **на сегодня**
для одной из услуг мастера (``GET internal/specialists/{id}/slots/`` — тот
же запрос, что делает клиент). Слотов нет, услуги нет, канон не читается —
``false`` с именем причины; ни одна из причин не рисует бейдж. Слоты
требуют ``service_id`` (Ayla отвечает 400 без него), поэтому берётся
первая продаваемая услуга, у которой есть мост в каталог; без такой услуги
и слотов быть не может — ``no_service``, а не запрос «на всякий случай».

### Чипы — категории выбранных шаблонов, не слаг и не «специализация»

``CatalogMaster.specialization`` не пишет никто (``upserter.py``), а
``_derive_category`` в ``services/catalog.py`` угадывает категорию по
первому слову слага. Источник, который знает шаблон, — состояние выбора
услуг в каталоге (``GET …/services/selection/``, DRF-1912: ``category_name``
— от канона). Отсюда чипы: уникальные имена категорий активных выбранных
услуг, в порядке ответа. Выбор недоступен (салонный мастер — 409) —
пустой список с причиной, а не догадка.

### Портфолио — прокси, субъект по построению

Маршруты не несут ``specialist_id``: он берётся из зеркала мастера, чья
initData подписала запрос (``catalog_specialist_id(request.master)``), как у
``PATCH profile``. Чужой профиль в пути невозможен не потому, что проверен,
а потому, что параметра нет; узел ``test_subject_is_the_signed_master_by_
construction`` держит ровно это. Лимит 10 — каталога: 11-е фото он отклоняет
400 ``portfolio_limit_exceeded``, и ответ едет наружу по имени через
``_profile_refusal``; бот своего счётчика не ведёт.
"""

from __future__ import annotations

import logging
from datetime import date as date_cls
from typing import Any
from zoneinfo import ZoneInfo

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.catalog.models import CatalogMaster, MasterService
from apps.catalog.specialist_ref import catalog_specialist_id
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import (
    BookingAPIError,
    BookingBadRequestError,
    BookingUnavailableError,
    get_ayla_booking_client,
)
from apps.integrations.ayla.user_proxy import external_user_id_for
from apps.master_api.auth import require_master_init_data

logger = logging.getLogger(__name__)

#: Почему бейджа «Принимает сегодня» нет — слова для лога и для экрана.
ACCEPTS_TODAY_NO_SERVICE = "no_service"
ACCEPTS_TODAY_NO_SLOTS = "no_slots"
ACCEPTS_TODAY_CATALOG_UNAVAILABLE = "catalog_unavailable"
ACCEPTS_TODAY_CATALOG_REFUSED = "catalog_refused"

#: Почему чипов нет.
CATEGORIES_SELECTION_UNAVAILABLE = "selection_unavailable"


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


def _catalog_unavailable() -> JsonResponse:
    return _error("catalog_unavailable", "Каталог сейчас недоступен — попробуйте позже.", 503)


def _tenant_tz(master: CatalogMaster) -> ZoneInfo:
    return ZoneInfo(getattr(master.tenant, "timezone", None) or "Europe/Moscow")


def _bridged_service_id(master: CatalogMaster) -> str | None:
    """Ayla-id первой активной услуги мастера с мостом в каталог — для запроса слотов."""

    edge = (
        MasterService.all_tenants.filter(
            tenant_id=master.tenant_id,
            master=master,
            service__is_active=True,
            service__ayla_service_id__isnull=False,
        )
        .select_related("service")
        .order_by("service__name")
        .first()
    )
    if edge is None:
        return None
    return str(edge.service.ayla_service_id)


def accepts_today(master: CatalogMaster, *, client: Any, today: date_cls | None = None) -> dict:
    """``{"value": bool, "reason": str | None}`` — бейдж только из реального слота.

    Читается отдельно от профиля и **никогда не роняет** карточку: канон
    слотов недоступен — ``false`` + причина, экран рисует профиль без бейджа.
    """

    service_id = _bridged_service_id(master)
    if service_id is None:
        return {"value": False, "reason": ACCEPTS_TODAY_NO_SERVICE}
    day = today or timezone.now().astimezone(_tenant_tz(master)).date()
    try:
        slots = client.get_available_times(
            specialist_id=catalog_specialist_id(master),
            date=day.isoformat(),
            service_id=service_id,
        )
    except BookingBadRequestError as exc:
        logger.info("master_api.profile_card.slots_refused master=%s code=%s", master.pk, exc.code)
        return {"value": False, "reason": ACCEPTS_TODAY_CATALOG_REFUSED}
    except BookingAPIError as exc:
        logger.warning("master_api.profile_card.slots_unavailable master=%s err=%s", master.pk, exc)
        return {"value": False, "reason": ACCEPTS_TODAY_CATALOG_UNAVAILABLE}
    if slots:
        return {"value": True, "reason": None}
    return {"value": False, "reason": ACCEPTS_TODAY_NO_SLOTS}


def selected_categories(
    master: CatalogMaster, *, client: Any, external_user_id: str
) -> dict[str, Any]:
    """``{"items": [..], "reason": str | None}`` — категории выбранных шаблонов."""

    try:
        state = client.get_service_selection(
            specialist_id=catalog_specialist_id(master), external_user_id=external_user_id
        )
    except BookingAPIError as exc:
        logger.info(
            "master_api.profile_card.selection_unavailable master=%s err=%s", master.pk, exc
        )
        return {"items": [], "reason": CATEGORIES_SELECTION_UNAVAILABLE}
    seen: list[str] = []
    for row in state.get("services") or []:
        if not isinstance(row, dict) or not row.get("is_active"):
            continue
        name = (row.get("category_name") or "").strip()
        if name and name not in seen:
            seen.append(name)
    return {"items": seen, "reason": None}


def _profile_refusal(exc: BookingBadRequestError) -> HttpResponse:
    # Импорт на месте: ``views`` тянет весь master_api при загрузке, а этот
    # модуль нужен ему через urls — иначе кольцо.
    from apps.master_api.views import _profile_refusal as refusal

    return refusal(exc)


def _catalog_profile_required(view_func: Any) -> Any:
    from apps.master_api.views import _catalog_profile_required as required

    return required(view_func)


@require_http_methods(["GET"])
@require_master_init_data
@_catalog_profile_required
def profile_card(request: HttpRequest) -> HttpResponse:
    """Всё, что рисует экран 07, одним ответом; владелец полей — каталог."""

    master: CatalogMaster = request.master  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    actor = external_user_id_for(bot_user)
    client = get_ayla_booking_client()

    try:
        state = client.get_specialist_profile(
            specialist_id=catalog_specialist_id(master), external_user_id=actor
        )
    except BookingBadRequestError as exc:
        return _profile_refusal(exc)
    except BookingUnavailableError:
        return _catalog_unavailable()

    badge = accepts_today(master, client=client)
    chips = selected_categories(master, client=client, external_user_id=actor)

    return JsonResponse(
        {
            "master": {
                "id": str(master.id),
                "name": state.get("display_name") or master.name,
                "bio": state.get("bio") or "",
                "photo_url": state.get("avatar_url") or "",
            },
            "limits": dict(state.get("limits") or {}),
            "portfolio": dict(state.get("portfolio") or {}),
            "accepts_today": badge["value"],
            "accepts_today_reason": badge["reason"],
            "categories": chips["items"],
            "categories_reason": chips["reason"],
        }
    )


@require_http_methods(["GET", "POST"])
@require_master_init_data
@_catalog_profile_required
def profile_portfolio(request: HttpRequest) -> HttpResponse:
    """Список работ / загрузка одной — прокси в каталог, лимит его."""

    master: CatalogMaster = request.master  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    actor = external_user_id_for(bot_user)
    client = get_ayla_booking_client()
    specialist_id = catalog_specialist_id(master)

    try:
        if request.method == "GET":
            return JsonResponse(
                client.list_specialist_portfolio(
                    specialist_id=specialist_id, external_user_id=actor
                )
            )
        image = request.FILES.get("image")
        if image is None:
            return _error("bad_request", "multipart field 'image' is required", 400)
        item = client.upload_specialist_portfolio_item(
            specialist_id=specialist_id,
            external_user_id=actor,
            filename=image.name or "photo",
            content=image.read(),
            content_type=image.content_type or "application/octet-stream",
        )
    except BookingBadRequestError as exc:
        return _profile_refusal(exc)
    except BookingUnavailableError:
        return _catalog_unavailable()

    logger.info(
        "master_api.profile_portfolio.uploaded master=%s item=%s", master.pk, item.get("id")
    )
    return JsonResponse(item, status=201)


@require_http_methods(["DELETE"])
@require_master_init_data
@_catalog_profile_required
def profile_portfolio_item(request: HttpRequest, item_id: str) -> HttpResponse:
    master: CatalogMaster = request.master  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    actor = external_user_id_for(bot_user)

    try:
        state = get_ayla_booking_client().delete_specialist_portfolio_item(
            specialist_id=catalog_specialist_id(master),
            external_user_id=actor,
            item_id=str(item_id),
        )
    except BookingBadRequestError as exc:
        return _profile_refusal(exc)
    except BookingUnavailableError:
        return _catalog_unavailable()

    logger.info("master_api.profile_portfolio.deleted master=%s item=%s", master.pk, item_id)
    return JsonResponse(state)
