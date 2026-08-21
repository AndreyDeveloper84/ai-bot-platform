"""Manual booking commit boundary — UX contract §18 + attribution.

Two tests here are load-bearing and were named as requirements rather than
found by me:

* **attribution** — an action taken by an administrator must carry that
  administrator's identity, not the owner's. Path Б was chosen for exactly
  this, so a journal that credits the wrong person defeats the decision;
* **pending is not failure** — a write that times out may have landed, and a
  surface that calls it a failure invites a second booking.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from django.test import Client
from django.urls import reverse

from apps.admin_api.tests.conftest import init_data_header, make_master
from apps.catalog.models import CatalogService
from apps.integrations.ayla.salon_client import (
    SalonForbidden,
    SalonNotConfigured,
    SalonSlotTaken,
    SalonUnauthorized,
    SalonUnavailable,
    SalonValidationError,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _url() -> str:
    return reverse("admin_api:create_booking")


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


class _StubSalon:
    def __init__(self, *, exc: Exception | None = None, result: dict | None = None) -> None:
        self.exc = exc
        self.result = result or {"id": str(uuid.uuid4())}
        self.calls: list[dict] = []

    def create_appointment(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return self.result


@pytest.fixture
def stub_salon(monkeypatch):
    def _install(stub: _StubSalon) -> _StubSalon:
        monkeypatch.setattr(
            "apps.integrations.ayla.salon_client.get_salon_client",
            lambda: stub,
        )
        return stub

    return _install


def _post(client: Client, tenant: Tenant, master, service, *, uid="5001", **over):
    body = {
        "master_id": str(master.id),
        "service_id": str(service.id),
        "start_at": "2026-08-21T15:00:00+03:00",
        "client_name": "Мария",
        "client_phone": "+79990000000",
    }
    body.update(over)
    return client.post(
        _url(),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(uid),
    )


class TestAttribution:
    """Path Б exists so the journal names the person who pressed the button."""

    def test_actor_is_the_calling_admin_not_the_owner(
        self, client: Client, tenant: Tenant, owner_bot_user, admin_bot_user, stub_salon
    ) -> None:
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant)
        stub = stub_salon(_StubSalon())

        # The ADMIN books (uid 5002), while an owner also exists in this salon.
        resp = _post(client, tenant, master, service, uid="5002")
        assert resp.status_code == 201

        actor = stub.calls[0]["actor_external_id"]
        assert actor == f"bot:max:{admin_bot_user.channel_user_id}"
        assert owner_bot_user.channel_user_id not in actor

    def test_actor_is_never_taken_from_the_body(
        self, client: Client, tenant: Tenant, owner_bot_user, admin_bot_user, stub_salon
    ) -> None:
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant)
        stub = stub_salon(_StubSalon())

        # A body that tries to name somebody else must change nothing.
        resp = _post(
            client,
            tenant,
            master,
            service,
            uid="5002",
            actor_external_id="bot:max:5001",
            external_user_id="bot:max:5001",
        )
        assert resp.status_code == 201
        assert stub.calls[0]["actor_external_id"] == f"bot:max:{admin_bot_user.channel_user_id}"


class TestOutcomes:
    """§18 — four presentation outcomes, and they are not interchangeable."""

    def test_committed_carries_the_appointment_id(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant)
        appt = str(uuid.uuid4())
        stub_salon(_StubSalon(result={"id": appt}))

        resp = _post(client, tenant, master, service)
        assert resp.status_code == 201
        data = resp.json()
        assert data["outcome"] == "committed"
        assert data["appointment_id"] == appt

    def test_slot_taken_is_conflict(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant)
        stub_salon(_StubSalon(exc=SalonSlotTaken("slot gone")))

        resp = _post(client, tenant, master, service)
        assert resp.status_code == 409
        assert resp.json()["outcome"] == "conflict"

    def test_forbidden_is_blocked(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant)
        stub_salon(_StubSalon(exc=SalonForbidden("not a tenant admin")))

        resp = _post(client, tenant, master, service)
        assert resp.status_code == 403
        assert resp.json()["outcome"] == "blocked"

    def test_timeout_is_pending_and_never_failed(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        """The single most important line of §18: «Do not claim creation».

        It must also not claim the opposite. The write may have landed, so
        the answer is «unknown» — anything else invites a double booking.
        """
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant)
        stub_salon(_StubSalon(exc=SalonUnavailable("network: ReadTimeout")))

        resp = _post(client, tenant, master, service)
        data = resp.json()
        assert data["outcome"] == "pending"
        assert data["outcome"] not in ("committed", "failed")
        # The key comes back so a retry can be the same write, not a new one.
        assert data["idempotency_key"]

    def test_upstream_401_is_blocked_and_not_failed(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        """Our credential being refused is not the booking failing.

        As of 2026-08-21 this is what live Ayla does to a service Bearer on
        the salon endpoints (DRF-1231). Reported as blocked so the screen
        does not offer a retry that cannot possibly succeed.
        """
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant)
        stub_salon(_StubSalon(exc=SalonUnauthorized("token_not_valid")))

        resp = _post(client, tenant, master, service)
        assert resp.status_code == 503
        data = resp.json()
        assert data["outcome"] == "blocked"
        assert data["outcome"] != "failed"
        # Nothing the administrator can act on — so no upstream text leaks.
        assert "token" not in data["detail"]

    def test_validation_error_is_blocked_not_conflict(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant)
        stub_salon(_StubSalon(exc=SalonValidationError("booking window invalid")))

        resp = _post(client, tenant, master, service)
        assert resp.json()["outcome"] == "blocked"

    def test_misconfigured_deployment_is_blocked_not_pending(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        """A missing token is a certainty, not an unknown — say so."""
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant)
        stub_salon(_StubSalon(exc=SalonNotConfigured("token empty")))

        resp = _post(client, tenant, master, service)
        assert resp.status_code == 503
        assert resp.json()["outcome"] == "blocked"


class TestIdempotency:
    def test_client_key_is_passed_through_unchanged(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        """A repeat submission must be able to repeat its key.

        Ayla invents one when the header is absent, so a retry without a
        stable key books the customer twice.
        """
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant)
        stub = stub_salon(_StubSalon())
        key = str(uuid.uuid4())

        _post(client, tenant, master, service, idempotency_key=key)
        assert stub.calls[0]["idempotency_key"] == key

    def test_a_key_is_generated_when_the_client_omits_one(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant)
        stub = stub_salon(_StubSalon())

        _post(client, tenant, master, service)
        assert stub.calls[0]["idempotency_key"]


class TestIdentificationPaths:
    """§14 — exactly one of «existing customer» or «new guest»."""

    def test_new_guest_needs_a_phone(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant)
        stub = stub_salon(_StubSalon())

        resp = _post(client, tenant, master, service, client_phone="")
        assert resp.status_code == 400
        assert stub.calls == []

    def test_both_paths_at_once_is_refused(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant)
        stub = stub_salon(_StubSalon())

        resp = _post(client, tenant, master, service, client_id=str(uuid.uuid4()))
        assert resp.status_code == 400
        assert stub.calls == []

    def test_existing_customer_path_sends_client_id_only(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant)
        stub = stub_salon(_StubSalon())
        cid = str(uuid.uuid4())

        resp = _post(
            client,
            tenant,
            master,
            service,
            client_id=cid,
            client_name="",
            client_phone="",
        )
        assert resp.status_code == 201
        call = stub.calls[0]
        assert call["client_id"] == cid
        assert call["client_name"] is None


class TestGuards:
    def test_master_of_another_salon_is_404(
        self, client: Client, tenant: Tenant, other_tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        stranger = make_master(other_tenant, name="Чужая", external_id=7)
        service = _service(tenant)
        stub = stub_salon(_StubSalon())

        resp = _post(client, tenant, stranger, service)
        assert resp.status_code == 404
        assert stub.calls == []

    def test_unbridged_service_never_reaches_ayla(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant, bridged=False)
        stub = stub_salon(_StubSalon())

        resp = _post(client, tenant, master, service)
        assert resp.status_code == 409
        assert stub.calls == []

    def test_catalog_service_id_is_translated_to_the_ayla_id(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant)
        stub = stub_salon(_StubSalon())

        _post(client, tenant, master, service)
        call = stub.calls[0]
        assert call["service_id"] == str(service.ayla_service_id)
        assert call["service_id"] != str(service.id)

    def test_master_only_caller_is_forbidden(
        self, client: Client, tenant: Tenant, master_only_bot_user, stub_salon
    ) -> None:
        master = make_master(tenant, name="Анна", external_id=1)
        service = _service(tenant)
        stub = stub_salon(_StubSalon())

        resp = _post(client, tenant, master, service, uid="5004")
        assert resp.status_code == 403
        assert stub.calls == []
