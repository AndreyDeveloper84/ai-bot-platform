"""Bookable slots for manual booking — UX contract §16, §17.

The load-bearing test here is the one that pins an upstream failure to a
non-200. An empty list and an unreachable schedule look identical to the
person holding the phone, and only one of them means «offer another day».
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from django.test import Client
from django.urls import reverse

from apps.admin_api.tests.conftest import init_data_header, make_master
from apps.catalog.models import CatalogService
from apps.integrations.ayla.booking_client import (
    AylaSlot,
    BookingBadRequestError,
    BookingUnavailableError,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _url(**params: str) -> str:
    base = reverse("admin_api:booking_slots")
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}" if qs else base


def _service(tenant: Tenant, *, bridged: bool = True) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=4242,
        external_updated_at=datetime.now(tz=timezone.utc),
        slug="manicure",
        name="Маникюр",
        duration_min=60,
        is_active=True,
        ayla_service_id=uuid.uuid4() if bridged else None,
    )


class _StubClient:
    def __init__(self, *, slots=None, exc: Exception | None = None) -> None:
        self.slots = slots or []
        self.exc = exc
        self.calls: list[dict] = []

    def get_available_times(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return self.slots


@pytest.fixture
def stub_slots(monkeypatch):
    def _install(stub: _StubClient) -> _StubClient:
        monkeypatch.setattr(
            "apps.integrations.ayla.booking_client.get_ayla_booking_client",
            lambda: stub,
        )
        return stub

    return _install


class TestSlots:
    def test_returns_slots_for_master_service_and_day(
        self, client: Client, owner_bot_user, tenant: Tenant, stub_slots
    ) -> None:
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant)
        stub_slots(
            _StubClient(
                slots=[
                    AylaSlot(time="15:00", datetime="2026-08-21T15:00:00+03:00", duration_s=3600),
                    AylaSlot(time="15:30", datetime=None, duration_s=None),
                ]
            )
        )

        resp = client.get(
            _url(master_id=str(master.id), service_id=str(service.id), date="2026-08-21"),
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert [s["time"] for s in data["slots"]] == ["15:00", "15:30"]
        assert data["slots"][0]["duration_min"] == 60
        assert data["slots"][1]["start_at"] is None
        assert data["duration_min"] == 60

    def test_translates_catalog_ids_to_ayla_ids(
        self, client: Client, owner_bot_user, tenant: Tenant, stub_slots
    ) -> None:
        """The Mini App speaks catalog ids; Ayla ids stay server-side."""
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant)
        stub = stub_slots(_StubClient())

        client.get(
            _url(master_id=str(master.id), service_id=str(service.id), date="2026-08-21"),
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        call = stub.calls[0]
        assert call["specialist_id"] == str(master.id)
        assert call["service_id"] == str(service.ayla_service_id)
        assert call["service_id"] != str(service.id)


class TestFailuresAreNotEmptiness:
    """§16/§17 — an unreachable schedule must not read as «no free time»."""

    def test_upstream_unavailable_is_503_not_an_empty_list(
        self, client: Client, owner_bot_user, tenant: Tenant, stub_slots
    ) -> None:
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant)
        stub_slots(_StubClient(exc=BookingUnavailableError("circuit_open")))

        resp = client.get(
            _url(master_id=str(master.id), service_id=str(service.id), date="2026-08-21"),
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 503
        assert resp.json()["error"] == "schedule_unavailable"
        assert "slots" not in resp.json()

    def test_upstream_rejection_is_502_not_an_empty_list(
        self, client: Client, owner_bot_user, tenant: Tenant, stub_slots
    ) -> None:
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant)
        stub_slots(_StubClient(exc=BookingBadRequestError("service_id_required")))

        resp = client.get(
            _url(master_id=str(master.id), service_id=str(service.id), date="2026-08-21"),
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 502
        assert resp.json()["error"] == "schedule_error"

    def test_genuinely_empty_day_is_200_with_no_slots(
        self, client: Client, owner_bot_user, tenant: Tenant, stub_slots
    ) -> None:
        """The other side of the same coin — a real «fully booked» answers 200."""
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant)
        stub_slots(_StubClient(slots=[]))

        resp = client.get(
            _url(master_id=str(master.id), service_id=str(service.id), date="2026-08-21"),
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 200
        assert resp.json()["slots"] == []

    def test_unbridged_service_refuses_instead_of_querying(
        self, client: Client, owner_bot_user, tenant: Tenant, stub_slots
    ) -> None:
        """A catalog row with no Ayla id cannot be booked — say so."""
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant, bridged=False)
        stub = stub_slots(_StubClient())

        resp = client.get(
            _url(master_id=str(master.id), service_id=str(service.id), date="2026-08-21"),
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "service_not_bookable"
        assert stub.calls == []


class TestValidation:
    @pytest.mark.parametrize("drop", ["master_id", "service_id", "date"])
    def test_every_parameter_is_required(
        self, client: Client, owner_bot_user, tenant: Tenant, drop: str
    ) -> None:
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant)
        params = {
            "master_id": str(master.id),
            "service_id": str(service.id),
            "date": "2026-08-21",
        }
        params.pop(drop)
        resp = client.get(_url(**params), HTTP_AUTHORIZATION=init_data_header("5001"))
        assert resp.status_code == 400
        assert drop in resp.json()["detail"]

    def test_bad_date_is_400(self, client: Client, owner_bot_user, tenant: Tenant) -> None:
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant)
        resp = client.get(
            _url(master_id=str(master.id), service_id=str(service.id), date="21-08-2026"),
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400

    def test_unknown_master_is_404(self, client: Client, owner_bot_user, tenant: Tenant) -> None:
        service = _service(tenant)
        resp = client.get(
            _url(master_id=str(uuid.uuid4()), service_id=str(service.id), date="2026-08-21"),
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 404

    def test_master_only_is_forbidden(
        self, client: Client, master_only_bot_user, tenant: Tenant
    ) -> None:
        resp = client.get(_url(date="2026-08-21"), HTTP_AUTHORIZATION=init_data_header("5004"))
        assert resp.status_code == 403
