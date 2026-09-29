"""Колбэк мастера из консьержа: внутри бота — pk зеркала, в каталог — его id (DRF-1933, часть 2).

``orchestrator/handoff.py`` кладёт pk карточки в ``native_master_id``, колбэк
``book:pick_master:<pk>:<service>`` ведёт в ``_render_date_picker``. Условия
главного окна 15.09:

* локальные поиски по-прежнему находят склеенную/соло строку по pk — ребро
  ворот здоровья (``_resolved_health_check_for_edge``) и кнопки выбора даты
  несут pk;
* в каталог уходит ``catalog_specialist_id`` — даты и котировка превью.

Красный до правки: даты и котировка уходят с pk. Зелёный в обе стороны:
ребро по pk и pk в кнопках.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, cast

import pytest

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.skills.booking.provider import AylaYClientsAdapter
from apps.skills.booking.skill import _render_date_picker, _resolved_health_check_for_edge
from apps.skills.booking.tools import _quote_for_preview
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

SERVICE_AYLA_ID = uuid.UUID("0c5f0000-0000-4000-8000-00000000c0de")


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(
        slug="pick-master-1933", name="Pick 1933", timezone="Europe/Moscow"
    )


@pytest.fixture
def glued(tenant: Tenant) -> tuple[CatalogMaster, uuid.UUID]:
    catalog_id = uuid.uuid4()
    row = CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=1933,
        external_updated_at=datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc),
        name="Анна",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        ayla_user_id=uuid.uuid4(),
    )
    CatalogMaster.all_tenants.filter(pk=row.pk).update(catalog_specialist_id=catalog_id)
    row.refresh_from_db()
    service = CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=19331,
        external_updated_at=datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc),
        slug="manicure-1933",
        name="Маникюр",
        duration_min=60,
        is_active=True,
        ayla_service_id=SERVICE_AYLA_ID,
    )
    MasterService.all_tenants.create(
        tenant=tenant, master=row, service=service, resolved_requires_health_check=False
    )
    return row, catalog_id


class _Fake:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def get_available_dates(self, *, specialist_id: str, service_id: str) -> list[str]:
        self.sent.append(("get_available_dates", specialist_id))
        start = date.today() + timedelta(days=3)
        return [(start + timedelta(days=i)).isoformat() for i in range(3)]

    def get_specialist_service_edges(self, *, specialist_id: str, service_id: str) -> list[dict]:
        self.sent.append(("get_specialist_service_edges", specialist_id))
        return [{"price": "1500.00", "duration_minutes": 60}]


def _adapter(fake: _Fake, tenant: Tenant | None) -> AylaYClientsAdapter:
    return AylaYClientsAdapter(
        client=cast(Any, fake), external_user_id="bot:max:1933", client_id="c-1933", tenant=tenant
    )


def _callbacks(result: Any) -> list[str]:
    out: list[str] = []
    for attachment in (result.action_data or {}).get("attachments", []):
        buttons = attachment.get("payload", {}).get("buttons", [])
        for row in buttons:
            for button in row if isinstance(row, list) else [row]:
                if isinstance(button, dict) and button.get("callback"):
                    out.append(button["callback"])
    return out


def test_the_date_picker_asks_the_catalog_by_its_id(tenant, glued, settings):
    settings.BOOKING_VIA_AYLA_REST = True
    row, catalog_id = glued
    fake = _Fake()

    with tenant_scope(tenant):
        _render_date_picker(
            master_id=str(row.pk),
            service_id=str(SERVICE_AYLA_ID),
            yclients=_adapter(fake, tenant),
            tenant_id=str(tenant.id),
            tenant=tenant,
        )

    assert fake.sent == [("get_available_dates", str(catalog_id))]


def test_the_date_buttons_keep_the_mirror_pk(tenant, glued, settings):
    settings.BOOKING_VIA_AYLA_REST = True
    row, catalog_id = glued
    fake = _Fake()

    with tenant_scope(tenant):
        result = _render_date_picker(
            master_id=str(row.pk),
            service_id=str(SERVICE_AYLA_ID),
            yclients=_adapter(fake, tenant),
            tenant_id=str(tenant.id),
            tenant=tenant,
        )

    callbacks = _callbacks(result)
    assert callbacks, result.reply_text
    assert all(str(row.pk) in cb for cb in callbacks if "pick_date" in cb or "more_dates" in cb)
    assert all(str(catalog_id) not in cb for cb in callbacks)


def test_the_preview_quote_reads_the_edge_by_the_catalog_id(tenant, glued, settings):
    settings.BOOKING_VIA_AYLA_REST = True
    row, catalog_id = glued
    fake = _Fake()

    price, duration = _quote_for_preview(
        _adapter(fake, tenant), master_id=str(row.pk), service_id=str(SERVICE_AYLA_ID)
    )

    assert fake.sent == [("get_specialist_service_edges", str(catalog_id))]
    assert duration == 60 and price is not None


def test_the_local_health_gate_edge_is_found_by_the_mirror_pk(tenant, glued):
    row, _catalog_id = glued

    verdict = _resolved_health_check_for_edge(tenant, str(row.pk), str(SERVICE_AYLA_ID))

    assert verdict is False
