"""Синхронизация пишет id профиля каталога в колонку зеркала (DRF-1933, часть 1).

Колонка ``CatalogMaster.catalog_specialist_id`` — одно представление факта
«каким id эту строку знает каталог». Синк знает его всегда
(``dto.ayla_master_id``) и пишет:

* на новую строку — там он совпадает с первичным ключом;
* на строку приглашения, которую синк нашёл склейкой DRF-1507 по
  ``ayla_user_id``, — там первичный ключ остаётся ``uuid4``, и колонка —
  единственное место, где id каталога виден без разбора ``raw``.

Красный до правки: колонки нет — ``getattr`` возвращает ``None``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from apps.catalog.models import CatalogMaster
from apps.catalog.services.http_client import CatalogSpecialistDTO
from apps.catalog.services.upserter import upsert_specialists
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="catalog-id-1933", name="Catalog Id 1933")


def _dto(mid: str, *, user_id: str) -> CatalogSpecialistDTO:
    return CatalogSpecialistDTO(
        ayla_master_id=mid,
        user_id=user_id,
        name="Анна Иванова",
        external_updated_at=datetime(2026, 9, 15, 7, 0, tzinfo=timezone.utc),
        tenant=None,
        raw={"id": mid},
    )


def test_a_row_created_by_sync_carries_its_catalog_id(tenant: Tenant):
    mid = str(uuid.uuid4())

    upsert_specialists(tenant, [_dto(mid, user_id=str(uuid.uuid4()))])

    row = CatalogMaster.all_tenants.get(pk=mid)
    assert str(getattr(row, "catalog_specialist_id", None)) == mid


def test_an_invite_row_glued_by_sync_gets_the_catalog_id_and_keeps_its_pk(tenant: Tenant):
    user_id = str(uuid.uuid4())
    invite_row = CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=1933,
        external_updated_at=datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc),
        name="Анна (приглашение)",
        ayla_user_id=user_id,
    )
    canonical = str(uuid.uuid4())

    upsert_specialists(tenant, [_dto(canonical, user_id=user_id)])

    invite_row.refresh_from_db()
    assert not CatalogMaster.all_tenants.filter(pk=canonical).exists()  # второй строки нет
    assert str(invite_row.pk) != canonical
    assert str(getattr(invite_row, "catalog_specialist_id", None)) == canonical
