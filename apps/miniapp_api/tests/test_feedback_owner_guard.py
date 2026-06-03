"""Регресс-лок (#1005): defense-in-depth owner-граница для feedback.

Контекст
--------
S5 adversarial-pass на PR #1000 (#968) показал: у feedback owner-граница была
**однослойной** (только во view), в отличие от пяти transition-эндпоинтов, где
сервис дополнительно зовёт ``transitions._assert_actor_owns``. То есть «два
уровня владения» — неверно для feedback. Не эксплуатируется сегодня (view
фильтрует по bot_user), но это robustness-пробел перед пилотом.

Этот PR закрывает 4 пункта acceptance #1005:
1. сервис ``submit_feedback`` теперь сам проверяет владельца (паритет с
   transitions) — backstop на случай рефактора view или не-view вызывающего;
2. view грузит бронь через ``_get_booking_owned`` (явный ``tenant=`` предикат);
3. этот тест добавляет cross-tenant кейс;
4. 404-сообщение feedback нормализовано до общего «booking not found»
   (убран oracle «does not belong to this user»).

Тесты:
* view: cross-tenant и cross-customer feedback → 404, бронь не оценена;
* 404-сообщение — общий «booking not found»;
* сервис-уровень: ``submit_feedback`` с чужим ``actor`` → ``InvalidBookingTransition``
  (прямая проверка нового defense-in-depth, в обход view).
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
from apps.booking.services.feedback import submit_feedback
from apps.booking.services.transitions import InvalidBookingTransition
from apps.catalog.models import CatalogMaster, CatalogService
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-1005"


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


def _make_booking(tenant: Tenant, owner: BotUser, *, ext: int) -> BookingRequest:
    master = CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=ext,
        external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        name="Анна",
        is_active=True,
    )
    service = CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=ext + 1,
        external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        slug=f"svc-{ext}",
        name="Маникюр",
        duration_min=60,
        is_active=True,
    )
    return BookingRequest.all_tenants.create(
        tenant=tenant,
        bot_user=owner,
        service=service,
        master=master,
        service_name=service.name,
        master_name=master.name,
        client_name="Bob",
        client_phone="+7-000",
        # past visit so the only thing standing between the request and a
        # successful rating is the owner guard.
        visit_at=datetime.now(timezone.utc) - timedelta(days=1),
        duration_min=60,
        status=BookingRequest.Status.CONFIRMED,
        source="bot",
        booking_source="ai_direct",
        billable=True,
        billing_reason="ai_direct + confirmed",
        attribution_metadata={"actor_type": "customer", "created_by": "execute_confirm"},
    )


@pytest.fixture(autouse=True)
def _bot_token(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture
def tenant_a(db, settings) -> Tenant:
    t = Tenant.objects.create(slug="fb1005-a", name="Feedback Guard A", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "fb1005-a"
    return t


@pytest.fixture
def tenant_b(db) -> Tenant:
    return Tenant.objects.create(slug="fb1005-b", name="Feedback Guard B", timezone="Europe/Moscow")


@pytest.fixture
def attacker(tenant_a: Tenant) -> BotUser:
    """Authenticated client A (tenant A)."""
    return BotUser.all_tenants.create(
        tenant=tenant_a, channel="max", channel_user_id="100100", chat_id="100100"
    )


@pytest.fixture
def victim_other_tenant(tenant_b: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant_b, channel="max", channel_user_id="200200", chat_id="200200"
    )


@pytest.fixture
def victim_same_tenant(tenant_a: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant_a, channel="max", channel_user_id="300300", chat_id="300300"
    )


@pytest.mark.django_db(transaction=True)
def test_cross_tenant_feedback_rejected(
    client: Client, attacker: BotUser, victim_other_tenant: BotUser, tenant_b: Tenant
) -> None:
    """Client A (tenant A) rates a booking owned by B in a DIFFERENT tenant →
    404 generic, no rating written. Trips if the explicit ``tenant=`` predicate
    or STRICT_TENANT_SCOPE is ever weakened.
    """
    booking = _make_booking(tenant_b, victim_other_tenant, ext=10)

    resp = client.post(
        reverse("miniapp_api:submit_feedback", kwargs={"booking_id": str(booking.id)}),
        data=json.dumps({"rating": 5, "comment": "x"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=_init_data_header("100100"),
    )

    assert resp.status_code == 404, resp.content
    # Normalized message — no existence oracle (#1005 LOW).
    assert resp.json()["detail"] == "booking not found"
    booking.refresh_from_db()
    assert booking.rating is None
    assert booking.feedback_at is None


@pytest.mark.django_db(transaction=True)
def test_cross_customer_same_tenant_feedback_rejected(
    client: Client, attacker: BotUser, victim_same_tenant: BotUser, tenant_a: Tenant
) -> None:
    """Client A rates a booking owned by another customer in the SAME tenant →
    404, no rating written.
    """
    booking = _make_booking(tenant_a, victim_same_tenant, ext=20)

    resp = client.post(
        reverse("miniapp_api:submit_feedback", kwargs={"booking_id": str(booking.id)}),
        data=json.dumps({"rating": 1, "comment": "y"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=_init_data_header("100100"),
    )

    assert resp.status_code == 404, resp.content
    assert resp.json()["detail"] == "booking not found"
    booking.refresh_from_db()
    assert booking.rating is None
    assert booking.feedback_at is None


@pytest.mark.django_db(transaction=True)
def test_service_layer_owner_assert_blocks_foreign_actor(
    tenant_a: Tenant, attacker: BotUser, victim_same_tenant: BotUser
) -> None:
    """Defense-in-depth: calling the service directly with a foreign actor —
    bypassing the view filter — still raises before any rating write (#1005).
    """
    booking = _make_booking(tenant_a, victim_same_tenant, ext=30)

    with pytest.raises(InvalidBookingTransition):
        submit_feedback(booking, actor=attacker, rating=5, comment="z")

    booking.refresh_from_db()
    assert booking.rating is None
    assert booking.feedback_at is None
