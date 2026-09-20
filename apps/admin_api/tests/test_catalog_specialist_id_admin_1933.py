"""Поверхность салона шлёт в каталог id профиля каталога (DRF-1933, часть 2).

Шесть вызовов: окна для записи администратором, создание записи, выходные и
отгулы, влияние закрытия времени, одобрение заявки на отгул, недельный шаблон
для подтверждения расписания. Везде мастер выбран по первичному ключу зеркала;
в каталог уходит ``catalog_specialist_id``. Пустая колонка — отказ по имени
``catalog_profile_unresolved`` (409), каталог не зовётся.

Красный до правки: склеенная строка (уходит pk) и отказ (каталог зовут).
Зелёный в обе стороны: строка синка.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.admin_api.services.availability import AvailabilityDecisionError
from apps.admin_api.tests.conftest import init_data_header, make_master
from apps.admin_api.tests.test_availability_ayla_block_1062 import (
    CLIENT_PATH as AVAILABILITY_CLIENT_PATH,
    END,
    START,
    _approve,
)
from apps.admin_api.tests.test_schedule_impact import WINDOW
from apps.catalog.models import CatalogMaster, CatalogService
from apps.catalog.services import schedule_confirmation
from apps.integrations.ayla.salon_client import SalonNotConfigured
from apps.scheduling.models import ScheduleChangeRequest
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

KINDS = ["glued", "unresolved", "synced"]


def _prepare(tenant: Tenant, kind: str) -> tuple[CatalogMaster, str | None]:
    """Мастер и ожидаемый id в каталоге (``None`` — ждём отказ)."""
    master = make_master(tenant, name="Анна", external_id=1933)
    if kind == "glued":
        value = uuid.uuid4()
    elif kind == "unresolved":
        value = None
    else:
        value = master.pk
    CatalogMaster.all_tenants.filter(pk=master.pk).update(catalog_specialist_id=value)
    master.refresh_from_db()
    return master, (str(value) if value else None)


def _service(tenant: Tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=19330,
        external_updated_at=datetime.now(tz=timezone.utc),
        slug="manicure-1933",
        name="Маникюр",
        duration_min=60,
        is_active=True,
        ayla_service_id=uuid.uuid4(),
    )


class _Recorder:
    def __init__(self, result=None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.result = result

    def __getattr__(self, name: str):
        def call(**kwargs):
            self.calls.append((name, kwargs))
            return self.result if self.result is not None else []

        return call


def _check(expected: str | None, resp, recorder: _Recorder, *, refusal_key: str = "error") -> None:
    if expected is None:
        assert resp.status_code == 409, resp.content
        assert resp.json()[refusal_key] in {"catalog_profile_unresolved", "blocked"}
        assert len(recorder.calls) == 0
        return
    sent = {str(kw["specialist_id"]) for _name, kw in recorder.calls if "specialist_id" in kw}
    assert sent == {expected}, recorder.calls


@pytest.mark.parametrize("kind", KINDS)
def test_booking_slots(client: Client, tenant, owner_bot_user, monkeypatch, kind):
    master, expected = _prepare(tenant, kind)
    service = _service(tenant)
    rec = _Recorder()
    monkeypatch.setattr(
        "apps.integrations.ayla.booking_client.get_ayla_booking_client", lambda: rec
    )

    resp = client.get(
        reverse("admin_api:booking_slots"),
        {"master_id": str(master.pk), "service_id": str(service.pk), "date": "2026-09-21"},
        HTTP_AUTHORIZATION=init_data_header("5001"),
    )

    _check(expected, resp, rec)


@pytest.mark.parametrize("kind", KINDS)
def test_create_booking(client: Client, tenant, owner_bot_user, monkeypatch, kind):
    master, expected = _prepare(tenant, kind)
    service = _service(tenant)
    rec = _Recorder(result={"id": str(uuid.uuid4())})
    monkeypatch.setattr("apps.integrations.ayla.salon_client.get_salon_client", lambda: rec)

    resp = client.post(
        reverse("admin_api:create_booking"),
        data=json.dumps(
            {
                "master_id": str(master.pk),
                "service_id": str(service.pk),
                "start_at": "2026-09-21T15:00:00+03:00",
                "client_name": "Мария",
                "client_phone": "+79990000000",
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header("5001"),
    )

    _check(expected, resp, rec, refusal_key="outcome")


@pytest.mark.parametrize("kind", KINDS)
def test_master_exceptions(client: Client, tenant, owner_bot_user, monkeypatch, settings, kind):
    settings.BOOKING_VIA_AYLA_REST = True
    master, expected = _prepare(tenant, kind)
    rec = _Recorder()
    monkeypatch.setattr("apps.admin_api.views_master_exceptions.get_salon_client", lambda: rec)

    resp = client.get(
        reverse("admin_api:master_exceptions", args=[str(master.pk)]),
        HTTP_AUTHORIZATION=init_data_header("5001"),
    )

    _check(expected, resp, rec)


@pytest.mark.parametrize("kind", KINDS)
def test_schedule_impact(client: Client, tenant, owner_bot_user, monkeypatch, settings, kind):
    settings.BOOKING_VIA_AYLA_REST = True
    master, expected = _prepare(tenant, kind)
    rec = _Recorder(result={"bookings": [], "timezone": "Europe/Moscow"})
    monkeypatch.setattr("apps.admin_api.services.schedule_impact.get_salon_client", lambda: rec)

    resp = client.get(
        reverse("admin_api:master_schedule_impact", args=[str(master.pk)]),
        WINDOW,
        HTTP_AUTHORIZATION=init_data_header("5001"),
    )

    _check(expected, resp, rec)


@pytest.mark.parametrize("kind", KINDS)
def test_availability_approval(tenant, settings, kind):
    settings.BOOKING_VIA_AYLA_REST = True
    master, expected = _prepare(tenant, kind)
    pending = ScheduleChangeRequest.all_tenants.create(
        tenant=tenant,
        master=master,
        requested_start=START,
        requested_end=END,
        reason_class="sick_leave",
        reason_text="болезнь",
        status=ScheduleChangeRequest.Status.PENDING,
    )
    calls: list[dict] = []
    fake = SimpleNamespace(
        create_specialist_time_off=lambda **kw: calls.append(kw) or {"id": str(uuid.uuid4())}
    )

    with patch(AVAILABILITY_CLIENT_PATH, return_value=fake):
        if expected is None:
            with pytest.raises(AvailabilityDecisionError) as exc:
                _approve(tenant, pending.id)
            assert (exc.value.slug, exc.value.status) == ("catalog_profile_unresolved", 409)
            assert len(calls) == 0
            return
        _approve(tenant, pending.id)

    assert [c["specialist_id"] for c in calls] == [expected]


@pytest.mark.parametrize("kind", KINDS)
def test_weekly_template_for_confirmation(tenant, owner_bot_user, monkeypatch, settings, kind):
    settings.BOOKING_VIA_AYLA_REST = True
    master, expected = _prepare(tenant, kind)
    rec = _Recorder()
    monkeypatch.setattr("apps.integrations.ayla.salon_client.get_salon_client", lambda: rec)

    if expected is None:
        with pytest.raises(SalonNotConfigured, match="catalog_specialist_unresolved"):
            schedule_confirmation._read_ayla(master)
        assert len(rec.calls) == 0
        return
    schedule_confirmation._read_ayla(master)

    assert [kw["specialist_id"] for _name, kw in rec.calls] == [expected]
