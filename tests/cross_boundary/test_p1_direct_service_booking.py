"""P1 — прямая услуга → бронь, через настоящую границу.

**Если этот файл покраснел — прочтите README рядом, прежде чем чинить стенд.**
Матрица готовности зовёт P1 PARTIAL: «цена/длительность подменяются молча».
Этот golden **не чинит** подмену — он делает видимым, **какое** число, **из
какого слоя** и **на сколько** разошлось между тем, что бот показал, и тем,
что каталог записал. Красный здесь — находка о продукте, не поломка стенда;
стенд отвечает пульсом (``test_pulse.py``).

Посев (``seed_golden --scenario p1`` в каталоге) кладёт два числа длительности
**намеренно разными**, чтобы по самому числу было видно, какой слой ответил:

```
SalonService.duration_minutes      = 45   каталог берёт в бронь (service_resolver, AMD-019)
SpecialistService.duration_minutes = 60   бот ПОКАЗЫВАЕТ (specialist-services, «путь котировки»)
SpecialistService.price            = 1500 ребро — и бронь, и котировка
SalonService.base_price            = null список услуг (salon-services): цены у слоя нет
```

Что показал локальный стенд 12.09.2026 (каталог ``feat/seed-golden-p1``):
бронь создана с ``snapshot_duration_minutes=45``, ``snapshot_price=1500``;
повтор с тем же ``X-Idempotency-Key`` вернул тот же id; список броней — 1.

Идёт через **код клиента бота** (``AylaBookingHTTPClient``), не через ``httpx``:
заголовки, идемпотентность, разбор — те же, что на пилоте.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from apps.integrations.ayla.booking_client import AylaBookingHTTPClient
from apps.integrations.ayla.identity_client import resolve_identity
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

from .conftest import Catalog

pytestmark = [pytest.mark.cross_boundary, pytest.mark.django_db]

#: Совпадает с ``P1_TENANT_ID`` в ``seed_golden`` каталога: бот зовёт
#: ``catalog/salon-services/?tenant=<id>`` и видит только «свой» тенант.
P1_TENANT_ID = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
P1_SERVICE_NAME = "Golden manicure P1"
P1_EXTERNAL_USER_ID = "bot:golden-p1"

#: Что посеяно — из ``seed_golden``. Тест сверяет с этим, а не выводит из
#: ответа: иначе он подтверждал бы всё, что каталог прислал.
SEEDED_EDGE_PRICE = 1500.0
SEEDED_EDGE_DURATION_S = 60 * 60
SEEDED_SALON_DURATION_S = 45 * 60


@pytest.fixture
def bot_tenant(db) -> Tenant:
    return Tenant.objects.create(id=P1_TENANT_ID, slug="golden-p1", name="Golden P1 Salon")


@pytest.fixture
def booking(bot_points_at_catalog: Catalog) -> AylaBookingHTTPClient:
    # Свой экземпляр, не синглтон: синглтон читает настройки при первом
    # обращении и пережил бы этот тест с адресом стенда внутри.
    return AylaBookingHTTPClient(
        base_url=bot_points_at_catalog.base_url, api_token=bot_points_at_catalog.token
    )


def _golden_service(booking: AylaBookingHTTPClient, bot_tenant: Tenant):
    with tenant_scope(bot_tenant):
        services = booking.get_services()
    matches = [s for s in services if s.title == P1_SERVICE_NAME]
    assert matches, (
        f"на стенде нет услуги {P1_SERVICE_NAME!r} в тенанте {P1_TENANT_ID} — "
        f"seed_golden --scenario p1 не отработал; видно: {[s.title for s in services]}"
    )
    return matches[0]


def _golden_edge(
    booking: AylaBookingHTTPClient, bot_tenant: Tenant, service_id: str
) -> tuple[str, dict]:
    """Ребро мастер×услуга — «путь котировки» бота — и id мастера."""
    with tenant_scope(bot_tenant):
        masters = booking.get_masters()
    assert masters, "на стенде нет мастеров"
    for master in masters:
        edges = booking.get_specialist_service_edges(specialist_id=master.id, service_id=service_id)
        if edges:
            return master.id, edges[0]
    pytest.fail(f"ни у одного мастера нет ребра на услугу {service_id}")


def _free_slot(
    booking: AylaBookingHTTPClient, specialist_id: str, service_id: str, *, day_offset: int
) -> str:
    """Свободный слот на свой день. Каждому вызову — свой день вперёд.

    Стенд между тестами НЕ очищается (это состояние каталога, а не бота),
    и первый прогон это показал: второй тест взял тот же первый слот и
    получил ``409 SLOT_NOT_AVAILABLE``. Посев даёт расписание на все семь
    дней, поэтому день — дешёвый способ развести брони, не гадая, какой
    слот уже занят прошлым тестом или прошлым прогоном.
    """
    day = (date.today() + timedelta(days=day_offset)).isoformat()
    slots = booking.get_available_times(
        specialist_id=specialist_id, date=day, service_id=service_id
    )
    assert slots, f"на {day} нет слотов — расписание не посеяно или день выбран"
    # Не первый, а из середины дня: первый слот дня чаще всего уже занят
    # прошлым прогоном на этом же стенде.
    slot = slots[len(slots) // 2]
    return slot.datetime or f"{day}T{slot.time}:00"


def _book(booking: AylaBookingHTTPClient, bot_tenant: Tenant, *, day_offset: int) -> dict:
    service = _golden_service(booking, bot_tenant)
    specialist_id, edge = _golden_edge(booking, bot_tenant, service.id)
    start = _free_slot(booking, specialist_id, service.id, day_offset=day_offset)
    client_id = str(resolve_identity(P1_EXTERNAL_USER_ID).ayla_user_id)
    key = f"golden-p1-{uuid.uuid4()}"

    before = len(booking.get_user_appointments(external_user_id=P1_EXTERNAL_USER_ID))
    first = booking.create_appointment(
        external_user_id=P1_EXTERNAL_USER_ID,
        client_id=client_id,
        specialist_id=specialist_id,
        service_id=service.id,
        start_datetime=start,
        idempotency_key=key,
    )
    second = booking.create_appointment(
        external_user_id=P1_EXTERNAL_USER_ID,
        client_id=client_id,
        specialist_id=specialist_id,
        service_id=service.id,
        start_datetime=start,
        idempotency_key=key,
    )
    after = len(booking.get_user_appointments(external_user_id=P1_EXTERNAL_USER_ID))
    detail = booking.get_booking_detail(
        external_user_id=P1_EXTERNAL_USER_ID, booking_id=first.appointment_id
    )
    return {
        "service": service,
        "edge": edge,
        "start": start,
        "first": first,
        "second": second,
        "detail": detail,
        "bookings_before": before,
        "bookings_after": after,
    }


def test_the_same_idempotency_key_does_not_create_a_second_booking(
    booking: AylaBookingHTTPClient, bot_tenant: Tenant
) -> None:
    """Дедупликация двойной записи — гарантия границы, и она обязана быть
    зелёной независимо от находок ниже."""
    r = _book(booking, bot_tenant, day_offset=3)

    assert r["first"].appointment_id, r["first"].raw
    assert r["second"].appointment_id == r["first"].appointment_id, (
        "повтор с тем же X-Idempotency-Key создал ВТОРУЮ бронь: "
        f"{r['first'].appointment_id} и {r['second'].appointment_id}"
    )
    # Два POST — плюс одна бронь, не две. Считается списком броней клиента
    # до и после, а не поиском по времени: список отдаёт UTC, слот — со
    # смещением, и сравнение строк времени первый прогон провалил.
    assert r["bookings_after"] == r["bookings_before"] + 1, (
        f"броней было {r['bookings_before']}, стало {r['bookings_after']} — "
        f"ожидалось ровно +1 на два POST с одним ключом"
    )


def test_what_the_bot_quoted_is_what_the_catalog_recorded(
    booking: AylaBookingHTTPClient, bot_tenant: Tenant
) -> None:
    """Сердце P1. Красный здесь — находка: см. шапку файла и README.

    Сравниваются ТРИ ряда: что бот показал в списке услуг, что бот показал
    в котировке ребра, что каталог записал в бронь. Сообщение об ошибке
    называет поле, оба числа и источник каждого — чтобы красный читался
    как факт, а не как «что-то не так».
    """
    r = _book(booking, bot_tenant, day_offset=4)
    service, edge, detail = r["service"], r["edge"], r["detail"]

    quoted_price = float(edge.get("price") or 0)
    quoted_duration_s = int(edge.get("duration_minutes") or 0) * 60
    recorded_price = float(detail.price) if detail.price is not None else None
    recorded_duration_s = detail.duration_s

    print(
        "\n[CROSS-BOUNDARY P1]"
        f"\n  список услуг (salon-services)   price_min={service.price_min} duration_s={service.duration_s}"
        f"\n  котировка ребра (specialist-services) price={quoted_price} duration_s={quoted_duration_s}"
        f"\n  записано в бронь (me/bookings/{{id}})  price={recorded_price} duration_s={recorded_duration_s}"
    )

    # Стража на посев: если ребро не то, что сеяли, всё ниже — не о том.
    assert quoted_price == SEEDED_EDGE_PRICE and quoted_duration_s == SEEDED_EDGE_DURATION_S, (
        f"ребро не совпадает с посевом: price={quoted_price}, duration_s={quoted_duration_s}"
    )

    findings: list[str] = []
    if recorded_price is None:
        # Отсутствие — не разница: вычитать нечего, и это отдельная находка.
        findings.append(
            f"ЦЕНА: показано {quoted_price} (ребро SpecialistService.price), "
            "в брони цены НЕТ (price=null)"
        )
    elif recorded_price != quoted_price:
        findings.append(
            f"ЦЕНА: показано {quoted_price} (ребро SpecialistService.price), "
            f"записано {recorded_price} — разница {recorded_price - quoted_price:+.2f}"
        )
    if recorded_duration_s != quoted_duration_s:
        source = (
            "SalonService.duration_minutes"
            if recorded_duration_s == SEEDED_SALON_DURATION_S
            else "источник не опознан"
        )
        findings.append(
            f"ДЛИТЕЛЬНОСТЬ: показано {quoted_duration_s // 60} мин (ребро "
            f"SpecialistService.duration_minutes), записано "
            f"{(recorded_duration_s or 0) // 60} мин ({source}) — разница "
            f"{((recorded_duration_s or 0) - quoted_duration_s) // 60:+d} мин"
        )
    assert not findings, (
        "бот показал одно, каталог записал другое (матрица P1 PARTIAL — это находка, "
        "не поломка стенда):\n  " + "\n  ".join(findings)
    )


def test_the_service_list_does_not_show_a_zero_price_for_a_priced_edge(
    booking: AylaBookingHTTPClient, bot_tenant: Tenant
) -> None:
    """Второй ряд: список услуг, который человек видит ДО котировки.

    ``salon-services`` несёт ``base_price``, у слоя её нет (``null``), и
    клиент бота превращает это в ``0.0`` — ноль как имя отсутствия, тот же
    класс, что §103. Человек видит «0 ₽» на услуге, у которой ребро стоит
    1500. Красный здесь — вторая находка P1, отдельная от длительности.
    """
    service = _golden_service(booking, bot_tenant)
    print(
        f"\n[CROSS-BOUNDARY P1] список услуг: price_min={service.price_min} price_max={service.price_max}"
    )
    assert service.price_min > 0 or service.price_max > 0, (
        f"список услуг показывает цену {service.price_min}/{service.price_max} на услуге, "
        f"у которой ребро стоит {SEEDED_EDGE_PRICE}: ноль подставлен вместо отсутствия"
    )
