"""Integration tests for Customer Mini App views (Phase 0b)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from apps.booking.models import BookingRequest
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser
from apps.scheduling.models import ScheduleException, Weekday, WorkingHours
from apps.tenancy.models import Tenant


BOT_TOKEN = "test-bot-token-xyz"


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _init_data_header(user_id: str = "12345") -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Мария"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _bot_token(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="mn-test", name="Mini App Test", timezone="Europe/Moscow")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="12345",
        display_name="Мария",
        chat_id="12345",
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
        slug="manicure-gel",
        name="Маникюр гель-лак",
        duration_min=60,
        is_active=True,
    )


@pytest.fixture
def master_service(tenant: Tenant, master: CatalogMaster, service: CatalogService) -> None:
    """Master-service mapping — required for bookability per PR B."""

    MasterService.all_tenants.create(tenant=tenant, master=master, service=service)


@pytest.fixture
def working_hours(tenant: Tenant, master: CatalogMaster) -> None:
    # Mon..Sun, 10:00-19:00.
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


class TestAuthVerify:
    def test_happy_path(self, client: Client, bot_user: BotUser) -> None:
        resp = client.post(
            reverse("miniapp_api:auth_verify"),
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["user"]["channel_user_id"] == "12345"
        assert data["tenant"]["slug"] == "mn-test"

    def test_missing_header(self, client: Client) -> None:
        resp = client.post(reverse("miniapp_api:auth_verify"))
        assert resp.status_code == 400
        assert resp.json()["error"] == "malformed"

    def test_bad_signature(self, client: Client, bot_user: BotUser, settings) -> None:
        settings.MAX_BOT_TOKEN = BOT_TOKEN
        # Sign with wrong token.
        params = {
            "user": json.dumps({"id": 12345}),
            "auth_date": str(int(time_module.time())),
        }
        raw = _sign(params, token="wrong")
        resp = client.post(
            reverse("miniapp_api:auth_verify"),
            HTTP_AUTHORIZATION=f"MaxInitData {raw}",
        )
        assert resp.status_code == 401
        assert resp.json()["error"] == "bad_signature"

    @pytest.mark.django_db
    def test_user_not_registered(self, client: Client) -> None:
        # No BotUser fixture used; nothing in DB.
        resp = client.post(
            reverse("miniapp_api:auth_verify"),
            HTTP_AUTHORIZATION=_init_data_header("99999"),
        )
        assert resp.status_code == 404
        assert resp.json()["error"] == "user_not_registered"


class TestSlots:
    def _url(self, **params) -> str:
        return reverse("miniapp_api:slots") + "?" + urlencode(params)

    def test_returns_free_slots(
        self,
        client: Client,
        bot_user: BotUser,
        master: CatalogMaster,
        service: CatalogService,
        master_service,
        working_hours,
    ) -> None:
        # Pick a far-future date so the past-cutoff doesn't strip everything.
        target = date.today() + timedelta(days=30)
        resp = client.get(
            self._url(
                master_id=str(master.id),
                service_id=str(service.id),
                date_from=target.isoformat(),
                date_to=target.isoformat(),
            ),
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 200
        slots = resp.json()["slots"]
        assert len(slots) >= 8  # 10:00..17:00 with 15-min step → many slots
        # All slots on the target date.
        for s in slots:
            assert s["date"] == target.isoformat()

    def test_missing_params(self, client: Client, bot_user: BotUser) -> None:
        resp = client.get(
            reverse("miniapp_api:slots"),
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 400

    def test_range_too_wide(
        self,
        client: Client,
        bot_user: BotUser,
        master: CatalogMaster,
        service: CatalogService,
    ) -> None:
        resp = client.get(
            self._url(
                master_id=str(master.id),
                service_id=str(service.id),
                date_from="2026-06-01",
                date_to="2026-07-01",
            ),
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 400
        assert "exceeds" in resp.json()["detail"]

    def test_master_not_found(
        self,
        client: Client,
        bot_user: BotUser,
        service: CatalogService,
    ) -> None:
        resp = client.get(
            self._url(
                master_id="00000000-0000-0000-0000-000000000000",
                service_id=str(service.id),
                date_from="2026-06-01",
                date_to="2026-06-01",
            ),
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 404

    def test_master_not_bookable_when_pending(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
        service: CatalogService,
    ) -> None:
        pending = CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=99,
            external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
            name="Наталья",
            is_active=False,
            invite_status=CatalogMaster.InviteStatus.PENDING,
        )
        MasterService.all_tenants.create(tenant=tenant, master=pending, service=service)
        resp = client.get(
            self._url(
                master_id=str(pending.id),
                service_id=str(service.id),
                date_from="2026-06-01",
                date_to="2026-06-01",
            ),
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 404
        assert "not bookable" in resp.json()["detail"]

    def test_master_does_not_offer_service(
        self,
        client: Client,
        bot_user: BotUser,
        master: CatalogMaster,
        service: CatalogService,
        working_hours,
    ) -> None:
        # No MasterService row created — master is bookable but has no
        # services mapped. Booking must be rejected.
        target = date.today() + timedelta(days=30)
        resp = client.get(
            self._url(
                master_id=str(master.id),
                service_id=str(service.id),
                date_from=target.isoformat(),
                date_to=target.isoformat(),
            ),
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 404
        assert "does not perform" in resp.json()["detail"]

    def test_day_off_returns_empty(
        self,
        client: Client,
        bot_user: BotUser,
        master: CatalogMaster,
        service: CatalogService,
        master_service,
        working_hours,
        tenant: Tenant,
    ) -> None:
        target = date.today() + timedelta(days=30)
        ScheduleException.all_tenants.create(
            tenant=tenant,
            master=master,
            date=target,
            type=ScheduleException.Type.DAY_OFF,
        )
        resp = client.get(
            self._url(
                master_id=str(master.id),
                service_id=str(service.id),
                date_from=target.isoformat(),
                date_to=target.isoformat(),
            ),
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 200
        assert resp.json()["slots"] == []


class TestServicesEndpoints:
    def test_list_only_active(
        self, client: Client, bot_user: BotUser, tenant: Tenant, service: CatalogService
    ) -> None:
        CatalogService.all_tenants.create(
            tenant=tenant,
            external_id=99,
            external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
            slug="inactive",
            name="Inactive",
            duration_min=60,
            is_active=False,
        )
        resp = client.get(
            reverse("miniapp_api:services_list"),
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 200
        services = resp.json()["services"]
        assert len(services) == 1
        assert services[0]["slug"] == "manicure-gel"

    def test_detail(self, client: Client, bot_user: BotUser, service: CatalogService) -> None:
        resp = client.get(
            reverse("miniapp_api:service_detail", kwargs={"service_id": str(service.id)}),
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 200
        assert resp.json()["service"]["name"] == "Маникюр гель-лак"


class TestMastersEndpoints:
    def test_list_bookable_only(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
    ) -> None:
        # Pending master — should not appear in list.
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=99,
            external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
            name="Наталья (pending)",
            is_active=False,
            invite_status=CatalogMaster.InviteStatus.PENDING,
        )
        resp = client.get(
            reverse("miniapp_api:masters_list"),
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 200
        masters = resp.json()["masters"]
        assert len(masters) == 1
        assert masters[0]["name"] == "Анна"

    def test_list_filter_by_service(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
        master: CatalogMaster,
        service: CatalogService,
        master_service,
    ) -> None:
        # Second bookable master without MasterService mapping — shouldn't match filter.
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=2,
            external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
            name="Мария",
            is_active=True,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        )
        resp = client.get(
            reverse("miniapp_api:masters_list") + f"?service_id={service.id}",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 200
        masters = resp.json()["masters"]
        assert [m["name"] for m in masters] == ["Анна"]

    def test_detail_includes_services(
        self,
        client: Client,
        bot_user: BotUser,
        master: CatalogMaster,
        service: CatalogService,
        master_service,
    ) -> None:
        resp = client.get(
            reverse("miniapp_api:master_detail", kwargs={"master_id": str(master.id)}),
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 200
        payload = resp.json()["master"]
        assert payload["name"] == "Анна"
        assert str(service.id) in payload["service_ids"]


class TestCreateBooking:
    def _picked_slot(self) -> str:
        # Pick a far-future Monday 12:00 MSK to bypass past + lead_time.
        target_date = date.today() + timedelta(days=30)
        # Find next Monday from target.
        while target_date.weekday() != 0:
            target_date += timedelta(days=1)
        return f"{target_date.isoformat()}T12:00:00+03:00"

    def test_happy_path(
        self,
        client: Client,
        bot_user: BotUser,
        master: CatalogMaster,
        service: CatalogService,
        master_service,
        working_hours,
    ) -> None:
        visit_at = self._picked_slot()
        resp = client.post(
            reverse("miniapp_api:create_booking"),
            data=json.dumps(
                {
                    "service_id": str(service.id),
                    "master_id": str(master.id),
                    "visit_at": visit_at,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 201, resp.json()
        booking_id = resp.json()["booking"]["id"]

        booking = BookingRequest.all_tenants.get(id=booking_id)
        assert booking.booking_source == "ai_direct"
        assert booking.billable is True
        assert "ai_direct" in booking.billing_reason
        assert booking.attribution_metadata["actor_type"] == "customer"
        assert booking.attribution_metadata["created_by"] == "execute_confirm"
        assert booking.visit_at is not None
        assert booking.duration_min == 60
        assert booking.service_id == service.id
        assert booking.master_id == master.id

    def test_missing_body_fields(self, client: Client, bot_user: BotUser) -> None:
        resp = client.post(
            reverse("miniapp_api:create_booking"),
            data=json.dumps({}),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 400

    def test_visit_in_past_rejected(
        self,
        client: Client,
        bot_user: BotUser,
        master: CatalogMaster,
        service: CatalogService,
        master_service,
        working_hours,
    ) -> None:
        resp = client.post(
            reverse("miniapp_api:create_booking"),
            data=json.dumps(
                {
                    "service_id": str(service.id),
                    "master_id": str(master.id),
                    "visit_at": "2020-01-01T10:00:00+03:00",
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 400
        assert "future" in resp.json()["detail"]

    def test_master_without_service_mapping_rejected(
        self,
        client: Client,
        bot_user: BotUser,
        master: CatalogMaster,
        service: CatalogService,
        working_hours,
    ) -> None:
        # No master_service fixture — mapping absent.
        visit_at = self._picked_slot()
        resp = client.post(
            reverse("miniapp_api:create_booking"),
            data=json.dumps(
                {
                    "service_id": str(service.id),
                    "master_id": str(master.id),
                    "visit_at": visit_at,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 404
        assert "does not perform" in resp.json()["detail"]

    def test_slot_not_available_rejected(
        self,
        client: Client,
        bot_user: BotUser,
        master: CatalogMaster,
        service: CatalogService,
        master_service,
        working_hours,
    ) -> None:
        # Pick a time that's NOT on the grid (12:07 instead of 12:00/12:15).
        target_date = date.today() + timedelta(days=30)
        while target_date.weekday() != 0:
            target_date += timedelta(days=1)
        bad_time = f"{target_date.isoformat()}T12:07:00+03:00"
        resp = client.post(
            reverse("miniapp_api:create_booking"),
            data=json.dumps(
                {
                    "service_id": str(service.id),
                    "master_id": str(master.id),
                    "visit_at": bad_time,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 409
        assert "slot_unavailable" in resp.json()["error"]
