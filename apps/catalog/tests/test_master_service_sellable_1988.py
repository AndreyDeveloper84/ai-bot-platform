"""Непродаваемое ребро в зеркале: колонка, разбор, апсертер, предикат (DRF-1964a).

Решение владельца (раздел Q, «цена 0 ₽ не продажная»): ребро каталога с
ценой ниже 1 ₽ — ``sellable=false`` + ``unsellable_reason``, и оно остаётся
в выдаче каталога (контракт DRF-1962, ``SpecialistServiceInternalSerializer``).
Бот обязан читать ``sellable``, а не выводить продаваемость из ``is_active``
или цены.

У зеркала ``MasterService`` «строка есть = предлагается» — статусной
колонки не было намеренно. Здесь колонка появляется, и ровно поэтому она
приходит вместе с census-сторожем читателей
(``test_master_service_sellable_census.py``).

Порядок выкладки: бот-половина раньше каталога. Ответ каталога без ключа
``sellable`` не меняет в зеркале ничего — это закреплено
``test_absent_sellable_key_changes_nothing`` (свидетель безопасного порядка).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.forms.models import model_to_dict

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.catalog.services.http_client import CatalogSpecialistServiceDTO, _parse_specialist_service
from apps.catalog.services.upserter import upsert_master_services
from apps.tenancy.models import Tenant
from tests.support.catalog_mirror import sync_shaped

pytestmark = pytest.mark.django_db

_TS = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
_ABSENT = object()


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="sellable-1988", name="Sellable 1988", city="Пенза")


def _master(tenant: Tenant, name: str = "Мастер") -> CatalogMaster:
    return sync_shaped(
        CatalogMaster.all_tenants.create(
            tenant=tenant, id=uuid.uuid4(), name=name, external_updated_at=_TS,
        )
    )


def _service(tenant: Tenant, name: str = "Массаж шейно-воротниковой зоны", slug: str = "neck") -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant, ayla_service_id=uuid.uuid4(), slug=slug, name=name, external_updated_at=_TS,
    )


def _has_column() -> bool:
    names = {f.name for f in MasterService._meta.get_fields()}
    return {"sellable", "unsellable_reason"} <= names


def _require_column() -> None:
    assert _has_column(), "у MasterService нет колонок sellable / unsellable_reason"


def _dto(
    master: CatalogMaster,
    service: CatalogService,
    *,
    edge_id: str,
    sellable: Any = _ABSENT,
    reason: str | None = None,
) -> CatalogSpecialistServiceDTO:
    kwargs: dict[str, Any] = {}
    if sellable is not _ABSENT:
        fields = CatalogSpecialistServiceDTO.__dataclass_fields__
        assert {"sellable", "unsellable_reason", "sellable_key_present"} <= set(fields), (
            "в CatalogSpecialistServiceDTO нет sellable / unsellable_reason / sellable_key_present"
        )
        kwargs = {"sellable": sellable, "unsellable_reason": reason, "sellable_key_present": True}
    return CatalogSpecialistServiceDTO(
        ayla_specialist_service_id=edge_id,
        salon_service=str(service.ayla_service_id),
        specialist=str(master.id),
        external_updated_at=_TS,
        tenant=str(master.tenant_id),
        is_active=True,
        **kwargs,
    )


def _row(service: CatalogService, master: CatalogMaster, **over: Any) -> dict[str, Any]:
    """Строка в форме живого сериализатора каталога (поля — из #477)."""
    row: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "salon_service": str(service.ayla_service_id),
        "specialist": str(master.id),
        "tenant": str(service.tenant_id),
        "user_id": str(uuid.uuid4()),
        "name": service.name,
        "category_slug": "massage",
        "price": "0.00",
        "is_active": True,
        "updated_at": "2026-09-15T12:00:00+00:00",
    }
    row.update(over)
    return row


# ── разбор ───────────────────────────────────────────────────────


def test_parser_reads_unsellable_edge_with_reason(tenant):
    m, s = _master(tenant), _service(tenant)
    dto = _parse_specialist_service(_row(s, m, sellable=False, unsellable_reason="price_below_minimum"))
    assert getattr(dto, "sellable", None) is False, "разбор не читает sellable"
    assert dto.unsellable_reason == "price_below_minimum"
    assert dto.sellable_key_present is True


def test_parser_marks_sellable_key_absent(tenant):
    m, s = _master(tenant), _service(tenant)
    row = _row(s, m)
    assert row["id"] and "sellable" not in row  # строка не пуста, просто без ключа
    dto = _parse_specialist_service(row)
    assert getattr(dto, "sellable_key_present", None) is False, "разбор не различает отсутствие ключа sellable"
    assert dto.sellable is True


def test_parser_maps_unknown_reason_to_unknown_and_stays_unsellable(tenant):
    m, s = _master(tenant), _service(tenant)
    dto = _parse_specialist_service(_row(s, m, sellable=False, unsellable_reason="что-то новое"))
    assert getattr(dto, "sellable", None) is False, "разбор не читает sellable"
    assert dto.unsellable_reason == "unknown"


