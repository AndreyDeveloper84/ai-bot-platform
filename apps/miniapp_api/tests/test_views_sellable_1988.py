"""Mini App клиента не продаёт непродаваемое ребро (DRF-1964a).

Слоты, витрина услуг (``is_bookable``), подборщик мастеров по услуге и
карточка мастера читают ребро через ``MasterService``; ребро с
``sellable=false`` для них то же, что отсутствие ребра.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser
from apps.scheduling.models import Weekday, WorkingHours
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-1988"  # pragma: allowlist secret
_TS = datetime(2026, 9, 15, tzinfo=timezone.utc)


def _init_data_header(user_id: str = "19880") -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Клиент"}),
        "auth_date": str(int(time_module.time())),
    }
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return f"MaxInitData {urlencode({**params, 'hash': digest})}"


@pytest.fixture(autouse=True)
def _bot_token(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.MAX_BOT_TENANT_SLUG = "mn-sell-1988"


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(
        slug="mn-sell-1988", name="Mini App Sellable", timezone="Europe/Moscow"
    )


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="19880",
        display_name="Клиент",
        chat_id="19880",
    )


def _master(tenant: Tenant, name: str, external_id: int) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=external_id,
        external_updated_at=_TS,
        name=name,
        is_active=True,
        ayla_user_id=uuid4(),
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
    )


@pytest.fixture
def master(tenant: Tenant) -> CatalogMaster:
    return _master(tenant, "Анна", 1)


@pytest.fixture
def neck(tenant: Tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=1988,
        external_updated_at=_TS,
        slug="neck-1988",
        name="Массаж шейно-воротниковой зоны",
        duration_min=30,
        is_active=True,
    )


@pytest.fixture
def working_hours(tenant: Tenant, master: CatalogMaster) -> None:
    for wd in (
        Weekday.MONDAY,
        Weekday.TUESDAY,
        Weekday.WEDNESDAY,
        Weekday.THURSDAY,
        Weekday.FRIDAY,
        Weekday.SATURDAY,
        Weekday.SUNDAY,
    ):
        WorkingHours.all_tenants.create(
            tenant=tenant,
            master=master,
            day_of_week=wd,
            is_working=True,
            start_time=time(10, 0),
            end_time=time(19, 0),
        )


def _unsellable_link(
    tenant: Tenant, master: CatalogMaster, service: CatalogService
) -> MasterService:
    assert {"sellable", "unsellable_reason"} <= {
        f.name for f in MasterService._meta.get_fields()
    }, "у MasterService нет колонок sellable / unsellable_reason"
    return MasterService.all_tenants.create(
        tenant=tenant,
        master=master,
        service=service,
        sellable=False,
        unsellable_reason="price_below_minimum",
    )


def test_slots_for_unsellable_edge_answer_404(
    client: Client, bot_user, tenant, master, neck, working_hours
):
    _unsellable_link(tenant, master, neck)
    target = date.today() + timedelta(days=30)

    resp = client.get(
        reverse("miniapp_api:slots")
        + "?"
        + urlencode(
            {
                "master_id": str(master.id),
                "service_id": str(neck.id),
                "date_from": target.isoformat(),
                "date_to": target.isoformat(),
            }
        ),
        HTTP_AUTHORIZATION=_init_data_header(),
    )

    assert resp.status_code == 404
    assert "does not perform" in resp.json()["detail"]


def test_service_whose_only_edge_is_unsellable_is_not_bookable(
    client: Client, bot_user, tenant, master, neck
):
    _unsellable_link(tenant, master, neck)

    resp = client.get(reverse("miniapp_api:services_list"), HTTP_AUTHORIZATION=_init_data_header())

    assert resp.status_code == 200
    by_id = {s["id"]: s for s in resp.json()["services"]}
    assert by_id[str(neck.id)]["is_bookable"] is False


def test_masters_of_service_exclude_unsellable_edge(client: Client, bot_user, tenant, master, neck):
    _unsellable_link(tenant, master, neck)
    sold = _master(tenant, "Ольга", 2)
    MasterService.all_tenants.create(tenant=tenant, master=sold, service=neck)

    resp = client.get(
        reverse("miniapp_api:masters_list") + f"?service_id={neck.id}",
        HTTP_AUTHORIZATION=_init_data_header(),
    )

    assert resp.status_code == 200
    assert [m["name"] for m in resp.json()["masters"]] == ["Ольга"]


def test_master_detail_omits_unsellable_service(client: Client, bot_user, tenant, master, neck):
    back = CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=1989,
        external_updated_at=_TS,
        slug="back-1988",
        name="Массаж спины",
        duration_min=60,
        is_active=True,
    )
    MasterService.all_tenants.create(tenant=tenant, master=master, service=back)
    _unsellable_link(tenant, master, neck)

    resp = client.get(
        reverse("miniapp_api:master_detail", kwargs={"master_id": str(master.id)}),
        HTTP_AUTHORIZATION=_init_data_header(),
    )

    assert resp.status_code == 200
    service_ids = resp.json()["master"]["service_ids"]
    assert str(back.id) in service_ids  # список не пуст: продаваемая услуга мастера на месте
    assert str(neck.id) not in service_ids
