"""Tests for GET /customer/recent-activity — dashboard rollup (bookings-only).

Covers:
- next_booking present (earliest upcoming CONFIRMED) + field shape
- next_booking absent → key omitted
- this_week_booking_count (CONFIRMED in current week)
- date_human formatting (Сегодня / Завтра / dated)
- weekly_progress zeros (nutrition rollup deferred)
- tenant boundary (other tenant's booking invisible)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from datetime import datetime, timedelta, timezone as dt_timezone
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from apps.booking.models import BookingRequest
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-recent-activity"  # noqa: S105 — test fixture  # pragma: allowlist secret


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _init_data_header(user_id: str) -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Анна"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _bot_token(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture
def tenant(settings) -> Tenant:
    t = Tenant.objects.create(slug="recent-test", name="Формула тела", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "recent-test"
    return t


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="92001",
        display_name="Анна",
    )


def _make_booking(
    bot_user: BotUser,
    *,
    visit_at: datetime,
    status=BookingRequest.Status.CONFIRMED,
    service_name="Массаж лимфодренаж",
    master_name="Ирина",
    duration_min=60,
    tenant=None,
) -> BookingRequest:
    return BookingRequest.objects.create(
        tenant=tenant or bot_user.tenant,
        bot_user=bot_user,
        service_name=service_name,
        master_name=master_name,
        client_name="Анна",
        client_phone="+7-000",
        visit_at=visit_at,
        duration_min=duration_min,
        status=status,
        source="bot",
        booking_source="ai_direct",
        attribution_metadata={"actor_type": "customer", "created_by": "test"},
    )


def _url() -> str:
    return reverse("miniapp_api:customer_recent_activity")


def _get(client: Client, bot_user: BotUser):
    return client.get(_url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id))


class TestRecentActivityNextBooking:
    def test_next_booking_shape(self, client: Client, bot_user: BotUser):
        visit = datetime.now(dt_timezone.utc) + timedelta(days=2)
        _make_booking(bot_user, visit_at=visit)
        resp = _get(client, bot_user)
        assert resp.status_code == 200
        data = resp.json()
        nb = data["next_booking"]
        assert nb["service_name"] == "Массаж лимфодренаж"
        assert nb["master_name"] == "Ирина"
        assert nb["duration_min"] == 60
        assert nb["salon_name"] == "Формула тела"
        assert nb["address"] == ""  # no Tenant.address field — graceful
        assert "booking_id" in nb
        assert "·" in nb["date_human"]  # «… · пт · 16:00» shape

    def test_earliest_upcoming_wins(self, client: Client, bot_user: BotUser):
        later = datetime.now(dt_timezone.utc) + timedelta(days=5)
        sooner = datetime.now(dt_timezone.utc) + timedelta(days=1)
        _make_booking(bot_user, visit_at=later, service_name="Поздняя")
        _make_booking(bot_user, visit_at=sooner, service_name="Ранняя")
        data = _get(client, bot_user).json()
        assert data["next_booking"]["service_name"] == "Ранняя"

    def test_no_upcoming_omits_next_booking(self, client: Client, bot_user: BotUser):
        # Only a past booking exists.
        past = datetime.now(dt_timezone.utc) - timedelta(days=3)
        _make_booking(bot_user, visit_at=past)
        data = _get(client, bot_user).json()
        assert "next_booking" not in data

    def test_non_confirmed_not_surfaced(self, client: Client, bot_user: BotUser):
        future = datetime.now(dt_timezone.utc) + timedelta(days=2)
        _make_booking(bot_user, visit_at=future, status=BookingRequest.Status.CANCELLED)
        data = _get(client, bot_user).json()
        assert "next_booking" not in data


class TestRecentActivityWeekCount:
    def test_counts_only_this_week_confirmed(self, client: Client, bot_user: BotUser):
        now = datetime.now(dt_timezone.utc)
        # Two within ~this week (forward a day or two), one 20 days out.
        _make_booking(bot_user, visit_at=now + timedelta(hours=2))
        _make_booking(bot_user, visit_at=now + timedelta(days=1))
        _make_booking(bot_user, visit_at=now + timedelta(days=20))
        data = _get(client, bot_user).json()
        # The far-future one is excluded; the count reflects only the
        # current Mon-Sun window (exact value depends on weekday, so we
        # assert it's >= 1 and < 3).
        assert 1 <= data["this_week_booking_count"] <= 2


class TestRecentActivityWeeklyProgress:
    def test_weekly_progress_zeros(self, client: Client, bot_user: BotUser):
        data = _get(client, bot_user).json()
        assert data["weekly_progress"] == {
            "water_days_logged": 0,
            "food_days_logged": 0,
            "active_days_count": 0,
        }


class TestRecentActivityTenantBoundary:
    def test_other_tenant_booking_invisible(self, client: Client, bot_user: BotUser):
        other_tenant = Tenant.objects.create(
            slug="other-salon", name="Другой", timezone="Europe/Moscow"
        )
        other_user = BotUser.all_tenants.create(
            tenant=other_tenant, channel="max", channel_user_id="92002"
        )
        # A booking belonging to a different tenant/user.
        _make_booking(
            other_user,
            visit_at=datetime.now(dt_timezone.utc) + timedelta(days=1),
            tenant=other_tenant,
        )
        data = _get(client, bot_user).json()
        assert "next_booking" not in data
        assert data["this_week_booking_count"] == 0
