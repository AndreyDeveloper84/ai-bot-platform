"""Входящие: id каталога находит строку зеркала по колонке (DRF-1933, часть 2).

События и записи каталога называют мастера ``SpecialistProfile.id``. У
склеенного приглашения и соло-мастера первичный ключ зеркала другой, а
``ayla_user_id`` — это id *пользователя*, не профиля. До правки:

* ``booking/master_notify.py::resolve_master`` искал ``id | ayla_user_id`` —
  мастер не получал уведомлений, клиенту уходило «Мастер: —»;
* ``miniapp_api/views.py::_proxy_catalog_refs`` искал ``id`` — карточка записи
  и отзыв оставались без мастера.

Красный до правки: склеенная строка по id каталога. Зелёный в обе стороны:
строка синка и поиск по ``ayla_user_id``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from apps.booking.master_notify import resolve_master
from apps.catalog.models import CatalogMaster
from apps.miniapp_api.views import _proxy_catalog_refs
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="inbound-1933", name="Inbound 1933")


def _row(tenant: Tenant, *, column: uuid.UUID | None, user_id: uuid.UUID | None = None):
    row = CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=CatalogMaster.all_tenants.filter(tenant=tenant).count() + 1,
        external_updated_at=datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc),
        name="Анна",
        ayla_user_id=user_id,
    )
    CatalogMaster.all_tenants.filter(pk=row.pk).update(catalog_specialist_id=column)
    return row


class TestResolveMaster:
    def test_glued_row_is_found_by_the_catalog_id(self, tenant):
        catalog_id = uuid.uuid4()
        row = _row(tenant, column=catalog_id, user_id=uuid.uuid4())

        found = resolve_master(tenant=tenant, specialist_id=catalog_id)

        assert found is not None and found.pk == row.pk

    def test_synced_row_is_still_found(self, tenant):
        row = _row(tenant, column=None)
        CatalogMaster.all_tenants.filter(pk=row.pk).update(catalog_specialist_id=row.pk)

        found = resolve_master(tenant=tenant, specialist_id=row.pk)

        assert found is not None and found.pk == row.pk

    def test_the_user_id_key_still_resolves(self, tenant):
        user_id = uuid.uuid4()
        row = _row(tenant, column=uuid.uuid4(), user_id=user_id)

        found = resolve_master(tenant=tenant, specialist_id=user_id)

        assert found is not None and found.pk == row.pk


class TestProxyCatalogRefs:
    def test_glued_row_is_the_master_of_the_booking(self, tenant):
        catalog_id = uuid.uuid4()
        row = _row(tenant, column=catalog_id)

        with tenant_scope(tenant):
            _service, master = _proxy_catalog_refs(
                SimpleNamespace(service_id=None, specialist_id=catalog_id)
            )

        assert master is not None and master.pk == row.pk

    def test_synced_row_is_still_the_master_of_the_booking(self, tenant):
        row = _row(tenant, column=None)
        CatalogMaster.all_tenants.filter(pk=row.pk).update(catalog_specialist_id=row.pk)

        with tenant_scope(tenant):
            _service, master = _proxy_catalog_refs(
                SimpleNamespace(service_id=None, specialist_id=row.pk)
            )

        assert master is not None and master.pk == row.pk
