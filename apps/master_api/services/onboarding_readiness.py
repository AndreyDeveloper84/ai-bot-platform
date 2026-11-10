"""Готовность онбординга мастера — проекция по фактам, без хранимого состояния (DRF-1794, M2).

Экран 01 макета («[имя], всё готово!») обещает чек-лист «Осталось настроить»
и «Настройку можно прервать и продолжить позже — всё сохранится». Слово
владельца (PROMPT §19): readiness — проекция, ``onboarding_completed=true``
не хранится, если его можно вычислить из authoritative-фактов.

Здесь пять пунктов, и у каждого — свой источник истины и своё «не знаю»:

===============  ==========================================================  ==========================
пункт            факт                                                        когда ``unknown``
===============  ==========================================================  ==========================
``services``     выбор услуг мастера в каталоге (``services/selection``);    каталог молчит или нет
                 без субъекта — строки ``MasterService`` зеркала             субъекта у соло, а зеркало
                                                                             «настроено» не говорит
``hours``        недельный шаблон из ``load_day_frame`` (Ayla при флаге)     канон не читается (DRF-1111)
``profile``      ``name`` и ``photo_url`` у ``CatalogMaster``                —
``location``     своё место мастера в каталоге (``service-locations``)       нет субъекта / каталог молчит
``identity``     ``SoloIdentityLink.status`` соло-мастера / столбец ключа    —
===============  ==========================================================  ==========================

Три правила, ради которых модуль не свёрнут в четыре булева:

* **Незнание — не «нет».** Когда Ayla не отвечает, пункт ``hours`` получает
  ``state="unknown"`` с именем причины, а не ``missing``: «настройте
  расписание» человеку, который его настроил, — ложь, и та же ложь в
  обратную сторону (DRF-1111: отказываться, а не гадать).
* **Место работы — по правилу каталога, не своему** (DRF-2370). До DRF-2370
  пункт ``location`` отвечал ``unavailable``/``capability_not_built``
  безусловно, хотя способ указать место был построен целиком (каталог #502,
  клиент и ручка ``/service-locations``, экран 05) — и ``ready`` не бывал
  истиной ни у кого. Теперь «готово» = то, что каталог требует для ОТПРАВКИ
  профиля (``users/publication.py``, этап «к проверке»): место есть и оно не
  ``inactive``. ``review_required`` — уже готово к отправке; ``confirmed``
  ставит модератор при одобрении, и условием готовности он не является. Зона
  выезда место не заменяет (у каталога ``location_area_unavailable``).
  Разойтись с каталогом значило бы снова пообещать «всё готово» и вернуть
  мастера отказом «Место работы ещё не указано».
* **Один гейт продажи.** ``setup_state``/``sale_block`` берутся из
  :func:`apps.catalog.master_state.sale_block` — того же, что читают
  витрина, ростер и бронь; второго определения «опубликован» здесь нет.

Числа в ответе — только серверные (§3 карты: «счётчики — только от
сервера»): счётчики каталога либо посчитанные здесь из строк зеркала;
процентов и «шаг N из M» нет намеренно.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import timedelta
from decimal import Decimal
from typing import Any, Literal

from django.utils import timezone

from apps.catalog.master_state import IDENTITY_LINKED, SaleBlock, sale_block
from apps.catalog.models import CatalogMaster
from apps.catalog.specialist_ref import CatalogSpecialistUnresolved, catalog_specialist_id
from apps.identity.services.workspace_kind import workspace_kind
from apps.integrations.ayla.booking_client import (
    BookingBadRequestError,
    BookingUnavailableError,
    get_ayla_booking_client,
)
from apps.integrations.ayla.salon_client import SalonAPIError, SalonNotConfigured, SalonUnavailable
from apps.master_api.services.catalog import list_master_services
from apps.master_api.services.schedule_frame import load_day_frame

logger = logging.getLogger(__name__)

ItemState = Literal["done", "missing", "unknown", "unavailable"]

#: Пункты, без которых ``ready`` не бывает истиной. ``identity`` сюда не
#: входит: связь — условие ПУБЛИКАЦИИ (ruling 6), а не настройки; она
#: отдаётся отдельным полем, чтобы экран показал её состояние, не смешивая с
#: пунктами настройки. Довязывает её бот (``solo_link_retry``), не оператор.
REQUIRED_ITEMS: tuple[str, ...] = ("services", "location", "hours", "profile")

#: Причины пункта ``location`` — те же слаги, что у каталога
#: (``users/publication.py``), чтобы экран и ответ отправки говорили одно.
LOCATION_NOT_ASSIGNED = "location_not_assigned"
LOCATION_INACTIVE = "location_inactive"
#: Статус места «недействительно» у каталога (``LocationStatus.INACTIVE``).
PLACE_INACTIVE = "inactive"
#: Места нельзя спросить без субъекта: каталог отдаёт его только мастеру.
NO_SUBJECT = "no_subject"
#: Клиент каталога не настроен (нет ``AYLA_BASE_URL``) — спросить нельзя.
CLIENT_NOT_CONFIGURED = "booking_client_not_configured"
#: Услуги есть, но ни одна не продаётся (DRF-1989) — тот же слаг, что у отказа записи.
OFFER_NOT_SELLABLE = "offer_not_sellable"
#: Код отказа каталога на ``services/selection``: у этого кабинета выбора услуг нет.
SERVICE_SELECTION_REFUSED = "SERVICE_SELECTION_REFUSED"

#: Куда ведёт каждый пункт — маршруты соло-поверхности (``App.tsx`` /solo/*).
DEEP_LINKS: dict[str, str] = {
    "services": "/solo/services",
    "location": "/solo/place",  # DRF-1811 (M19): экран 05 «Место работы»
    "hours": "/solo/working-hours",  # M25 (#1711): редактор часов, не журнал записей
    "profile": "/solo/profile",
}

#: DRF-1809 (M17) → DRF-1808 (M16): пока не выбрано ни одной услуги, пункт
#: «services» ведёт на экран 02 — направления (макет: 01 → 02 → 03), а не на
#: экран цен, где новичку нечего настроить. Экран 03 остаётся достижим с
#: экрана 02 и с экрана 04. Считается по тому же ``detail.selected``, что
#: отдаёт пункт. Экран 01 своей константы не держит — ведёт по ``deep_link``.
SERVICES_DIRECTIONS_LINK = "/solo/directions"

#: DRF-2254 — пункты, которые ведёт не мастер в приложении, когда каталог
#: называет рабочее пространство салоном (``Tenant.kind == salon``). Причина
#: нейтральная: это и настоящий однолюдный салон (место ведёт салон — правда),
#: и соло до G4, которому признак проставят позже (решение владельца, 3а).
#: Такие пункты ``unavailable`` и без ``deep_link`` (вести некуда). ``ready``
#: они блокируют, как блокирует ``capability_not_built``: достижимость «готово»
#: при ведении вне приложения — отдельное решение владельца, не этот лист.
MANAGED_OUTSIDE_APP = "managed_outside_app"
SALON_MANAGED_ITEMS: tuple[str, ...] = ("services", "location")


@dataclass(frozen=True)
class ReadinessItem:
    key: str
    state: ItemState
    detail: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None

    @property
    def deep_link(self) -> str | None:
        if self.reason == MANAGED_OUTSIDE_APP:
            return None
        if self.key == "services" and not self.detail.get("selected"):
            return SERVICES_DIRECTIONS_LINK
        return DEEP_LINKS[self.key]

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "state": self.state,
            "detail": dict(self.detail),
            "reason": self.reason,
            "deep_link": self.deep_link,
        }


@dataclass(frozen=True)
class Readiness:
    items: tuple[ReadinessItem, ...]
    identity: dict[str, Any]
    sale_block: SaleBlock | None

    @property
    def blocking(self) -> list[str]:
        """Пункты, из-за которых ``ready`` ложно — с состоянием, не только именем."""

        return [
            f"{item.key}:{item.state}"
            for item in self.items
            if item.key in REQUIRED_ITEMS and item.state != "done"
        ]

    @property
    def ready(self) -> bool:
        return not self.blocking

    @property
    def setup_state(self) -> str:
        return "READY" if self.sale_block is None else "SETUP_PENDING"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "blocking": self.blocking,
            "items": [item.as_dict() for item in self.items],
            "identity": dict(self.identity),
            "setup_state": self.setup_state,
            "sale_block": self.sale_block,
        }


def build_readiness(master: CatalogMaster, *, actor: str | None = None) -> Readiness:
    """Собрать проекцию по живым фактам. Ничего не пишет.

    ``actor`` — внешний идентификатор мастера (``external_user_id_for``): место
    работы и выбор услуг каталог отдаёт только под субъектом. Без него пункт
    ``location`` — ``unknown`` с причиной ``no_subject`` (карточка в админке
    оператора), а не догадка; пункт ``services`` отвечает по зеркалу — кроме
    соло-кабинета, где зеркало «нет» не видит: ответ тот же, ``no_subject``,
    если зеркало само не говорит «настроено».

    DRF-2254: вид рабочего пространства — из каталога (``workspace_kind``).
    ``salon`` — место и услуги ведутся вне приложения; ``solo`` и «не знаю» —
    как прежде.
    """

    kind = workspace_kind(master.tenant_id)
    salon_managed = kind == "salon"
    return Readiness(
        items=(
            _managed_outside("services")
            if salon_managed
            else _services_item(master, actor, solo=kind == "solo"),
            _managed_outside("location") if salon_managed else _location_item(master, actor),
            _hours_item(master),
            _profile_item(master),
        ),
        identity=identity_facts(master),
        sale_block=sale_block(master),
    )


# ─── пункты ──────────────────────────────────────────────────────────────────


def _services_item(master: CatalogMaster, actor: str | None, *, solo: bool) -> ReadinessItem:
    """Услуги — по правилу каталога для отправки профиля, как и место.

    Живой прогон регистрации соло-мастера (09.11.2026) нашёл разрыв: каталог
    отвечал «готов к отправке», а этот пункт — ``missing`` навсегда, и кнопка
    «Отправить профиль на проверку» (её включает ``ready``) мастеру не
    показывалась вовсе. Причина — два разных определения «настроено»:

    * каталог (``users/publication.py`` → ``selected_services``): активная
      услуга + активное предложение мастера с длительностью;
    * зеркало (:func:`_services_from_mirror`): цена и длительность на строке
      ``CatalogService``. У соло-мастера цена живёт на ПРЕДЛОЖЕНИИ, а эти поля
      услуги каталог оставляет пустыми — зеркало не скажет «настроено» никогда.
      К тому же зеркало отстаёт на период синхронизации (15 минут).

    Поэтому, когда каталог можно спросить под субъектом, отвечает он — той же
    ручкой, что экран выбора услуг (``/services/selection``). Правило
    DRF-1989 остаётся: предложение дешевле 1 ₽ настроенным не считается.

    Правило зеркала остаётся там, где каталог на этот вопрос не отвечает:
    без субъекта (карточка в админке; у соло — см. ниже), без настроенного
    клиента, при отказе
    «выбор услуг не для этого кабинета» (``SERVICE_SELECTION_REFUSED`` — он
    есть только у соло) и при ответе не той формы. Каталог молчит или
    отказывает по другой причине — ``unknown``, а не ``missing`` («незнание —
    не „нет“»); но положительный факт зеркала это не отменяет.

    Без субъекта у соло-кабинета (``solo`` — вид из каталога, DRF-2254) зеркало
    «нет» сказать не вправе: цена у соло живёт на предложении, которого в
    зеркале нет, — так на карточке оператора у готового к отправке мастера
    стояло «Услуги: нет» (прогон 09.11.2026). Ответ тот же, что у места:
    ``unknown`` / ``no_subject`` — «не спрошено». Положительный факт зеркала
    и здесь остаётся. Причина зеркала (``offer_not_sellable``) не переносится:
    она — часть ответа «нет», которого без субъекта у соло не бывает; как и в
    ветке отказа каталога, причиной ``unknown`` остаётся то, почему не спросили.
    """
    if actor is None:
        mirror = _services_from_mirror(master)
        if solo and mirror.state != "done":
            return ReadinessItem("services", "unknown", dict(mirror.detail), reason=NO_SUBJECT)
        return mirror
    try:
        client = get_ayla_booking_client()
    except ValueError:
        return _services_from_mirror(master)
    try:
        data = client.get_service_selection(
            specialist_id=catalog_specialist_id(master), external_user_id=actor
        )
    except (BookingBadRequestError, BookingUnavailableError, CatalogSpecialistUnresolved) as exc:
        mirror = _services_from_mirror(master)
        if _selection_refused(exc) or mirror.state == "done":
            return mirror
        # Любой другой отказ (не тот токен, субъект не разрешён, id устарел)
        # — «не спросить», как у места работы: у соло зеркало здесь сказало
        # бы ``missing``, которого на деле нет.
        reason = type(exc).__name__
        logger.info("master.readiness.services_unknown master=%s reason=%s", master.id, reason)
        return ReadinessItem("services", "unknown", dict(mirror.detail), reason=reason)

    counted = _count_selection(data)
    if counted is None:
        logger.warning("master.readiness.services_selection_malformed master=%s", master.id)
        return _services_from_mirror(master)
    selected, configured, unsellable = counted
    detail = {"selected": selected, "configured": configured}
    if configured:
        return ReadinessItem("services", "done", detail)
    return ReadinessItem(
        "services", "missing", detail, reason=OFFER_NOT_SELLABLE if unsellable else None
    )


def _selection_refused(exc: Exception) -> bool:
    """Каталог сказал «выбор услуг — не для этого кабинета» (409, только у соло он есть)."""

    return isinstance(exc, BookingBadRequestError) and exc.code == SERVICE_SELECTION_REFUSED


def _count_selection(data: Any) -> tuple[int, int, int] | None:
    """``(выбрано, настроено и продаётся, настроено дешевле 1 ₽)`` либо ``None``.

    Счётчики — каталожные (``selected`` / ``configured``), бот их не
    пересчитывает; из «настроено» вычитается только то, что правило DRF-1989
    продажей не считает. ``None`` — ответ не той формы.
    """

    if not isinstance(data, dict):
        return None
    selected, configured = _as_count(data.get("selected")), _as_count(data.get("configured"))
    if selected is None or configured is None:
        return None
    rows = data.get("services")
    unsellable = 0
    if isinstance(rows, list):
        unsellable = sum(
            1
            for row in rows
            if isinstance(row, dict) and row.get("configured") is True and _below_one_rouble(row)
        )
    return selected, max(configured - unsellable, 0), unsellable


def _as_count(value: Any) -> int | None:
    """Целое неотрицательное число — оно само; что угодно другое — ``None``."""

    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _below_one_rouble(row: dict[str, Any]) -> bool:
    """Цена предложения читается и она меньше 1 ₽. Не читается — не вычитаем."""

    offer = row.get("offer")
    if not isinstance(offer, dict):
        return False
    try:
        return Decimal(str(offer.get("price"))) < 1
    except ArithmeticError:  # ``InvalidOperation`` — цена не число
        return False


def _services_from_mirror(master: CatalogMaster) -> ReadinessItem:
    """Тот же пункт по зеркалу — когда каталог на этот вопрос не отвечает."""
    rows = list_master_services(master=master)
    # DRF-1989: «настроено» — только продаваемое: непродаваемое ребро и цена
    # ниже 1 ₽ (``price_rub is None``) не настроены. Форма ``detail`` прежняя;
    # когда не настроено ничего, а непродаваемое есть, причина — в ``reason``
    # (слаг ``offer_not_sellable``, как у отказа записи).
    configured = [
        r
        for r in rows
        if r["sellable"] and r["price_rub"] is not None and (r["duration_min"] or 0) > 0
    ]
    detail = {"selected": len(rows), "configured": len(configured)}
    if configured:
        return ReadinessItem("services", "done", detail)
    reason = OFFER_NOT_SELLABLE if any(not r["sellable"] for r in rows) else None
    return ReadinessItem("services", "missing", detail, reason=reason)


def _managed_outside(key: str) -> ReadinessItem:
    return ReadinessItem(key, "unavailable", {}, reason=MANAGED_OUTSIDE_APP)


def _location_item(master: CatalogMaster, actor: str | None) -> ReadinessItem:
    """Своё место мастера — по правилу каталога для отправки профиля (DRF-2370)."""
    if actor is None:
        return ReadinessItem("location", "unknown", {}, reason=NO_SUBJECT)
    try:
        client = get_ayla_booking_client()
    except ValueError:
        # Клиент не собирается без AYLA_BASE_URL — это «не спросить», а не
        # «места нет»; и уж точно не 500 на весь чек-лист.
        return ReadinessItem("location", "unknown", {}, reason=CLIENT_NOT_CONFIGURED)
    try:
        data = client.get_service_locations(
            specialist_id=catalog_specialist_id(master), external_user_id=actor
        )
    except (BookingBadRequestError, BookingUnavailableError, CatalogSpecialistUnresolved) as exc:
        reason = type(exc).__name__
        logger.info("master.readiness.location_unknown master=%s reason=%s", master.id, reason)
        return ReadinessItem("location", "unknown", {}, reason=reason)

    places = data.get("places") or []
    areas = data.get("areas") or []
    detail = {"place_status": None, "areas": len(areas)}
    if not places:
        # Зона выезда место не заменяет — у каталога так же.
        return ReadinessItem("location", "missing", detail, reason=LOCATION_NOT_ASSIGNED)
    status = places[0].get("status")
    detail["place_status"] = status
    if status == PLACE_INACTIVE:
        return ReadinessItem("location", "missing", detail, reason=LOCATION_INACTIVE)
    return ReadinessItem("location", "done", detail)


def _hours_item(master: CatalogMaster) -> ReadinessItem:
    today = timezone.now().date()
    from apps.tenancy.timezones import salon_zone

    # DRF-2595: битое имя бросало и бросает — теперь с журналом; пусто давало
    # МСК и даёт (выход — показ, не обязательство).
    tz = salon_zone(master.tenant, refuse_broken=True)
    try:
        weekly, _exceptions, _blocks = load_day_frame(
            master,
            from_date=today,
            to_date=_a_week_from(today),
            tz=tz,
        )
    except (SalonNotConfigured, SalonUnavailable, SalonAPIError) as exc:
        # Канон не читается — отказ, не догадка (DRF-1111).
        reason = type(exc).__name__
        logger.info("master.readiness.hours_unknown master=%s reason=%s", master.id, reason)
        return ReadinessItem("hours", "unknown", {}, reason=reason)

    working_days = sorted(
        weekday for weekday, row in weekly.items() if bool(getattr(row, "is_working", False))
    )
    detail = {"working_days": working_days}
    return ReadinessItem("hours", "done" if working_days else "missing", detail)


def _profile_item(master: CatalogMaster) -> ReadinessItem:
    has_name = bool((master.name or "").strip())
    has_photo = bool((master.photo_url or "").strip())
    detail = {"name": has_name, "photo": has_photo, "bio": bool((master.bio or "").strip())}
    # Фото обязательно для публикации, не для сохранения (P57) — но пункт
    # «Профиль» чек-листа закрыт только когда есть и имя, и фото.
    return ReadinessItem("profile", "done" if has_name and has_photo else "missing", detail)


def identity_facts(master: CatalogMaster) -> dict[str, Any]:
    """Состояние связи — то, что читает гейт продажи, словами для экрана.

    Соло-мастер несёт строку ``SoloIdentityLink``; у мастера салона её нет,
    и вопрос решает столбец ``ayla_user_id`` (как в ``master_state``).
    """

    link = getattr(master, "identity_link", None)
    if link is None:
        state = "linked" if master.ayla_user_id is not None else "unlinked"
        return {"state": state, "link_status": None}
    if link.status == IDENTITY_LINKED:
        state = "linked"
    elif link.status == "IDENTITY_LINK_REJECTED":
        state = "rejected"
    else:
        state = "pending"
    return {"state": state, "link_status": link.status}


def _a_week_from(day: date_cls) -> date_cls:
    return day + timedelta(days=6)


__all__ = [
    "MANAGED_OUTSIDE_APP",
    "Readiness",
    "ReadinessItem",
    "REQUIRED_ITEMS",
    "build_readiness",
    "identity_facts",
]
