"""Гео мастера и адрес салона доезжают до колонок — DRF-1588.

Замер на боевом пилоте 08.09.2026: из 34 строк ``CatalogMaster`` 31 несла
в ``raw`` ключи ``address`` / ``location_lat`` / ``location_lng``, а колонок
под них не было ни одной. Данные были — доступа к ним не было: по ключу
внутри JSON нельзя ни искать, ни фильтровать, ни сортировать.

Тесты делятся на две половины, и вторая важнее первой.

**Первая** пришпиливает перенос: то, что лежит в слепке, оказывается в
колонке.

**Вторая** пришпиливает то, чего в колонке оказаться НЕ ДОЛЖНО. Это тот же
класс дефекта, который вычищался 07-08.09 (OPEN_DECISIONS §65): норма воды
выводилась из веса, норма калорий была плоской константой, поданной как
персональная цель. Подставленное значение неотличимо от настоящего, и
человек считает его фактом. Здесь подстановка выглядела бы так:

* ``row.get("address") or ""`` — отсутствие ключа становится пустым адресом,
  то есть «не знаем» печатается как «адреса нет»;
* ``float(row.get("location_lat") or 0)`` — отсутствие координаты становится
  парой нулей, то есть точкой в Гвинейском заливе, которая доедет до карты
  как настоящая.

Обе подстановки красят эти тесты в красный. Без них дефект вернулся бы тем
же путём, каким пришёл.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from apps.catalog.models import CatalogMaster
from apps.catalog.services.http_client import CatalogSpecialistDTO, _parse_specialist
from apps.catalog.services.upserter import upsert_specialists
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="geo-salon", name="Гео-салон")


def _row(**overrides) -> dict:
    """Строка фида ``/internal/specialists/`` в форме боевого слепка."""

    row = {
        "id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "display_name": "Анна Иванова",
        "updated_at": "2026-09-08T12:00:00Z",
        "status": "active",
        "is_available": True,
    }
    row.update(overrides)
    return row


def _dto(**overrides) -> CatalogSpecialistDTO:
    return _parse_specialist(_row(**overrides))


def _master(tenant: Tenant, dto: CatalogSpecialistDTO) -> CatalogMaster:
    upsert_specialists(tenant, [dto])
    return CatalogMaster.all_tenants.get(pk=dto.ayla_master_id)


# --------------------------------------------------------------------------
# Половина первая — то, что есть в слепке, доезжает до колонки
# --------------------------------------------------------------------------


def test_address_from_snapshot_lands_in_column(tenant):
    """Адрес из слепка оказывается в колонке, а не только внутри ``raw``."""

    dto = _dto(address="Пенза, ул. Карпинского, 33А")

    master = _master(tenant, dto)

    assert master.address == "Пенза, ул. Карпинского, 33А"
    # И остаётся в raw: колонка — доступ к данным, а не переезд данных.
    assert master.raw["address"] == "Пенза, ул. Карпинского, 33А"


def test_coordinates_from_snapshot_land_in_columns(tenant):
    """Координаты, если источник их прислал, доезжают числами."""

    dto = _dto(location_lat="53.195878", location_lng="45.018316")

    master = _master(tenant, dto)

    assert master.location_lat == Decimal("53.195878")
    assert master.location_lng == Decimal("45.018316")


def test_address_column_is_queryable(tenant):
    """Ради чего колонка и заводилась: по адресу теперь можно искать.

    До DRF-1588 такого запроса не существовало — фильтровать по ключу
    внутри ``raw`` нечем, и «найти мастеров на Карпинского» было не
    вопросом к базе, а перебором в питоне.
    """

    _master(tenant, _dto(address="Пенза, ул. Карпинского, 33А"))
    _master(tenant, _dto(address="Пенза, ул. Московская, 1"))

    found = CatalogMaster.all_tenants.filter(address__icontains="Карпинского")

    assert [m.address for m in found] == ["Пенза, ул. Карпинского, 33А"]


def test_rerun_keeps_geo_stable(tenant):
    """Повторный удар синхронизации не меняет и не теряет гео."""

    dto = _dto(address="Пенза, ул. Карпинского, 33А", location_lat="53.195878")

    _master(tenant, dto)
    master = _master(tenant, dto)

    assert master.address == "Пенза, ул. Карпинского, 33А"
    assert master.location_lat == Decimal("53.195878")


# --------------------------------------------------------------------------
# Половина вторая — отсутствие остаётся отсутствием
#
# Каждый тест ниже краснеет при подстановке значения вместо отсутствия.
# --------------------------------------------------------------------------


def test_missing_address_key_is_absence_not_empty_string(tenant):
    """Ключа ``address`` в слепке нет → ``None``, а НЕ пустая строка.

    Три пилотные строки из 34 именно такие. ``None`` читается как «источник
    ничего не сказал»; пустая строка читалась бы как «источник сказал, что
    адреса нет» — утверждение, которого никто не делал.

    Краснеет от ``row.get("address") or ""``.
    """

    dto = _dto()
    assert "address" not in dto.raw, "предусловие: ключа в слепке нет"

    master = _master(tenant, dto)

    assert master.address is None
    assert master.address != ""


def test_empty_address_value_is_preserved_as_empty(tenant):
    """Ключ есть и нёс пустую строку → в колонке пустая строка, а НЕ ``None``.

    Обратная сторона предыдущего теста, и без неё «отличается» было бы
    словом, а не свойством: пустое значение обязано отличаться от
    отсутствующего В ОБЕ СТОРОНЫ.
    """

    dto = _dto(address="")

    master = _master(tenant, dto)

    assert master.address == ""
    assert master.address is not None


def test_null_coordinates_stay_null_and_never_become_zero(tenant):
    """``None`` у координат остаётся ``None`` и не превращается в ``0.0``.

    На пилоте координаты ``None`` у всех 31 строки с гео-ключами. Пара
    нулей — точка в Гвинейском заливе: она не пустая, она НЕВЕРНАЯ, и на
    карте выглядит как настоящее значение.

    Краснеет от ``or 0`` / ``or 0.0`` в разборе или в переносе.
    """

    dto = _dto(address="Пенза, ул. Карпинского, 33А", location_lat=None, location_lng=None)

    master = _master(tenant, dto)

    assert master.location_lat is None
    assert master.location_lng is None
    assert master.location_lat != Decimal("0")
    assert master.location_lng != Decimal("0")


def test_missing_coordinate_keys_stay_null(tenant):
    """Ключей координат нет вовсе → ``None``, не ``0`` и не исключение."""

    dto = _dto(address="Пенза, ул. Карпинского, 33А")
    assert "location_lat" not in dto.raw

    master = _master(tenant, dto)

    assert master.location_lat is None
    assert master.location_lng is None


def test_explicit_zero_coordinate_is_kept_verbatim(tenant):
    """Явный ноль ОТ ИСТОЧНИКА — значение, и оно сохраняется.

    Запрет касается нашей подстановки, а не чужих данных: если Ayla
    когда-нибудь пришлёт ``0``, зеркало обязано показать ``0``, а не
    переписать источник «из осторожности». Зеркало не редактирует.
    """

    master = _master(tenant, _dto(location_lat="0", location_lng="0"))

    assert master.location_lat == Decimal("0")
    assert master.location_lng == Decimal("0")


def test_address_is_mirrored_verbatim(tenant):
    """Адрес переносится дословно — без ``strip`` и прочей нормализации.

    ``raw`` — сырой слепок чужой системы, и выводить из него что-либо
    сверх лежащего буквально нельзя. Обрезкой занимается читатель, у
    которого есть на это основание.
    """

    master = _master(tenant, _dto(address="  Пенза, ул. Карпинского, 33А  "))

    assert master.address == "  Пенза, ул. Карпинского, 33А  "


# --------------------------------------------------------------------------
# Адрес салона — тот же закон, отдельный ключ
# --------------------------------------------------------------------------


def test_tenant_address_absent_today_leaves_column_untouched(tenant):
    """Сегодняшнее состояние: ключа ``tenant_address`` нет → колонка ``None``.

    Это честное пустое, а не дефект: ключ заводит DRF-1587. Здесь важно
    именно то, что синхронизация НЕ подставила вместо него адрес мастера —
    «первый непустой среди мастеров» это лотерея (OPEN_DECISIONS §45).
    """

    _master(tenant, _dto(address="Пенза, ул. Карпинского, 33А"))

    tenant.refresh_from_db()
    assert tenant.address is None


def test_tenant_address_arrives_without_touching_master_address(tenant):
    """Оба адреса в одной строке доезжают в СВОИ колонки, не смешиваясь.

    Правило старшинства «салон против мастера» — DRF-1589. Задача этой
    колонки — дать этому правилу на чём применяться.
    """

    dto = _dto(
        address="Пенза, ул. Карпинского, 33А",
        tenant_address="Пенза, ул. Московская, 1",
    )

    master = _master(tenant, dto)
    tenant.refresh_from_db()

    assert master.address == "Пенза, ул. Карпинского, 33А"
    assert tenant.address == "Пенза, ул. Московская, 1"


def test_tenant_address_disagreement_writes_nothing(tenant):
    """Строки разошлись в адресе салона → не пишем ничего.

    Выбор одного из двух завёл бы лотерею заново, а записанное значение
    было бы неотличимо от подтверждённого.
    """

    upsert_specialists(
        tenant,
        [
            _dto(tenant_address="Пенза, ул. Московская, 1"),
            _dto(tenant_address="Пенза, ул. Карпинского, 33А"),
        ],
    )

    tenant.refresh_from_db()
    assert tenant.address is None