# ── модель ───────────────────────────────────────────────────────


def test_unsellable_without_reason_is_refused_by_the_database(tenant):
    _require_column()
    m, s = _master(tenant), _service(tenant)
    with pytest.raises(IntegrityError), transaction.atomic():
        MasterService.all_tenants.create(tenant=tenant, master=m, service=s, sellable=False, unsellable_reason="")


def test_sellable_with_reason_is_refused_by_the_database(tenant):
    _require_column()
    m, s = _master(tenant), _service(tenant)
    with pytest.raises(IntegrityError), transaction.atomic():
        MasterService.all_tenants.create(
            tenant=tenant, master=m, service=s, sellable=True, unsellable_reason="price_below_minimum",
        )


# ── апсертер ─────────────────────────────────────────────────────


def test_create_stores_unsellable_and_reason(tenant):
    m, s = _master(tenant), _service(tenant)
    upsert_master_services(tenant, [_dto(m, s, edge_id=str(uuid.uuid4()), sellable=False, reason="price_below_minimum")])
    _require_column()
    row = MasterService.all_tenants.get(tenant=tenant, master=m, service=s)
    assert (row.sellable, row.unsellable_reason) == (False, "price_below_minimum")


def test_update_to_unsellable_writes_only_changed_fields(tenant):
    m, s = _master(tenant), _service(tenant)
    edge = str(uuid.uuid4())
    upsert_master_services(tenant, [_dto(m, s, edge_id=edge, sellable=True)])
    _require_column()
    before = MasterService.all_tenants.get(tenant=tenant, master=m, service=s)

    upsert_master_services(tenant, [_dto(m, s, edge_id=edge, sellable=False, reason="price_below_minimum")])

    after = MasterService.all_tenants.get(pk=before.pk)
    assert (after.sellable, after.unsellable_reason) == (False, "price_below_minimum")
    assert after.resolved_requires_health_check == before.resolved_requires_health_check
    assert after.updated_at > before.updated_at


def test_absent_sellable_key_changes_nothing(tenant):
    """Свидетель порядка выкладки: каталог без #477 ключ не шлёт — зеркало байт в байт прежнее."""
    m, s = _master(tenant), _service(tenant)
    edge = str(uuid.uuid4())
    upsert_master_services(tenant, [_dto(m, s, edge_id=edge, sellable=False, reason="price_below_minimum")])
    _require_column()
    before = model_to_dict(MasterService.all_tenants.get(tenant=tenant, master=m, service=s))
    before_updated = MasterService.all_tenants.get(tenant=tenant, master=m, service=s).updated_at

    upsert_master_services(tenant, [_dto(m, s, edge_id=edge)])  # ключа sellable нет

    row = MasterService.all_tenants.get(tenant=tenant, master=m, service=s)
    assert model_to_dict(row) == before
    assert row.updated_at == before_updated

    fresh_m, fresh_s = _master(tenant, "Новый"), _service(tenant, "Новая услуга", "new")
    upsert_master_services(tenant, [_dto(fresh_m, fresh_s, edge_id=str(uuid.uuid4()))])
    fresh = MasterService.all_tenants.get(tenant=tenant, master=fresh_m, service=fresh_s)
    assert (fresh.sellable, fresh.unsellable_reason) == (True, "")


def test_back_to_sellable_clears_reason(tenant):
    m, s = _master(tenant), _service(tenant)
    edge = str(uuid.uuid4())
    upsert_master_services(tenant, [_dto(m, s, edge_id=edge, sellable=False, reason="price_below_minimum")])
    upsert_master_services(tenant, [_dto(m, s, edge_id=edge, sellable=True)])
    _require_column()
    row = MasterService.all_tenants.get(tenant=tenant, master=m, service=s)
    assert (row.sellable, row.unsellable_reason) == (True, "")


def test_operator_row_is_not_touched_by_unsellable_edge(tenant):
    _require_column()
    m, s = _master(tenant), _service(tenant)
    MasterService.all_tenants.create(tenant=tenant, master=m, service=s)

    upsert_master_services(tenant, [_dto(m, s, edge_id=str(uuid.uuid4()), sellable=False, reason="price_below_minimum")])

    row = MasterService.all_tenants.get(tenant=tenant, master=m, service=s)
    assert row.ayla_specialist_service_id is None
    assert (row.sellable, row.unsellable_reason) == (True, "")


# ── предикат ─────────────────────────────────────────────────────


def test_sellable_queryset_excludes_unsellable_edges(tenant):
    _require_column()
    assert hasattr(MasterService.all_tenants.all(), "sellable"), "нет MasterServiceQuerySet.sellable()"
    m = _master(tenant)
    good, bad = _service(tenant, "Спина", "back"), _service(tenant)
    MasterService.all_tenants.create(tenant=tenant, master=m, service=good)
    MasterService.all_tenants.create(
        tenant=tenant, master=m, service=bad, sellable=False, unsellable_reason="price_below_minimum",
    )

    ids = set(MasterService.all_tenants.filter(tenant=tenant).sellable().values_list("service_id", flat=True))

    assert ids == {good.id}
