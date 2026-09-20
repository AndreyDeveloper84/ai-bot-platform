"""Сердцевина ручек записи — одна на салонную и мастерскую поверхности (DRF-2154).

До М-2 создание записи, слоты и поиск клиента жили целиком во вьюхах
``admin_api`` (``views_booking_create``, ``views_availability_slots``,
``views_customers``): разбор тела, резолв услуги, вызов Ayla и девять веток
исключений → исход §18 — всё в одной функции. Мастеру нужны те же три
действия под своей личностью, и лист DRF-2154 требует «те же сервисы, не
копия». Здесь — то общее, что не зависит от того, кто нажал кнопку:

* :func:`bookable_service` — услуга этого салона, привязанная к Ayla;
* :func:`create_appointment_as` — вызов Ayla и перевод её ответов в исход §18;
* :func:`bookable_starts` — окна, куда услуга помещается целиком;
* :func:`search_customers_as` — поиск возвращающегося клиента;
* :func:`public_customer_row` / :func:`slot_payload` — форма строки наружу.

Что здесь НЕ живёт: разбор HTTP-тела, выбор мастера (админ берёт его из тела,
мастер — из ``request.master``) и права. Вьюхи остаются обёртками: их
ответы, коды и журнальные слова не меняются — тесты admin_api тому свидетель.

Ayla владеет записью (ADR-0009, правило 5): ничего здесь не пишет строку
записи. Клиенты Ayla импортируются внутри функций — тесты обеих поверхностей
подменяют ``get_salon_client`` / ``get_ayla_booking_client`` по модулю.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from apps.catalog.models import CatalogService
from apps.catalog.specialist_ref import CatalogSpecialistUnresolved, catalog_specialist_id

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Refusal:
    """Отказ формы ``{"error": slug, "detail": …}`` — до вызова Ayla или без исхода §18."""

    slug: str
    detail: str
    status: int


@dataclass(frozen=True)
class Outcome:
    """Исход §18: ``committed`` / ``conflict`` / ``blocked`` / ``pending`` / ``failed``.

    ``outcome`` едет отдельным полем, а не выводится из кода ответа: 409
    бывает и «время занято», и «уже существует», а экран реагирует на них
    по-разному.
    """

    outcome: str
    detail: str
    status: int
    extra: dict[str, Any] = field(default_factory=dict)


#: Что говорит каждая поверхность о неподготовленной услуге — один текст.
SERVICE_NOT_FOUND = Refusal("not_found", "service not found", 404)
SERVICE_NOT_BOOKABLE = Refusal(
    "service_not_bookable", "this service is not linked to the booking system yet", 409
)


def bookable_service(tenant_id: Any, service_id: str) -> CatalogService | Refusal:
    """Услуга этого салона, которую можно записать в Ayla.

    Строка каталога без ``ayla_service_id`` в Ayla не записывается: сказать
    об этом лучше, чем спросить слоты с пустым id и отдать пустоту как
    «свободного времени нет».
    """

    service = CatalogService.objects.filter(tenant_id=tenant_id, id=service_id).first()
    if service is None:
        return SERVICE_NOT_FOUND
    if not service.ayla_service_id:
        logger.warning("booking.service_not_bridged service=%s tenant=%s", service.id, tenant_id)
        return SERVICE_NOT_BOOKABLE
    return service


def create_appointment_as(
    *,
    actor: str,
    tenant: Any,
    master: Any,
    service: CatalogService,
    start_at: str,
    idempotency_key: str,
    client_id: str | None,
    client_name: str | None,
    client_phone: str | None,
    log: str,
    journal: logging.Logger | None = None,
) -> Outcome:
    """Записать клиента в Ayla от имени ``actor`` и назвать, что случилось.

    ``log`` — префикс журнала поверхности (``admin_api.create_booking``,
    ``master_api.create_booking``): по нему видно, с какого экрана пришло;
    ``journal`` — логгер поверхности, чтобы строки остались там, где их
    читают (счётчик разметки HEALTH_CHECK_UNKNOWN слушает логгер вьюхи).
    Ветки и коды — те, что были во вьюхе администратора, дословно.
    """

    from apps.integrations.ayla.health_check import text_for
    from apps.integrations.ayla.offer_refusal import OFFER_NOT_SELLABLE_SLUG, staff_text_for
    from apps.integrations.ayla.salon_client import (
        SalonAPIError,
        SalonForbidden,
        SalonHealthCheckHandoff,
        SalonNotConfigured,
        SalonNotFound,
        SalonOfferNotSellable,
        SalonSlotTaken,
        SalonUnauthorized,
        SalonUnavailable,
        SalonValidationError,
        get_salon_client,
    )

    jl = journal or logger
    # DRF-1933: у строки зеркала нет id профиля в каталоге — звать каталог
    # не с чем; первичный ключ зеркала туда не уходит.
    try:
        catalog_specialist_id(master)
    except CatalogSpecialistUnresolved:
        return Outcome("blocked", "master is not set up in the catalog yet", 409)
    try:
        created = get_salon_client().create_appointment(
            actor_external_id=actor,
            idempotency_key=idempotency_key,
            tenant_slug=tenant.slug,
            specialist_id=catalog_specialist_id(master),
            service_id=str(service.ayla_service_id),
            start_datetime=start_at,
            client_id=client_id,
            client_name=client_name,
            client_phone=client_phone,
        )
    except SalonNotConfigured as exc:
        jl.error("%s.not_configured err=%s", log, exc)
        return Outcome("blocked", "booking is not configured on this deployment", 503)
    except SalonUnauthorized as exc:
        # Наш ключ, не права этого человека. Громко в журнал — починить может
        # только оператор; нейтрально на экране — нажавший ни при чём.
        jl.error("%s.upstream_unauthorized tenant=%s err=%s", log, tenant.id, exc)
        return Outcome("blocked", "запись сейчас недоступна — обратитесь к поддержке", 503)
    except SalonSlotTaken as exc:
        return Outcome("conflict", str(exc), 409)
    except SalonForbidden as exc:
        jl.warning("%s.forbidden actor=%s tenant=%s err=%s", log, actor, tenant.id, exc)
        # §106 — `blocked` наружу одно, различает `reason_code`.
        return Outcome("blocked", str(exc), 403, {"reason_code": "permission_denied"})
    except SalonNotFound as exc:
        # Ayla не знает мастера или клиента как этого салона. Для клиента это
        # приглашение записать его новым гостем — конфликт, не тупик.
        return Outcome("conflict", str(exc), 404)
    except SalonValidationError as exc:
        return Outcome("blocked", str(exc), 400)
    except SalonUnavailable as exc:
        # Запись могла лечь. Никогда не называть это отказом.
        jl.warning("%s.unknown actor=%s err=%s", log, actor, exc)
        return Outcome(
            "pending",
            "the schedule did not answer — refresh the day before trying again",
            504,
            {"idempotency_key": idempotency_key},
        )
    except SalonHealthCheckHandoff as exc:
        # DRF-1614: осознанный медицинский отказ, не поломка сервера — ветка
        # стоит до SalonAPIError. Код в журнале точный, на экране —
        # объединённое имя: очередь разметки считает HEALTH_CHECK_UNKNOWN по
        # услугам, и счётчик умрёт, если REQUIRED и UNKNOWN придут одним словом.
        jl.info(
            "%s.health_check_handoff actor=%s tenant=%s code=%s",
            log,
            actor,
            tenant.id,
            exc.code or "MISSING",
        )
        return Outcome(
            "blocked",
            text_for(exc.code, handoff=exc.handoff),
            422,
            {"reason_code": (exc.code or "").lower() or "health_check_unspecified"},
        )
    except SalonOfferNotSellable as exc:
        # DRF-1989: осознанный отказ каталога — `blocked` с причиной.
        jl.info(
            "%s.offer_not_sellable actor=%s tenant=%s reason=%s",
            log,
            actor,
            tenant.id,
            exc.reason,
        )
        return Outcome(
            "blocked",
            staff_text_for(exc.reason),
            409,
            {"reason_code": OFFER_NOT_SELLABLE_SLUG, "unsellable_reason": exc.reason},
        )
    except SalonAPIError as exc:
        jl.warning("%s.error actor=%s err=%s", log, actor, exc)
        return Outcome("failed", str(exc), 502)

    appointment_id = str(created.get("id") or "") if isinstance(created, dict) else ""
    jl.info("%s.committed appointment=%s actor=%s tenant=%s", log, appointment_id, actor, tenant.id)
    return Outcome("committed", "appointment created", 201, {"appointment_id": appointment_id})


def bookable_starts(
    *,
    master: Any,
    service: CatalogService,
    day: date,
    log: str,
    journal: logging.Logger | None = None,
) -> list[Any] | Refusal:
    """Окна одного мастера под одну услугу на один день — из Ayla.

    Отказ Ayla никогда не сериализуется как «слотов нет»: пустой список и
    недоступное расписание выглядят одинаково для человека с телефоном в
    руке, и только один из них значит «предложи другой день» (§16/§17).
    """

    from apps.integrations.ayla.booking_client import (
        BookingAPIError,
        BookingUnavailableError,
        get_ayla_booking_client,
    )

    jl = journal or logger
    # DRF-1933: см. create_appointment_as.
    try:
        catalog_specialist_id(master)
    except CatalogSpecialistUnresolved:
        return Refusal("catalog_profile_unresolved", "master is not set up in the catalog yet", 409)
    try:
        return list(
            get_ayla_booking_client().get_available_times(
                specialist_id=catalog_specialist_id(master),
                date=day.isoformat(),
                service_id=str(service.ayla_service_id),
            )
        )
    except BookingUnavailableError as exc:
        # Транзиент: таймаут, сеть, открытый предохранитель.
        jl.warning("%s.upstream_unavailable master=%s date=%s err=%s", log, master.id, day, exc)
        return Refusal(
            "schedule_unavailable",
            "the schedule is temporarily unreachable — try again in a moment",
            503,
        )
    except BookingAPIError as exc:
        jl.warning("%s.upstream_error master=%s date=%s err=%s", log, master.id, day, exc)
        return Refusal("schedule_error", "the schedule refused the request", 502)


def slot_payload(slot: Any) -> dict[str, Any]:
    duration_s = getattr(slot, "duration_s", None)
    return {
        "time": getattr(slot, "time", ""),
        # Ayla не всегда шлёт полную метку; поле остаётся nullable, а не
        # восстанавливается здесь — восстановить значило бы выбрать часовой
        # пояс за клиента, ровно то локальное вычисление, что запрещает §17.
        "start_at": getattr(slot, "datetime", None),
        "duration_min": int(duration_s // 60) if duration_s else None,
    }


def search_customers_as(
    *,
    actor: str,
    tenant: Any,
    query: str,
    log: str,
    journal: logging.Logger | None = None,
) -> list[dict[str, Any]] | Refusal:
    """Найти возвращающегося клиента салона от имени ``actor`` (§13).

    Недоступный поиск — никогда не пустой список: «не нашли» и «не смогли
    спросить» на экране одинаковы, а значат противоположное; спутать их —
    завести клиента второй раз.
    """

    from apps.integrations.ayla.salon_client import (
        SalonAPIError,
        SalonForbidden,
        SalonNotConfigured,
        SalonNotFound,
        SalonUnauthorized,
        SalonUnavailable,
        SalonValidationError,
        get_salon_client,
    )

    jl = journal or logger
    try:
        return list(
            get_salon_client().search_customers(
                actor_external_id=actor, tenant_slug=tenant.slug, query=query
            )
        )
    except SalonValidationError as exc:
        # Включая порог в два символа. 400 — о запросе, не о салоне.
        return Refusal("bad_request", str(exc), 400)
    except SalonNotConfigured as exc:
        jl.error("%s.not_configured err=%s", log, exc)
        return Refusal("unavailable", "customer search is not configured", 503)
    except SalonUnauthorized as exc:
        jl.error("%s.upstream_unauthorized tenant=%s err=%s", log, tenant.id, exc)
        return Refusal("unavailable", "customer search is unavailable", 503)
    except SalonForbidden as exc:
        jl.warning("%s.forbidden actor=%s tenant=%s err=%s", log, actor, tenant.id, exc)
        return Refusal("forbidden", "not permitted in this salon", 403)
    except (SalonNotFound, SalonUnavailable, SalonAPIError) as exc:
        # Намеренно вместе: для стойки это одна ситуация — «не смогли
        # спросить», и ни одна из них не «такого клиента нет».
        jl.warning("%s.unavailable err=%s", log, exc)
        return Refusal("unavailable", "customer search is unavailable", 503)


def public_customer_row(row: dict[str, Any]) -> dict[str, Any]:
    """Свести строку Ayla к тому, что можно показать в выборе клиента.

    Две формы требуют обработки, обе из ``_client_name`` Ayla (2026-08-21):
    пустое имя (намеренно там, но пустая строка в выборе бесполезна —
    нейтральная заглушка) и username вместо имени (для пришедших через бота
    это хэндл канала ``bot:max:83146139`` — внутренний идентификатор, не
    имя). Ни то ни другое не прячется молча: безымянный клиент читается
    безымянным, и это правда.
    """

    name = str(row.get("name") or "").strip()
    if ":" in name and name.split(":", 1)[0].isalnum() and " " not in name:
        # Форма хэндла канала (`bot:max:123`), никогда не человеческое имя.
        name = ""
    return {
        "id": str(row.get("id") or ""),
        "name": name or "Без имени",
        # Позволяет выбору пометить заглушку заглушкой, а не выдать её за
        # клиента, которого действительно зовут «Без имени».
        "named": bool(name),
    }


__all__ = [
    "Outcome",
    "Refusal",
    "SERVICE_NOT_BOOKABLE",
    "SERVICE_NOT_FOUND",
    "bookable_service",
    "bookable_starts",
    "create_appointment_as",
    "public_customer_row",
    "search_customers_as",
    "slot_payload",
]
