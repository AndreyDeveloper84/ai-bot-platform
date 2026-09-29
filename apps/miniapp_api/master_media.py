"""Фото мастера и работы портфолио — через бот, а не адресом хранилища (DRF-2539).

Каталог отдавал ``FieldFile.url``:
``http://minio:9000/beautygo-media/…?AWSAccessKeyId=…&Signature=…&Expires=…``.
Хост внутренний (телефон его не видит), подпись живёт час (зеркало хранит её
до синка), в адресе у каждого клиента — имя ключа хранилища. Решение
владельца 29.09 — вариант 3, «отдача через наш бэкенд»: каталог отдаёт байты
боту (``booking_client.specialist_media_file``), бот — Mini App.

# Адрес наружу — наш, относительный, с версией

:func:`master_photo_path` и :func:`portfolio_image_path` — ЕДИНСТВЕННЫЙ способ
отдать адрес фото мастера на провод: сырое значение каталога остаётся
внутри (зеркало ``CatalogMaster.photo_url``, состояние профиля). Версия —
хеш ПУТИ объекта без строки запроса: подпись меняется при каждом синке, и
версия по полному адресу сбивала бы кэш без смены фото; новое фото —
новый объект (``file_overwrite=False``), значит новый путь и новая версия.

Сторож — ``apps/miniapp_api/tests/test_master_media_wire_2539.py``: на всех
местах сериализации фото мастера — эти функции, и на проводе нет ни
внутреннего хоста, ни подписи.

# Кто может взять байты

Любая подлинная сессия Mini App (initData любого нашего бота): клиент на
витрине, мастер у себя, администратор в «Команде». Фото мастера — его
публичное лицо. Личность НЕ ищется и НЕ создаётся: ``require_init_data``
лениво заводит клиента в тенанте клиентского бота, и мастер, открывший
свою ленту, получил бы клиентскую строку за показ фото.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Callable
from functools import wraps
from typing import Any
from urllib.parse import urlsplit

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.catalog.specialist_ref import CatalogSpecialistUnresolved, catalog_specialist_id
from apps.integrations.ayla.booking_client import (
    BookingBadRequestError,
    BookingRateLimitedError,
    BookingUnavailableError,
    get_ayla_booking_client,
)
from apps.marketplace.discovery import master_for_media
from apps.miniapp_api.dev_bypass import try_dev_bypass
from apps.miniapp_api.per_person_quota import WINDOW_SECONDS, over_quota, rate_limited
from apps.miniapp_api.transport_refusal import GUARD_ATTR, verify_request_init_data

logger = logging.getLogger(__name__)

#: Префикс ручек ниже на проводе (``config/urls.py``: ``api/v1/customer/``).
MEDIA_PREFIX = "/api/v1/customer/media/masters"

#: Отдаём только картинки — тот же перечень, что у снимка дневника (DRF-2455):
#: объект, объявленный ``text/html`` или ``image/svg+xml``, исполнился бы в
#: источнике Mini App вместе с initData.
IMAGE_TYPES_SHOWN = frozenset({"image/jpeg", "image/png", "image/webp"})

#: DRF-2618 — квота на человека: картинок в минуту, до похода в каталог.
#:
#: Выведена, а не взята с потолка. Самый тяжёлый экран клиента — витрина
#: салона: ``masters_list`` отдаёт всех продаваемых мастеров без страниц,
#: на пилоте 33–36 фото. Профиль мастера — фото + до 10 работ портфолио
#: (лимит каталога) = 11. Минута честного пользования: витрина + три
#: профиля = 36 + 3 × 11 = 69. Администратор: «Команда» — до 50 на страницу
#: (``admin_api.views.MAX_LIST_LIMIT``) + карточка мастера = 51. Повтор в
#: пределах 5 минут бесплатен (``Cache-Control: private, max-age=300`` и
#: аренда Mini App). 120 — это ~1,7× честного пика: листающий человек не
#: упрётся, зациклившийся клиент остановится на двух в секунду.
#:
#: **Предел:** квота каталога — 600/мин на ВЕСЬ бот (один адрес). Пять
#: одновременных честных пиков выберут её и так; квота на человека лечит
#: одиночку, не толпу.
MEDIA_PER_PERSON_PER_MINUTE = 120
MEDIA_QUOTA_WINDOW_SECONDS = WINDOW_SECONDS


def _version(raw: str) -> str:
    return hashlib.sha1(urlsplit(raw).path.encode("utf-8")).hexdigest()[:12]  # noqa: S324 — не защита, ключ кэша


def master_photo_path(master_id: object, raw: str | None) -> str:
    """Адрес фото мастера на провод: наш путь с версией, или ``""`` — фото нет."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    return f"{MEDIA_PREFIX}/{master_id}/photo?v={_version(raw)}"


