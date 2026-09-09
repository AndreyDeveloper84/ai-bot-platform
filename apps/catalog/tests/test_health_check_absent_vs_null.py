"""«Ключа не было» против «прислали null» — для медицинского гейта это разное.

Зеркало несёт третье состояние с DRF-1353: ``MasterService.
resolved_requires_health_check`` объявлено ``null=True``, и гейт брони
читает ``NULL`` как «нужен скрининг». Состояние было, а доехать до него
было нечем: ``row.get(...)`` отдавал питоновский ``None`` и на отсутствие
ключа, и на присланный ``null``, а приёмник — правильно защищаясь от
икоты канала — на ``None`` не писал ничего.

Пока каталог не умел говорить «не знаю», разницы не существовало. Теперь
умеет (услуга без канонической связи), и разница стала ценой гейта.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.catalog.services.http_client import _parse_specialist_service
from apps.catalog.services.upserter import upsert_master_services
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _ts() -> datetime:
    return datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="hc-tristate", name="HC tri-state")


@pytest.fixture
def master(tenant: Tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=1,
        external_updated_at=_ts(),
        name="Мастер",
    )


@pytest.fixture
def service(tenant: Tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=1,
        external_updated_at=_ts(),
        slug="svc-1",
        name="Услуга",
        ayla_service_id=uuid.uuid4(),
    )


def _row(service: CatalogService, master: CatalogMaster, **over) -> dict:
    """Строка в форме, которую отдаёт внутренняя ручка каталога.

    Имена полей взяты у живого сериализатора
    (`services/serializers.py::SpecialistServiceInternalSerializer.Meta.fields`),
    а не придуманы здесь: фикстура, обе половины которой построил один
    автор, проверяет фикстуру, а не стык.
    """
    row = {
        "id": str(uuid.uuid4()),
        "salon_service": str(service.ayla_service_id),
        "specialist": str(master.id),
        "tenant": str(service.tenant_id),
        "user_id": str(uuid.uuid4()),
        "name": "Услуга",
        "category_slug": "manicure",
        "duration_minutes": 60,
        "resolved_duration": 60,
        "requires_health_check": False,
        "price": "1500.00",
        "buffer_after_minutes": 0,
        "is_active": True,
        "updated_at": "2026-09-09T12:00:00+00:00",
    }
    row.update(over)
    return row


# ---------------------------------------------------------------------------
# Разбор: ключ различается от значения
# ---------------------------------------------------------------------------


def test_parser_marks_the_key_as_present_when_null_was_sent(service, master):
    dto = _parse_specialist_service(_row(service, master, resolved_requires_health_check=None))
    assert dto.resolved_requires_health_check is None
    assert dto.health_check_key_present is True


def test_parser_marks_the_key_as_absent_when_it_was_not_sent(service, master):
    row = _row(service, master)
    assert "resolved_requires_health_check" not in row
    dto = _parse_specialist_service(row)
    assert dto.resolved_requires_health_check is None
    assert dto.health_check_key_present is False


# ---------------------------------------------------------------------------
# Запись: ответ пишется, молчание — нет
# ---------------------------------------------------------------------------


def test_explicit_null_downgrades_a_known_false_to_unknown(service, master, tenant):
    """Каталог сказал «не знаю» — зеркало обязано это записать.

    Ровно этот путь и был перекрыт: 96 из 387 рёбер пилота несли `False`,
    сочинённый каскадом за салон, и никакой ответ каталога не мог его
    исправить, потому что «не знаю» было неотличимо от молчания.
    """
    edge_id = str(uuid.uuid4())
    upsert_master_services(
        tenant,
        [
            _parse_specialist_service(
                _row(service, master, id=edge_id, resolved_requires_health_check=False)
            )
        ],
    )
    row = MasterService.all_tenants.get(ayla_specialist_service_id=edge_id)
    assert row.resolved_requires_health_check is False

    upsert_master_services(
        tenant,
        [
            _parse_specialist_service(
                _row(service, master, id=edge_id, resolved_requires_health_check=None)
            )
        ],
    )
    row.refresh_from_db()
    assert row.resolved_requires_health_check is None


def test_a_missing_key_still_keeps_what_the_mirror_knows(service, master, tenant):
    """Положительная стража: защита от икоты канала НЕ снята.

    Без неё правка выше «зеленела» бы и на коде, который пишет `None`
    всегда, — то есть на возврате к мигающему гейту, от которого
    DRF-1353 и защищался.
    """
    edge_id = str(uuid.uuid4())
    upsert_master_services(
        tenant,
        [
            _parse_specialist_service(
                _row(service, master, id=edge_id, resolved_requires_health_check=True)
            )
        ],
    )
    row = MasterService.all_tenants.get(ayla_specialist_service_id=edge_id)
    assert row.resolved_requires_health_check is True

    # Выгрузка без поля — «эта строка про здоровье не говорит».
    upsert_master_services(
        tenant,
        [_parse_specialist_service(_row(service, master, id=edge_id))],
    )
    row.refresh_from_db()
    assert row.resolved_requires_health_check is True


def test_an_explicit_false_still_overwrites_a_stale_true(service, master, tenant):
    """Вторая половина стражи: `False` — намеренный ответ, а не умолчание."""
    edge_id = str(uuid.uuid4())
    upsert_master_services(
        tenant,
        [
            _parse_specialist_service(
                _row(service, master, id=edge_id, resolved_requires_health_check=True)
            )
        ],
    )
    upsert_master_services(
        tenant,
        [
            _parse_specialist_service(
                _row(service, master, id=edge_id, resolved_requires_health_check=False)
            )
        ],
    )
    row = MasterService.all_tenants.get(ayla_specialist_service_id=edge_id)
    assert row.resolved_requires_health_check is False
