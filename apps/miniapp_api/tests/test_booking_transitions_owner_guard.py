"""Регресс-лок (#968): cross-customer защита на write-путях booking в miniapp_api.

Зачем
-----
Тикет #968 (расширение G5.1, ADR-0009 Rule 5) указывает, что клиентский
(MAX-init-data) surface miniapp_api пишет каноническое состояние booking напрямую
через ``apps.booking.services.transitions`` (cancel/reschedule) и
``apps.booking.services.feedback`` — то есть на тех же модулях-мутаторах, что и
исходный #925, только шире.

Что установлено при триаже на ``origin/dev``:

* «Source» тикета — ``.importlinter.baseline`` контракт ``G5-projection-writes-via-consumers``.
  Этого файла (и вообще import-linter) в репозитории нет — Part 1 тикета
  («расширить forbidden_modules») неисполним как написан. Зафиксировано в
  триаж-комментарии для S5.
* Part 2 (увести 7 сайтов на Ayla REST) — это архитектурный rewrite Phase 2.2,
  он вне S1 (та же развилка, что #996). Здесь НЕ делается.
* Владение booking уже проверяется на двух уровнях: view грузит строку через
  ``_get_booking_owned`` (фильтр ``(id, tenant, bot_user)``) и возвращает 404 ДО
  бизнес-логики; сервисный слой дополнительно зовёт ``_assert_actor_owns``
  (``booking.bot_user_id != actor.pk`` → ``forbidden``).

Этот тест НЕ меняет боевой код. Он замораживает существующую cross-customer
границу на ВСЕХ переходных write-эндпоинтах: клиент A не может отменить /
перенести / undo / оценить booking, принадлежащий другому клиенту B (тот же
тенант). Если будущий рефактор уберёт owner-фильтр из view, тест упадёт.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from apps.booking.models import BookingRequest
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-968"

# Все переходные write-эндпоинты, грузящие booking по (tenant, bot_user).
TRANSITION_ENDPOINTS = [
    "booking_cancel_request",
    "booking_cancel_confirm",
    "booking_cancel_undo",
    "booking_reschedule_request",
    "booking_reschedule_confirm",
    "submit_feedback",
]


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _init_data_header(user_id: str) -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Alice"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _bot_token(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture
def tenant(db, settings) -> Tenant:
    t = Tenant.objects.create(slug="tg968", name="Tenant Guard 968", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "tg968"
    return t


@pytest.fixture
def bot_user_a(tenant: Tenant) -> BotUser:
    """Аутентифицированный атакующий — клиент A."""
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="100100",
        display_name="Alice",
        chat_id="100100",
    )


@pytest.fixture
def bot_user_b(tenant: Tenant) -> BotUser:
    """Владелец booking — клиент B (тот же тенант)."""
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="200200",
        display_name="Bob",
        chat_id="200200",
    )


@pytest.fixture
def master(tenant: Tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=1,
        external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        name="Анна",
        is_active=True,
    )


@pytest.fixture
def service(tenant: Tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=42,
        external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        slug="manicure",
        name="Маникюр",
        duration_min=60,
        is_active=True,
    )


@pytest.fixture
def booking_b(
    tenant: Tenant, bot_user_b: BotUser, master: CatalogMaster, service: CatalogService
) -> BookingRequest:
    MasterService.all_tenants.create(tenant=tenant, master=master, service=service)
    return BookingRequest.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user_b,
        service=service,
        master=master,
        service_name=service.name,
        master_name=master.name,
        client_name="Bob",
        client_phone="+7-000",
        visit_at=datetime.now(timezone.utc) + timedelta(days=7),
        duration_min=60,
        status=BookingRequest.Status.CONFIRMED,
        source="bot",
        booking_source="ai_direct",
        billable=True,
        billing_reason="ai_direct + confirmed",
        attribution_metadata={"actor_type": "customer", "created_by": "execute_confirm"},
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("url_name", TRANSITION_ENDPOINTS)
def test_cross_customer_transition_rejected_and_unchanged(
    client: Client,
    bot_user_a: BotUser,
    booking_b: BookingRequest,
    url_name: str,
) -> None:
    """Клиент A дёргает write-эндпоинт на booking клиента B → 404, и состояние
    booking B не меняется (и новых строк не появляется).
    """

    assert BookingRequest.all_tenants.count() == 1

    resp = client.post(
        reverse(f"miniapp_api:{url_name}", kwargs={"booking_id": str(booking_b.id)}),
        data="{}",
        content_type="application/json",
        HTTP_AUTHORIZATION=_init_data_header("100100"),
    )

    # Owner-фильтр во view срабатывает ДО бизнес-логики → booking «не найден».
    assert resp.status_code == 404, (url_name, resp.status_code, resp.content)

    # Бронь клиента B нетронута: статус прежний, никаких фантомных строк
    # (reschedule/confirm при успехе создал бы новую запись — здесь не должен).
    booking_b.refresh_from_db()
    assert booking_b.status == BookingRequest.Status.CONFIRMED
    assert BookingRequest.all_tenants.count() == 1