def portfolio_image_path(master_id: object, item_id: object, raw: str | None) -> str:
    """Адрес работы портфолио на провод — наш путь с версией, или ``""``."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    return f"{MEDIA_PREFIX}/{master_id}/portfolio/{item_id}/image?v={_version(raw)}"


def outward_portfolio_item(master_id: object, item: dict) -> dict:
    """Элемент портфолио каталога → тот же элемент с нашим адресом картинки."""
    out = dict(item)
    if "image_url" in out:
        out["image_url"] = portfolio_image_path(master_id, out.get("id"), out.get("image_url"))
    return out


def outward_portfolio(master_id: object, body: dict) -> dict:
    """Ответ портфолио каталога → тот же ответ, адреса картинок — наши."""
    out = dict(body)
    if isinstance(out.get("items"), list):
        out["items"] = [
            outward_portfolio_item(master_id, it) if isinstance(it, dict) else it
            for it in out["items"]
        ]
    return out


def _over_quota(identity: str) -> bool:
    """Сверх квоты ли этот запрос человека — общий тормоз ``per_person_quota``."""
    return over_quota("master_media", identity, per_minute=MEDIA_PER_PERSON_PER_MINUTE)


def _rate_limited() -> JsonResponse:
    return rate_limited("media_rate_limited", "too many images, retry in a minute")


def _absent() -> JsonResponse:
    return JsonResponse({"error": "not_found", "detail": "photo not found"}, status=404)


def _serve(master_id: str, item_id: str | None) -> HttpResponse:
    try:
        uuid.UUID(str(master_id))
        if item_id is not None:
            uuid.UUID(str(item_id))
    except (ValueError, AttributeError, TypeError):
        return _absent()
    master = master_for_media(master_id)
    if master is None:
        return _absent()
    try:
        photo = get_ayla_booking_client().specialist_media_file(
            specialist_id=catalog_specialist_id(master),
            item_id=str(item_id) if item_id is not None else None,
        )
    except CatalogSpecialistUnresolved:
        return _absent()
    except BookingBadRequestError:
        return _absent()
    except BookingRateLimitedError:
        # Каталог выбрал свою квоту (600/мин на бот) — это не «каталог упал».
        logger.warning("miniapp_api.master_media.catalog_rate_limited master=%s", master_id)
        return _rate_limited()
    except BookingUnavailableError:
        logger.warning("miniapp_api.master_media.catalog_unavailable master=%s", master_id)
        return JsonResponse(
            {"error": "ayla_unavailable", "detail": "catalog unavailable"}, status=502
        )
    if photo is None:
        return _absent()
    content, content_type = photo
    safe_type = content_type.split(";")[0].strip().lower()
    if safe_type not in IMAGE_TYPES_SHOWN:
        safe_type = "application/octet-stream"
    response = HttpResponse(content, content_type=safe_type)
    response["Content-Disposition"] = "inline"
    response["X-Content-Type-Options"] = "nosniff"
    # Приватно: ответ зависит от initData. Смену фото сбивает версия в адресе.
    response["Cache-Control"] = "private, max-age=300"
    return response


def require_signed_session(view_func: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """Пропуск — подлинная подпись initData любого нашего бота, и только она.

    Не ``require_init_data``: тот ищет и лениво ЗАВОДИТ клиента (см. модуль).
    Отказ — тот же ``401 no_init_data``, что у всех поверхностей (DRF-1893);
    метка :data:`GUARD_ATTR` — для переписи маршрутов
    (``test_transport_refusal_1893``). Обход разработки — как у мастерских
    ручек: только при ``DEBUG`` и только по заголовку.

    DRF-2618 — квота на человека (:data:`MEDIA_PER_PERSON_PER_MINUTE`) —
    здесь же, после подписи и до вьюхи: сверх квоты не делается ни поиска
    мастера, ни похода в каталог. Ключ — бот подписи и id человека в канале
    из той же подписи; строка личности не ищется.
    """

    @wraps(view_func)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        bypass = try_dev_bypass(request)
        if bypass is None:
            verified, refusal = verify_request_init_data(request, surface="master_media")
            if refusal is not None:
                return refusal
            assert verified is not None  # noqa: S101 — отказа нет, значит подпись есть
            identity = f"{verified.bot_slug}:{verified.user_id}"
        else:
            identity = f"dev:{bypass[0].pk}"
        if _over_quota(identity):
            logger.warning("miniapp_api.master_media.person_rate_limited")
            return _rate_limited()
        return view_func(request, *args, **kwargs)

    setattr(wrapper, GUARD_ATTR, "master_media")
    return wrapper


@csrf_exempt
@require_http_methods(["GET"])
@require_signed_session
def master_photo(request: HttpRequest, master_id: str) -> HttpResponse:
    """Фото мастера — байты через бот (DRF-2539)."""
    return _serve(master_id, None)


@csrf_exempt
@require_http_methods(["GET"])
@require_signed_session
def master_portfolio_image(request: HttpRequest, master_id: str, item_id: str) -> HttpResponse:
    """Работа портфолио мастера — байты через бот (DRF-2539)."""
    return _serve(master_id, item_id)
