"""DRF-1308 — цели доезжают из Ayla в зеркало каталога и в текст KB.

До этой задачи `catalog_catalogservice.goals` было `[]` у **всех** строк
контура: у поля не было источника — `_service_fields` его не заполняло,
а разрешить дерево категорий на этой стороне невозможно, потому что
таблицы категорий здесь нет вовсе. Ayla теперь отдаёт цели уже
разрешёнными, зеркало их только переносит (ADR-0009).

Проверяется:
- парсер payload'а: форма, устойчивость к мусору, отсутствие поля;
- upsert пишет цели в колонку и **перезаписывает** их при отзыве;
- проекция в KB печатает человекочитаемую подпись, а не ключ.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from apps.catalog.models import CatalogService
from apps.catalog.services.http_client import (
    CatalogSalonServiceDTO,
    _parse_salon_service,
)
from apps.catalog.services.upserter import upsert_salon_services
from apps.kb.projectors import service_to_body
from apps.tenancy.models import Tenant

RELAX = {"key": "relax", "label": "Расслабиться и снять стресс"}
SELF_CARE = {"key": "self_care", "label": "Привести себя в порядок"}


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="drf1308", name="DRF-1308")


_UNSET: Any = object()


def _row(goals: Any = _UNSET) -> dict[str, Any]:
    """Одна строка ответа Ayla. `goals` опускается через ``_UNSET``.

    Отличать «поле отсутствует» от «поле есть и равно None» нужно
    буквально: это два разных случая, и парсер обязан пережить оба.
    """
    row: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "updated_at": "2026-08-23T09:00:00Z",
        "name": "Массаж спины",
        "is_active": True,
        "requires_health_check": False,
        "base_price": "1500.00",
        "duration_minutes": 60,
        "template": None,
        "category": str(uuid.uuid4()),
    }
    if goals is not _UNSET:
        row["goals"] = goals
    return row


def _dto(
    *,
    ayla_service_id: str | None = None,
    goals: list[dict[str, str]] | None = None,
) -> CatalogSalonServiceDTO:
    return CatalogSalonServiceDTO(
        ayla_service_id=ayla_service_id or str(uuid.uuid4()),
        external_updated_at=datetime(2026, 8, 23, 9, 0, tzinfo=timezone.utc),
        name="Массаж спины",
        price_from=Decimal("1500.00"),
        duration_min=60,
        goals=[RELAX] if goals is None else goals,
        raw={},
    )


class TestPayloadParsing:
    def test_goals_are_carried_from_the_payload(self) -> None:
        dto = _parse_salon_service(_row(goals=[RELAX, SELF_CARE]))
        assert dto.goals == [RELAX, SELF_CARE]

    def test_missing_field_is_an_empty_list_not_a_crash(self) -> None:
        """Поле аддитивное: старая Ayla его просто не пришлёт."""
        row = _row()
        assert "goals" not in row
        assert _parse_salon_service(row).goals == []

    @pytest.mark.parametrize(
        "raw_goals",
        [None, "relax", {}, [None], ["relax"], [{"key": "relax"}], [{"label": ""}]],
    )
    def test_malformed_entries_are_dropped_not_raised(self, raw_goals: Any) -> None:
        """Цель — обогащение; из-за неё нельзя терять всю услугу."""
        assert _parse_salon_service(_row(goals=raw_goals)).goals == []

    def test_good_entries_survive_alongside_bad_ones(self) -> None:
        dto = _parse_salon_service(_row(goals=[RELAX, "мусор", {"key": "x"}]))
        assert dto.goals == [RELAX]


@pytest.mark.django_db
class TestMirrorColumn:
    def test_upsert_writes_goals(self, tenant: Tenant) -> None:
        aid = str(uuid.uuid4())
        upsert_salon_services(tenant, [_dto(ayla_service_id=aid)])
        svc = CatalogService.all_tenants.get(tenant=tenant, ayla_service_id=aid)
        assert svc.goals == [RELAX]

    def test_retracted_goal_is_removed_on_next_sync(self, tenant: Tenant) -> None:
        """Цели курирует Ayla — снятая связь должна исчезнуть и в зеркале.

        Если бы sync только дополнял, отозванная владельцем цель осталась
        бы здесь навсегда и продолжала бы попадать в выдачу.
        """
        aid = str(uuid.uuid4())
        upsert_salon_services(tenant, [_dto(ayla_service_id=aid)])
        upsert_salon_services(tenant, [_dto(ayla_service_id=aid, goals=[])])
        svc = CatalogService.all_tenants.get(tenant=tenant, ayla_service_id=aid)
        assert svc.goals == []


@pytest.mark.django_db
class TestKbProjection:
    def _service(self, tenant: Tenant, goals: Any) -> CatalogService:
        return CatalogService.all_tenants.create(
            tenant=tenant,
            external_updated_at=datetime(2026, 8, 23, 9, 0, tzinfo=timezone.utc),
            slug="massazh-spiny",
            name="Массаж спины",
            goals=goals,
        )

    def test_body_shows_the_label_not_the_key(self, tenant: Tenant) -> None:
        """В теле лежит текст, который модель цитирует человеку.

        «Подходит: relax» — это утёкший внутренний ключ, а не подсказка.
        """
        body = service_to_body(self._service(tenant, [RELAX]))
        assert "Подходит: Расслабиться и снять стресс" in body
        assert "relax" not in body

    def test_several_goals_are_listed(self, tenant: Tenant) -> None:
        body = service_to_body(self._service(tenant, [RELAX, SELF_CARE]))
        assert "Подходит: Расслабиться и снять стресс, Привести себя в порядок" in body

    def test_legacy_string_shape_still_renders(self, tenant: Tenant) -> None:
        """Строки, лежащие в зеркале до первой пересинхронизации."""
        body = service_to_body(self._service(tenant, ["relax"]))
        assert "Подходит: relax" in body

    def test_empty_goals_add_no_line(self, tenant: Tenant) -> None:
        assert "Подходит" not in service_to_body(self._service(tenant, []))
