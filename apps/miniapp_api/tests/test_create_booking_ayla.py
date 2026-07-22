"""Miniapp create_booking on the Ayla path (AMD-019 / D6).

Pins: payment_required passthrough (default FALSE for miniapp),
verbatim Ayla status in the response, C1-neutral 409 on
SUBSCRIPTION_PAST_DUE, fail-closed grounding, and the local default
path staying untouched when BOOKING_VIA_AYLA_REST is off.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
import uuid
from urllib.parse import urlencode

import pytest
from django.test import Client as DjangoClient, override_settings

from apps.catalog.models import CatalogMaster, CatalogService
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import (
    AylaBookingRecord,
    BookingBadRequestError,
)
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-xyz"
AYLA_UID = uuid.uuid4()
SERVICE_AYLA_ID = uuid.uuid4()
MASTER_AYLA_ID = uuid.uuid4()


def _sign(params: dict[str, str]) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _init_data_header(user_id: str = "12345") -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Мария"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _settings(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.MAX_BOT_TENANT_SLUG = "ayla-create-test"
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="ayla-create-test", name="Ayla Create")


@pytest.fixture
def bot_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="12345",
        chat_id="12345",
        ayla_user_id=AYLA_UID,
    )


@pytest.fixture
def master(tenant) -> CatalogMaster:
    from django.utils import timezone as tz

    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=tz.now(),
        name="Ольга",
        specialization="Маникюр",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        ayla_user_id=MASTER_AYLA_ID,
    )


@pytest.fixture
def service(tenant) -> CatalogService:
    from django.utils import timezone as tz

    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_updated_at=tz.now(),
        name="Маникюр",
        slug="manikyur",
        duration_min=60,
        is_active=True,
        ayla_service_id=SERVICE_AYLA_ID,
    )


class _StubAylaClient:
    def __init__(self, *, exc: Exception | None = None) -> None:
        self.exc = exc
        self.calls: list[dict] = []

    def create_appointment(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return AylaBookingRecord(
            appointment_id=str(uuid.uuid4()),
            raw={"id": str(uuid.uuid4()), "status": kwargs.get("_status", "confirmed")},
        )


@pytest.fixture
def stub_client(monkeypatch) -> _StubAylaClient:
    stub = _StubAylaClient()
    monkeypatch.setattr(
        "apps.integrations.ayla.booking_client.get_ayla_booking_client",
        lambda: stub,
    )
    return stub


def _post(client: DjangoClient, service, master, extra: dict | None = None):
    body = {
        "service_id": str(service.id),
        "master_id": str(master.id),
        "visit_at": "2026-08-01T14:00:00+03:00",
    }
    if extra:
        body.update(extra)
    return client.post(
        "/api/v1/customer/bookings",
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=_init_data_header("12345"),
    )


class TestPassthrough:
    def test_default_false(self, client, bot_user, service, master, stub_client) -> None:
        resp = _post(client, service, master)
        assert resp.status_code == 201
        call = stub_client.calls[0]
        assert call["payment_required"] is False
        assert call["specialist_id"] == str(master.id)
        # #1027: SpecialistProfile UUID (= CatalogMaster.id), NEVER the
        # Ayla User UUID (that one is the AMD-005 billing key only).
        assert call["specialist_id"] != str(master.ayla_user_id)
        assert call["service_id"] == str(SERVICE_AYLA_ID)
        assert call["client_id"] == str(AYLA_UID)
        assert resp.json()["booking"]["status"] == "confirmed"

    def test_true_passed_verbatim(self, client, bot_user, service, master, stub_client) -> None:
        resp = _post(client, service, master, {"payment_required": True})
        assert resp.status_code == 201
        assert stub_client.calls[0]["payment_required"] is True

    def test_false_passed_verbatim(self, client, bot_user, service, master, stub_client) -> None:
        resp = _post(client, service, master, {"payment_required": False})
        assert resp.status_code == 201
        assert stub_client.calls[0]["payment_required"] is False

    def test_awaiting_payment_status_verbatim(
        self, client, bot_user, service, master, monkeypatch
    ) -> None:
        record = AylaBookingRecord(
            appointment_id=str(uuid.uuid4()),
            raw={"id": str(uuid.uuid4()), "status": "awaiting_payment"},
        )
        monkeypatch.setattr(
            "apps.integrations.ayla.booking_client.get_ayla_booking_client",
            lambda: type("C", (), {"create_appointment": lambda self, **kw: record})(),
        )
        resp = _post(client, service, master, {"payment_required": True})
        assert resp.status_code == 201
        assert resp.json()["booking"]["status"] == "awaiting_payment"

    def test_idempotency_key_differs_by_payment_required(
        self, client, bot_user, service, master, stub_client
    ) -> None:
        _post(client, service, master, {"payment_required": False})
        _post(client, service, master, {"payment_required": True})
        keys = [c["idempotency_key"] for c in stub_client.calls]
        assert len(keys) == 2 and keys[0] != keys[1]


class TestErrors:
    def test_c1_conflict_maps_unavailable(
        self, client, bot_user, service, master, stub_client
    ) -> None:
        stub_client.exc = BookingBadRequestError(
            "http_409_subscription_past_due",
            status_code=409,
            code="SUBSCRIPTION_PAST_DUE",
        )
        resp = _post(client, service, master)
        assert resp.status_code == 409
        assert resp.json()["error"] == "unavailable"
        assert "past_due" not in resp.text
        assert "subscription" not in resp.text.lower()

    def test_grounding_miss_fails_closed(
        self, client, bot_user, tenant, master, stub_client
    ) -> None:
        from django.utils import timezone as tz

        unlinked = CatalogService.all_tenants.create(
            tenant=tenant,
            external_updated_at=tz.now(),
            name="Не синкано",
            slug="unsynced",
            is_active=True,
            ayla_service_id=None,
        )
        resp = _post(client, unlinked, master)
        assert resp.status_code == 409
        assert resp.json()["error"] == "service_unbookable"
        assert stub_client.calls == []

    def test_unlinked_user_403(self, client, tenant, service, master, stub_client) -> None:
        BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="777", ayla_user_id=None
        )
        body = {
            "service_id": str(service.id),
            "master_id": str(master.id),
            "visit_at": "2026-08-01T14:00:00+03:00",
        }
        resp = client.post(
            "/api/v1/customer/bookings",
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header("777"),
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "identity_not_linked"
        assert stub_client.calls == []


class TestLocalPathUnchanged:
    @override_settings(BOOKING_VIA_AYLA_REST=False)
    def test_flag_off_keeps_local_path(
        self, client, bot_user, service, master, stub_client
    ) -> None:
        """payment_required is accepted but inert when the flag is off —
        the local create path is untouched and Ayla is never called."""
        import datetime as dt

        from apps.catalog.models import MasterService
        from apps.scheduling.models import Weekday, WorkingHours

        MasterService.all_tenants.create(tenant=bot_user.tenant, master=master, service=service)
        # 2026-08-01 is a Saturday — open the whole day for the slot check.
        WorkingHours.all_tenants.create(
            tenant=bot_user.tenant,
            master=master,
            day_of_week=Weekday.SATURDAY,
            start_time=dt.time(9, 0),
            end_time=dt.time(18, 0),
            is_working=True,
        )
        resp = _post(client, service, master, {"payment_required": True})
        assert resp.status_code == 201
        assert stub_client.calls == []
        assert resp.json()["booking"]["status"] == "confirmed"
