"""Цена и длительность соло-услуги доходят до зеркала через ребро мастер↔услуга.

Прогон настройки соло 11.11.2026: мастер ставит 1 500 ₽ / 60 мин на экране
«Услуги и цены» — каталог пишет это на ``SpecialistService`` (ребро), а
``SalonService.base_price`` у соло пуст. Зеркало ``CatalogService.price_from``
читалось только из ``salon-services.base_price`` → у опубликованной соло
все услуги без цены, клиентские списки бота без «от … ₽», котировка —
«точную цену озвучит администратор».

Правило (``derive_service_prices_from_edges``): фид назвал цену — она и
остаётся; не назвал — ``price_from`` = минимум по продаваемым рёбрам синка
(«от»), длительность — так же; рёбер нет — ``None``.

Узлы:

* p1 — парсер ребра читает ``price`` / ``duration_minutes`` / ``resolved_duration``;
* p2 — одно ребро 1600/30 на услуге без цены в фиде → ``price_from`` 1600, ``duration_min`` 30;
  ребро хранит свои цену и длительность;
* p3 — два мастера 2800 и 1600 → «от 1600», длительность — меньшая;
* p4 — фид назвал ``base_price`` → рёбра её не трогают;
* p5 — ребро непродаваемое (0 ₽) → цены нет;
* p6 — ребро ушло из снимка → цена снята (``None``), повтор ничего не меняет;
* p7 — операторское ребро (без ``ayla_specialist_service_id``) в расчёт не входит;
* p8 — такт ``salon-services`` с пустой ценой в фиде выведенную цену не обнуляет
  (ею владеет вывод из рёбер — без окна «без цены» между транзакциями);
* p9 — ``base_price: ""`` в фиде — это «цены нет», а не «цена есть»: выводится из ребра;
* p10 — нечитаемая цена ребра («от 1500») не стоит ребра: ребро зеркалится без цены;
* p11 — услуга без ``ayla_service_id`` (заведена руками) выводом не трогается.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.catalog.provenance import MasterServiceSource, master_service_write
from apps.catalog.services.http_client import (
    CatalogSalonServiceDTO,
    CatalogSpecialistServiceDTO,
    _parse_specialist_service,
)
from apps.catalog.services.upserter import upsert_master_services, upsert_salon_services
from apps.tenancy.models import Tenant
from tests.support.catalog_mirror import sync_shaped

pytestmark = pytest.mark.django_db

_TS = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="solo-max-edgeprice", name="Студия", city="Пенза")


def _master(tenant: Tenant, name: str = "Тихонова Ольга") -> CatalogMaster:
    return sync_shaped(
        CatalogMaster.all_tenants.create(
            tenant=tenant, id=uuid.uuid4(), name=name, external_updated_at=_TS
        )
    )


def _service(tenant: Tenant, *, raw: dict[str, Any] | None = None, **fields: Any) -> CatalogService:
    """Строка зеркала как после ``upsert_salon_services``: ``raw`` — строка фида."""
    return CatalogService.all_tenants.create(
        tenant=tenant,
        ayla_service_id=uuid.uuid4(),
        slug=f"s-{uuid.uuid4().hex[:6]}",
        name="Массаж спины",
        external_updated_at=_TS,
        raw={
            "base_price": None,
            "duration_minutes": None,
            "resolved_duration": None,
            **(raw or {}),
        },
        **fields,
    )


def _row(service: CatalogService, master: CatalogMaster, **over: Any) -> dict[str, Any]:
    """Строка живого сериализатора ``specialist-services`` (поля — из каталога)."""
    row: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "salon_service": str(service.ayla_service_id),
        "specialist": str(master.id),
        "tenant": str(service.tenant_id),
        "user_id": str(uuid.uuid4()),
        "name": service.name,
        "category_slug": "massage",
        "price": "1600.00",
        "duration_minutes": 30,
        "resolved_duration": 30,
        "is_active": True,
        "sellable": True,
        "unsellable_reason": None,
        "updated_at": "2026-11-11T12:00:00+00:00",
    }
    row.update(over)
    return row


def _dto(
    service: CatalogService, master: CatalogMaster, **over: Any
) -> CatalogSpecialistServiceDTO:
    return _parse_specialist_service(_row(service, master, **over))


def test_p1_the_edge_parser_reads_price_and_durations(tenant: Tenant) -> None:
    master = _master(tenant)
    service = _service(tenant)

    dto = _dto(service, master, price="1600.00", duration_minutes=45, resolved_duration=30)

    assert dto.price == Decimal("1600.00")
    assert dto.duration_minutes == 45
    assert dto.resolved_duration == 30
    assert dto.effective_duration == 30  # разрешённая каскадом — главнее своей

    bare = _dto(service, master, price=None, duration_minutes=None, resolved_duration=None)
    assert bare.price is None
    assert bare.effective_duration is None


def test_p2_a_solo_edge_prices_the_feedless_service(tenant: Tenant) -> None:
    """Красный до правки: ``price_from``/``duration_min`` оставались ``None``."""
    master = _master(tenant)
    service = _service(tenant)

    res = upsert_master_services(tenant, [_dto(service, master)])

    assert res.created == 1
    service.refresh_from_db()
    assert service.price_from == Decimal("1600.00")
    assert service.duration_min == 30
    edge = MasterService.all_tenants.get(service=service, master=master)
    assert edge.price == Decimal("1600.00")
    assert edge.duration_min == 30


def test_p3_several_masters_give_the_lowest_price_and_duration(tenant: Tenant) -> None:
    olga, vera = _master(tenant, "Ольга"), _master(tenant, "Вера")
    service = _service(tenant)

    upsert_master_services(
        tenant,
        [
            _dto(service, olga, price="2800.00", duration_minutes=60, resolved_duration=60),
            _dto(service, vera, price="1600.00", duration_minutes=45, resolved_duration=45),
        ],
    )

    service.refresh_from_db()
    assert service.price_from == Decimal("1600.00")
    assert service.duration_min == 45


def test_p4_a_feed_price_is_never_overridden_by_edges(tenant: Tenant) -> None:
    master = _master(tenant)
    service = _service(
        tenant,
        raw={"base_price": "1000.00", "resolved_duration": 90},
        price_from=Decimal("1000.00"),
        duration_min=90,
    )

    upsert_master_services(tenant, [_dto(service, master, price="1600.00", resolved_duration=30)])

    service.refresh_from_db()
    assert service.price_from == Decimal("1000.00")
    assert service.duration_min == 90


def test_p5_an_unsellable_edge_gives_no_price(tenant: Tenant) -> None:
    master = _master(tenant)
    service = _service(tenant)

    upsert_master_services(
        tenant,
        [
            _dto(
                service,
                master,
                price="0.00",
                sellable=False,
                unsellable_reason="price_below_minimum",
            )
        ],
    )

    service.refresh_from_db()
    assert service.price_from is None
    assert service.duration_min == 30  # длительность ребро знает и без цены


def test_p6_an_edge_gone_from_the_snapshot_takes_the_price_with_it(tenant: Tenant) -> None:
    master = _master(tenant)
    service = _service(tenant)
    other = _service(tenant)
    upsert_master_services(tenant, [_dto(service, master), _dto(other, master, price="900.00")])
    service.refresh_from_db()
    assert service.price_from == Decimal("1600.00")

    # Следующий снимок: ребро на «Массаж спины» пропало, второе осталось.
    upsert_master_services(tenant, [_dto(other, master, price="900.00")])

    service.refresh_from_db()
    other.refresh_from_db()
    assert service.price_from is None
    assert service.duration_min is None
    assert other.price_from == Decimal("900.00")

    before = (service.price_from, service.duration_min, other.price_from, other.duration_min)
    upsert_master_services(tenant, [_dto(other, master, price="900.00")])
    service.refresh_from_db()
    other.refresh_from_db()
    assert (
        service.price_from,
        service.duration_min,
        other.price_from,
        other.duration_min,
    ) == before


def test_p7_an_operator_edge_carries_no_price_and_is_ignored(tenant: Tenant) -> None:
    master = _master(tenant)
    service = _service(tenant)
    with master_service_write(
        MasterServiceSource.MANUAL_SCRIPT, reason="test p7: operator-shaped edge"
    ):
        MasterService.all_tenants.create(tenant=tenant, master=master, service=service)

    res = upsert_master_services(tenant, [], reconcile=False)

    assert res.errors == []
    service.refresh_from_db()
    assert service.price_from is None
    assert MasterService.all_tenants.get(service=service, master=master).price is None


def _salon_dto(service: CatalogService, **over: Any) -> CatalogSalonServiceDTO:
    """Строка фида ``salon-services`` соло: цены и длительности фид не знает."""
    row = {
        "id": str(service.ayla_service_id),
        "tenant": str(service.tenant_id),
        "name": service.name,
        "is_active": True,
        "base_price": None,
        "duration_minutes": None,
        "resolved_duration": None,
        "goals": [],
        "updated_at": "2026-11-11T12:00:00+00:00",
        **over,
    }
    return CatalogSalonServiceDTO(
        ayla_service_id=str(service.ayla_service_id),
        external_updated_at=_TS,
        name=service.name,
        is_active=True,
        price_from=None if row["base_price"] in (None, "") else Decimal(str(row["base_price"])),
        duration_min=row["resolved_duration"],
        goals=[],
        raw=row,
    )


def test_p8_the_salon_services_beat_keeps_the_derived_price(tenant: Tenant) -> None:
    master = _master(tenant)
    service = _service(tenant)
    upsert_master_services(tenant, [_dto(service, master)])
    service.refresh_from_db()
    assert service.price_from == Decimal("1600.00")

    res = upsert_salon_services(tenant, [_salon_dto(service)])

    assert res.errors == []
    service.refresh_from_db()
    assert service.price_from == Decimal("1600.00")
    assert service.duration_min == 30


def test_p9_an_empty_feed_price_counts_as_unknown(tenant: Tenant) -> None:
    master = _master(tenant)
    service = _service(tenant, raw={"base_price": ""})

    upsert_master_services(tenant, [_dto(service, master)])

    service.refresh_from_db()
    assert service.price_from == Decimal("1600.00")


def test_p10_an_unreadable_edge_price_does_not_cost_the_edge(tenant: Tenant) -> None:
    master = _master(tenant)
    service = _service(tenant)

    dto = _dto(service, master, price="от 1500", duration_minutes="долго")

    assert dto.price is None
    assert dto.duration_minutes is None
    res = upsert_master_services(tenant, [dto])
    assert res.created == 1
    assert res.errors == []
    assert MasterService.all_tenants.filter(service=service, master=master).exists()
    service.refresh_from_db()
    assert service.price_from is None
    assert service.duration_min == 30  # resolved_duration читается отдельно


def test_p11_a_hand_made_service_without_feed_id_is_left_alone(tenant: Tenant) -> None:
    master = _master(tenant)
    hand_made = CatalogService.all_tenants.create(
        tenant=tenant,
        slug="hand-made",
        name="Своя услуга",
        external_updated_at=_TS,
        price_from=Decimal("700.00"),
        duration_min=20,
    )
    service = _service(tenant)

    upsert_master_services(tenant, [_dto(service, master)])

    hand_made.refresh_from_db()
    assert hand_made.price_from == Decimal("700.00")
    assert hand_made.duration_min == 20
