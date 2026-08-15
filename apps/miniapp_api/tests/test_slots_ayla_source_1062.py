"""Mini App slots read from Ayla, not from the bot's own schedule (DRF-1062).

The defect: the customer picker computed slots from ``apps.scheduling``
while bookings were written to Ayla, and the endpoint had no branch on
``BOOKING_VIA_AYLA_REST`` at all. Two stores, and the one nobody could
edit was the one being shown — which is why the pilot's masters offered
10:00-19:00 seven days a week whatever Ayla knew.

The load-bearing test here is ``test_local_schedule_no_longer_decides``:
local WorkingHours say the day is open, Ayla says there is nothing, and
the customer must see nothing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
import uuid
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser
from apps.scheduling.models import Weekday, WorkingHours
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-xyz"
CLIENT_PATH = "apps.integrations.ayla.booking_client.get_ayla_booking_client"


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
def _bot_token(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.MAX_BOT_TENANT_SLUG = "ayla-slots-test"


@pytest.fixture(autouse=True)
def _ayla_path(settings) -> None:
    """Every test here is about the Ayla path unless it says otherwise."""
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(
        slug="ayla-slots-test",
        name="Слоты из Ayla",
        timezone="Europe/Moscow",
    )


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
        external_updated_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        name="Ольга",
        is_active=True,
        # The Ayla User id — deliberately different from the row id, which
        # is what the slots endpoint actually takes. Mixing these up gives
        # a silently empty picker.
        ayla_user_id=uuid.uuid4(),
    )


@pytest.fixture
def service(tenant: Tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=42,
        external_updated_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        slug="uz-cavitation",
        name="УЗ-кавитация",
        duration_min=60,
        is_active=True,
        ayla_service_id=uuid.uuid4(),
    )


@pytest.fixture
def master_service(tenant, master, service) -> None:
    MasterService.all_tenants.create(tenant=tenant, master=master, service=service)


@pytest.fixture
def open_every_day(tenant, master) -> None:
    """The stub the pilot actually has: 10:00-19:00, all seven days."""
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


def _url(**params) -> str:
    return reverse("miniapp_api:slots") + "?" + urlencode(params)


def _get(client: Client, master, service, day) -> object:
    return client.get(
        _url(
            master_id=str(master.id),
            service_id=str(service.id),
            date_from=day.isoformat(),
            date_to=day.isoformat(),
        ),
        HTTP_AUTHORIZATION=_init_data_header("12345"),
    )


def _fake_client(slots_by_date: dict[str, list[str]] | None = None, *, raises=None):
    calls: list[dict] = []

    def get_available_times(*, specialist_id, date, service_id):
        calls.append({"specialist_id": specialist_id, "date": date, "service_id": service_id})
        if raises is not None:
            raise raises
        return [SimpleNamespace(datetime=iso) for iso in (slots_by_date or {}).get(date, [])]

    return SimpleNamespace(get_available_times=get_available_times), calls


@pytest.fixture
def sunday() -> date:
    """A Sunday far enough out that the lead-time cutoff never bites."""
    base = date.today() + timedelta(days=21)
    return base + timedelta(days=(6 - base.weekday()) % 7)


class TestAylaIsTheSourceOfTruth:
    def test_local_schedule_no_longer_decides(
        self,
        client,
        bot_user,
        master,
        service,
        master_service,
        open_every_day,
        sunday,
    ):
        """The pilot regression, exactly.

        The bot's own rows say Sunday 10:00-19:00. Ayla — where the salon
        actually manages its schedule — says the day is closed. The
        customer must see nothing.
        """
        fake, _ = _fake_client({})

        with patch(CLIENT_PATH, return_value=fake):
            resp = _get(client, master, service, sunday)

        assert resp.status_code == 200
        assert resp.json()["slots"] == []

    def test_slots_shown_are_the_ones_ayla_returned(
        self,
        client,
        bot_user,
        master,
        service,
        master_service,
        open_every_day,
        sunday,
    ):
        fake, _ = _fake_client(
            {
                sunday.isoformat(): [
                    f"{sunday.isoformat()}T12:00:00+03:00",
                    f"{sunday.isoformat()}T13:30:00+03:00",
                ],
            }
        )

        with patch(CLIENT_PATH, return_value=fake):
            resp = _get(client, master, service, sunday)

        slots = resp.json()["slots"]
        assert [s["start"] for s in slots] == [
            f"{sunday.isoformat()}T12:00:00+03:00",
            f"{sunday.isoformat()}T13:30:00+03:00",
        ]
        assert {s["date"] for s in slots} == {sunday.isoformat()}

    def test_calls_ayla_with_the_specialist_id_not_the_user_id(
        self,
        client,
        bot_user,
        master,
        service,
        master_service,
        open_every_day,
        sunday,
    ):
        """CatalogMaster.id IS the Ayla specialist id (the mirror upserts
        with id=dto.ayla_master_id). ayla_user_id is the Ayla *User* id and
        would silently return nothing."""
        fake, calls = _fake_client({})

        with patch(CLIENT_PATH, return_value=fake):
            _get(client, master, service, sunday)

        assert len(calls) == 1
        assert calls[0]["specialist_id"] == str(master.id)
        assert calls[0]["specialist_id"] != str(master.ayla_user_id)
        assert calls[0]["service_id"] == str(service.ayla_service_id)

    def test_one_call_per_day_across_the_window(
        self,
        client,
        bot_user,
        master,
        service,
        master_service,
        open_every_day,
    ):
        start = date.today() + timedelta(days=21)
        end = start + timedelta(days=2)
        fake, calls = _fake_client({})

        with patch(CLIENT_PATH, return_value=fake):
            client.get(
                _url(
                    master_id=str(master.id),
                    service_id=str(service.id),
                    date_from=start.isoformat(),
                    date_to=end.isoformat(),
                ),
                HTTP_AUTHORIZATION=_init_data_header("12345"),
            )

        assert [c["date"] for c in calls] == [
            start.isoformat(),
            (start + timedelta(days=1)).isoformat(),
            end.isoformat(),
        ]


class TestFailureModes:
    def test_service_not_mirrored_to_ayla_refuses_clearly(
        self,
        client,
        bot_user,
        tenant,
        master,
        service,
        master_service,
        open_every_day,
        sunday,
    ):
        """An empty picker reads as "no free time"; a 409 says what is wrong."""
        service.ayla_service_id = None
        service.save(update_fields=["ayla_service_id"])
        fake, calls = _fake_client({})

        with patch(CLIENT_PATH, return_value=fake):
            resp = _get(client, master, service, sunday)

        assert resp.status_code == 409
        assert calls == [], "must not call Ayla with a missing service id"

    def test_upstream_outage_is_503_not_500(
        self,
        client,
        bot_user,
        master,
        service,
        master_service,
        open_every_day,
        sunday,
    ):
        fake, _ = _fake_client(raises=RuntimeError("ayla down"))

        with patch(CLIENT_PATH, return_value=fake):
            resp = _get(client, master, service, sunday)

        assert resp.status_code == 503


class TestFlagOffKeepsLocalComputation:
    def test_local_path_untouched_and_ayla_never_called(
        self,
        settings,
        client,
        bot_user,
        master,
        service,
        master_service,
        open_every_day,
        sunday,
    ):
        """Flag OFF deployments write bookings locally too, so reading the
        local schedule there is correct — the contract is unchanged."""
        settings.BOOKING_VIA_AYLA_REST = False
        fake, calls = _fake_client({})

        with patch(CLIENT_PATH, return_value=fake):
            resp = _get(client, master, service, sunday)

        assert resp.status_code == 200
        assert resp.json()["slots"], "local WorkingHours still drive this path"
        assert calls == []
