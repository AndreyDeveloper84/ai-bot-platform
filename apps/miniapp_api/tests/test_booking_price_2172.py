"""Цена записи на клиентской поверхности (DRF-2172, H01-1).

Макет DRF-1321 v1.2: «3 200 ₽» справа в карточке ближайшей записи. Источник —
снимок цены на момент записи: каталог шлёт его в ``booking.created`` как
``price_total`` (event-contract §3.1, v1), зеркало ``RemoteBookingProxy``
хранит ``price_amount``, ручки ``recent-activity`` / ``bookings/list`` /
``bookings/<id>`` отдают ``price`` строкой decimal или ``null``.

Сторожа из листа:
  - консьюмер кладёт ``price_total`` в зеркало; нет/нечисло → ``null``, а
    не падение события (запись важнее цены);
  - цена — снимок: последующие события (перенос) её не трогают;
  - ``null`` в зеркале → ``price: null`` (строки на экране нет, §103), не «0»;
  - локальный ``BookingRequest`` цены не хранит → ``null`` честно.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import time as time_module
import uuid
from urllib.parse import urlencode
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.booking.models import RemoteBookingProxy
from apps.eventbus.consumers.booking import handle_booking_created, handle_booking_rescheduled
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.identity.models import BotUser
from apps.miniapp_api.tests.test_recent_activity_mirror import make_mirror_visit
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

TENANT_ID = "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c"
AYLA_USER_ID = "f1a2b3c4-d5e6-4789-9abc-def012345678"
BOT_TOKEN = "test-bot-token-price-2172"  # noqa: S105 — test fixture  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.MAX_BOT_TENANT_SLUG = "t-price-2172"
    settings.BOOKING_VIA_AYLA_REST = True
    # Тот же путь, что у прода: тенант и события в allowlist (T-02).
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = False
    settings.EVENT_INGEST_ALLOWED_TENANTS = frozenset({TENANT_ID})
    settings.EVENT_INGEST_ALLOWED_EVENTS = frozenset({"booking.created", "booking.rescheduled"})


def _init_data_header(user_id: str) -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Анна"}),
        "auth_date": str(int(time_module.time())),
    }
    check = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return f"MaxInitData {urlencode({**params, 'hash': digest}, doseq=False)}"


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(
        id=TENANT_ID, slug="t-price-2172", name="Цена 2172", timezone="Europe/Moscow"
    )


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="21720001",
        chat_id="chat-21720001",
        ayla_user_id=AYLA_USER_ID,
        display_name="Анна",
    )


def _envelope(
    event_name: str, data: dict[str, Any], *, event_id: str | None = None
) -> IngestEnvelope:
    return IngestEnvelope(
        event_id=event_id or f"01J9{uuid.uuid4().hex[:22].upper()}",
        event_name=event_name,
        event_version=1,
        occurred_at=dt.datetime(2026, 9, 20, 10, 0, tzinfo=dt.timezone.utc),
        tenant_id=TENANT_ID,
        user_id=AYLA_USER_ID,
        actor="user",
        correlation_id=str(uuid.uuid4()),
        causation_id=None,
        data=data,
    )


def _created(appointment_id: str, **overrides: Any) -> dict[str, Any]:
    start = timezone.now() + dt.timedelta(days=2)
    data: dict[str, Any] = {
        "appointment_id": appointment_id,
        "specialist_id": str(uuid.uuid4()),
        "service_id": str(uuid.uuid4()),
        "start_at": start.isoformat(),
        "end_at": (start + dt.timedelta(hours=1)).isoformat(),
        "status": "confirmed",
        "price_total": "3200.00",
        "source": "mobile_app",
    }
    data.update(overrides)
    return data


def _get(client: Client, name: str, bot_user: BotUser, **kwargs: Any):
    return client.get(
        reverse(f"miniapp_api:{name}", kwargs=kwargs or None),
        HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
    )


class TestConsumerStoresTheSnapshot:
    def test_price_total_lands_in_the_mirror(self, tenant, bot_user):
        appt = str(uuid.uuid4())
        handle_booking_created(_envelope("booking.created", _created(appt)))

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=appt)
        assert proxy.price_amount == Decimal("3200.00")

    @pytest.mark.parametrize("bad", [None, "", "free", "NaN", "Infinity", "-5", "100000000.00"])
    def test_missing_or_unreadable_price_is_null_not_a_crash(self, tenant, bot_user, bad):
        appt = str(uuid.uuid4())
        data = _created(appt)
        if bad is None:
            data.pop("price_total")
        else:
            data["price_total"] = bad

        handle_booking_created(_envelope("booking.created", data))  # не падает

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=appt)
        assert proxy.price_amount is None
        assert proxy.status == "confirmed"  # запись доехала и без цены

    def test_reschedule_keeps_the_booking_time_price(self, tenant, bot_user):
        """Снимок: перенос меняет время, не цену — даже если прайс уже другой."""
        appt = str(uuid.uuid4())
        handle_booking_created(_envelope("booking.created", _created(appt)))
        new_start = timezone.now() + dt.timedelta(days=4)
        handle_booking_rescheduled(
            _envelope(
                "booking.rescheduled",
                {
                    "appointment_id": appt,
                    "old_start_at": (timezone.now() + dt.timedelta(days=2)).isoformat(),
                    "new_start_at": new_start.isoformat(),
                    "new_end_at": (new_start + dt.timedelta(hours=1)).isoformat(),
                    "reason": "customer_request",
                },
            )
        )

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=appt)
        assert proxy.price_amount == Decimal("3200.00")


class TestDialogPathAndRedelivery:
    """Запись из диалога бота: зеркало пишется ДО события, уже CONFIRMED, без цены."""

    def _pre_existing_confirmed(self, tenant, bot_user, appt: str, **extra):
        start = timezone.now() + dt.timedelta(days=2)
        return RemoteBookingProxy.all_tenants.create(
            appointment_id=appt,
            tenant=tenant,
            bot_user=bot_user,
            start_at=start,
            end_at=start + dt.timedelta(hours=1),
            status=RemoteBookingProxy.Status.CONFIRMED,
            source=RemoteBookingProxy.Source.MOBILE_APP,
            last_synced_event_id="dialog-write",
            **extra,
        )

    def test_dialog_booking_gets_its_price_from_the_late_created_event(self, tenant, bot_user):
        """Блокер ревью: мажоритарный путь — строка уже CONFIRMED, событие приходит позже."""
        appt = str(uuid.uuid4())
        self._pre_existing_confirmed(tenant, bot_user, appt)

        handle_booking_created(_envelope("booking.created", _created(appt)))

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=appt)
        assert proxy.status == "confirmed"  # состояние не откачено
        assert proxy.price_amount == Decimal("3200.00")

    def test_stored_price_is_never_overwritten_by_a_later_created(self, tenant, bot_user):
        """Снимок: строка уже с ценой, событие с другой — остаётся первая."""
        appt = str(uuid.uuid4())
        self._pre_existing_confirmed(tenant, bot_user, appt, price_amount=Decimal("1500.00"))

        handle_booking_created(_envelope("booking.created", _created(appt, price_total="3200.00")))

        assert RemoteBookingProxy.all_tenants.get(appointment_id=appt).price_amount == Decimal(
            "1500.00"
        )

    def test_redelivery_without_price_does_not_erase_the_snapshot(self, tenant, bot_user):
        """Не-advanced строка (pending_payment): повтор события без price_total — цена на месте."""
        appt = str(uuid.uuid4())
        handle_booking_created(
            _envelope(
                "booking.created", _created(appt, status="pending_payment"), event_id="01EVT-A"
            )
        )
        assert RemoteBookingProxy.all_tenants.get(appointment_id=appt).price_amount == Decimal(
            "3200.00"
        )

        data = _created(appt, status="pending_payment")
        data.pop("price_total")
        handle_booking_created(_envelope("booking.created", data, event_id="01EVT-B"))

        assert RemoteBookingProxy.all_tenants.get(appointment_id=appt).price_amount == Decimal(
            "3200.00"
        )


class TestSurfacesCarryThePrice:
    def test_recent_activity_next_booking_price(self, client, tenant, bot_user):
        appt = str(uuid.uuid4())
        handle_booking_created(_envelope("booking.created", _created(appt)))

        body = _get(client, "customer_recent_activity", bot_user).json()

        assert body["next_booking"]["booking_id"] == appt
        assert body["next_booking"]["price"] == "3200.00"

    def test_recent_activity_without_price_is_null_not_zero(self, client, tenant, bot_user):
        make_mirror_visit(
            tenant=tenant, bot_user=bot_user, visit_at=timezone.now() + dt.timedelta(days=1)
        )

        nb = _get(client, "customer_recent_activity", bot_user).json()["next_booking"]

        assert "price" in nb
        assert nb["price"] is None

    def test_bookings_list_and_detail_carry_price(self, client, tenant, bot_user):
        appt = str(uuid.uuid4())
        handle_booking_created(_envelope("booking.created", _created(appt)))

        listed = _get(client, "bookings_list", bot_user).json()["items"]
        assert [b["price"] for b in listed if b["id"] == appt] == ["3200.00"]

        detail = _get(client, "booking_detail", bot_user, booking_id=appt).json()["booking"]
        assert detail["price"] == "3200.00"

    def test_price_below_one_rouble_is_still_the_stored_value(self, client, tenant, bot_user):
        """«0.00» уезжает как есть — прятать «< 1 ₽» решает экран (DRF-1989), не провод."""
        appt = str(uuid.uuid4())
        handle_booking_created(_envelope("booking.created", _created(appt, price_total="0.00")))

        nb = _get(client, "customer_recent_activity", bot_user).json()["next_booking"]
        assert nb["price"] == "0.00"
