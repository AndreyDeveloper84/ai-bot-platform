"""Консьерж не предлагает непродаваемое ребро (DRF-1964a).

Ребро с ``sellable=false`` остаётся в зеркале (у него есть причина), но
поиск мастеров по услуге, мастера услуги и витрина его не видят: предлагать
то, что запись отклонит 422, — тупик, который владелец запретил (раздел Q).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from django.conf import settings

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.marketplace.discovery import discover_masters, discover_masters_for_service
from apps.tenancy.models import Tenant

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        "postgresql" not in str(settings.DATABASES["default"]["ENGINE"]),
        reason="Cyrillic ILIKE folding requires Postgres; negatives would pass vacuously on SQLite.",
    ),
]

_TS = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def penza() -> Tenant:
    return Tenant.objects.create(slug="sell-penza", name="Sell Penza", city="Пенза")


def _master(tenant: Tenant, name: str) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant, external_updated_at=_TS, name=name, specialization="",
        is_active=True, invite_status=CatalogMaster.InviteStatus.ACCEPTED, ayla_user_id=uuid4(),
    )


def _service(tenant: Tenant, name: str, slug: str) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant, slug=slug, name=name, is_active=True, ayla_service_id=uuid4(), external_updated_at=_TS,
    )


def _has_column() -> bool:
    return {"sellable", "unsellable_reason"} <= {f.name for f in MasterService._meta.get_fields()}


def _link(tenant: Tenant, master: CatalogMaster, service: CatalogService) -> MasterService:
    return MasterService.all_tenants.create(tenant=tenant, master=master, service=service)


def _unsellable(edge: MasterService) -> None:
    assert _has_column(), "у MasterService нет колонок sellable / unsellable_reason"
    MasterService.all_tenants.filter(pk=edge.pk).update(sellable=False, unsellable_reason="price_below_minimum")


def test_master_whose_only_matching_edge_is_unsellable_is_not_found(penza):
    master = _master(penza, "Массажист")
    _unsellable(_link(penza, master, _service(penza, "Массаж шейно-воротниковой зоны", "neck")))

    cards = discover_masters(city="Пенза", specialization="массаж шейно-воротниковой зоны")

    assert cards == []


def test_master_is_still_found_for_the_sellable_service(penza):
    """Положительная стража: фильтр режет ребро, а не мастера целиком."""
    master = _master(penza, "Массажист")
    _link(penza, master, _service(penza, "Спортивный массаж", "sport"))
    neck = _link(penza, master, _service(penza, "Массаж шейно-воротниковой зоны", "neck"))
    if _has_column():
        MasterService.all_tenants.filter(pk=neck.pk).update(sellable=False, unsellable_reason="price_below_minimum")

    cards = discover_masters(city="Пенза", specialization="спортивный массаж")

    assert {c.name for c in cards} == {"Массажист"}


def test_masters_for_service_excludes_unsellable_edge(penza):
    neck = _service(penza, "Массаж шейно-воротниковой зоны", "neck")
    sold = _master(penza, "Продаёт")
    _link(penza, sold, neck)
    _unsellable(_link(penza, _master(penza, "Ноль рублей"), neck))

    cards = discover_masters_for_service(neck.id)

    assert {c.name for c in cards} == {"Продаёт"}
