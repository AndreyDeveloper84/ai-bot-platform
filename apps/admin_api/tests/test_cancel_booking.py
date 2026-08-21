"""Cancellation — §19, and the outcome vocabulary of §18.

The load-bearing test is
:meth:`TestOutcomes.test_timeout_is_pending_and_never_failed`. A
cancellation reported as failed invites a second press, and a second
press on an already-cancelled booking is how a customer is told twice
that their appointment is off.

Second: :meth:`TestScope.test_a_booking_of_another_salon_is_not_found` —
the surface must not confirm which appointment ids exist elsewhere.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from django.test import Client
from django.urls import reverse

from apps.admin_api.tests.conftest import init_data_header
from apps.booking.models import RemoteBookingProxy
from apps.integrations.ayla.salon_client import (
    SalonForbidden,
    SalonNotAllowed,
    SalonNotConfigured,
    SalonNotFound,
    SalonSlotTaken,
    SalonStaleVersion,
    SalonUnauthorized,
    SalonUnavailable,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _url(appointment_id) -> str:
    return reverse("admin_api:cancel_booking", args=[str(appointment_id)])


def _proxy(tenant: Tenant, *, appointment_id=None) -> RemoteBookingProxy:
    start = datetime.now(tz=timezone.utc) + timedelta(days=1)
    return RemoteBookingProxy.all_tenants.create(
        tenant=tenant,
        appointment_id=appointment_id or uuid.uuid4(),
        start_at=start,
        end_at=start + timedelta(hours=1),
        status=RemoteBookingProxy.Status.CONFIRMED,
    )


class _StubSalon:
    def __init__(self, *, exc: Exception | None = None) -> None:
        self.exc = exc
        self.calls: list[dict] = []

    def cancel_appointment(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return {"id": kwargs["appointment_id"], "status": "cancelled"}


@pytest.fixture
def stub_salon(monkeypatch):
    def _install(stub: _StubSalon) -> _StubSalon:
        monkeypatch.setattr(
            "apps.integrations.ayla.salon_client.get_salon_client",
            lambda: stub,
        )
        return stub

    return _install


def _post(client: Client, proxy, *, uid: str = "5001", **body):
    return client.post(
        _url(proxy.appointment_id),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(uid),
    )


class TestOutcomes:
    def test_committed_names_the_booking(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        proxy = _proxy(tenant)
        stub_salon(_StubSalon())

        resp = _post(client, proxy, reason_code="master_unavailable")

        assert resp.status_code == 200
        data = resp.json()
        assert data["outcome"] == "committed"
        assert data["appointment_id"] == str(proxy.appointment_id)

    def test_timeout_is_pending_and_never_failed(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        """The cancellation may have landed. Saying otherwise is worse
        than saying «unknown»: the customer gets told off twice."""
        proxy = _proxy(tenant)
        stub_salon(_StubSalon(exc=SalonUnavailable("network: ReadTimeout")))

        data = _post(client, proxy).json()

        assert data["outcome"] == "pending"
        assert data["outcome"] not in ("committed", "failed")

    def test_a_terminal_booking_is_blocked_not_conflict(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        """«Already finished» is settled, not contended.

        A conflict tells the receptionist to refresh and try again; this
        one will answer the same forever.
        """
        proxy = _proxy(tenant)
        stub_salon(_StubSalon(exc=SalonNotAllowed("visit already completed")))

        resp = _post(client, proxy)

        assert resp.status_code == 409
        assert resp.json()["outcome"] == "blocked"

    def test_somebody_got_there_first_is_a_conflict(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        proxy = _proxy(tenant)
        stub_salon(_StubSalon(exc=SalonStaleVersion("version moved")))

        assert _post(client, proxy).json()["outcome"] == "conflict"

    def test_upstream_401_is_blocked_and_leaks_nothing(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        proxy = _proxy(tenant)
        stub_salon(_StubSalon(exc=SalonUnauthorized("token_not_valid")))

        resp = _post(client, proxy)

        assert resp.status_code == 503
        assert resp.json()["outcome"] == "blocked"
        assert "token" not in resp.json()["detail"]

    def test_misconfiguration_is_blocked_not_pending(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        proxy = _proxy(tenant)
        stub_salon(_StubSalon(exc=SalonNotConfigured("token empty")))

        assert _post(client, proxy).json()["outcome"] == "blocked"

    def test_forbidden_is_blocked(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        proxy = _proxy(tenant)
        stub_salon(_StubSalon(exc=SalonForbidden("not a tenant admin")))

        resp = _post(client, proxy)

        assert resp.status_code == 403
        assert resp.json()["outcome"] == "blocked"

    def test_a_slot_conflict_shape_is_still_a_conflict(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        proxy = _proxy(tenant)
        stub_salon(_StubSalon(exc=SalonSlotTaken("raced")))

        assert _post(client, proxy).json()["outcome"] == "conflict"


class TestScope:
    def test_a_booking_of_another_salon_is_not_found(
        self, client: Client, tenant: Tenant, other_tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        stranger = _proxy(other_tenant)
        stub = stub_salon(_StubSalon())

        resp = _post(client, stranger)

        assert resp.status_code == 404
        # Never forwarded — Ayla must not be asked about somebody else's id.
        assert stub.calls == []

    def test_an_unknown_booking_is_not_found(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        stub = stub_salon(_StubSalon())

        resp = client.post(
            _url(uuid.uuid4()),
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )

        assert resp.status_code == 404
        assert stub.calls == []

    def test_a_master_only_caller_is_forbidden(
        self, client: Client, tenant: Tenant, master_only_bot_user, stub_salon
    ) -> None:
        proxy = _proxy(tenant)
        stub = stub_salon(_StubSalon())

        resp = _post(client, proxy, uid="5004")

        assert resp.status_code == 403
        assert stub.calls == []

    def test_upstream_404_reads_as_divergence_not_user_error(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        """The mirror says this salon owns it and Ayla says otherwise.

        That is our data being wrong, not the receptionist's action, so
        the screen is told to refresh rather than that they made it up.
        """
        proxy = _proxy(tenant)
        stub_salon(_StubSalon(exc=SalonNotFound("no such appointment")))

        resp = _post(client, proxy)

        assert resp.status_code == 409
        assert resp.json()["outcome"] == "conflict"


class TestAttributionAndReason:
    def test_the_actor_is_the_calling_admin(
        self, client: Client, tenant: Tenant, owner_bot_user, admin_bot_user, stub_salon
    ) -> None:
        proxy = _proxy(tenant)
        stub = stub_salon(_StubSalon())

        _post(client, proxy, uid="5002")

        assert stub.calls[0]["actor_external_id"] == (f"bot:max:{admin_bot_user.channel_user_id}")

    def test_the_actor_is_never_taken_from_the_body(
        self, client: Client, tenant: Tenant, owner_bot_user, admin_bot_user, stub_salon
    ) -> None:
        proxy = _proxy(tenant)
        stub = stub_salon(_StubSalon())

        _post(client, proxy, uid="5002", actor_external_id="bot:max:5001")

        assert stub.calls[0]["actor_external_id"] == (f"bot:max:{admin_bot_user.channel_user_id}")

    def test_a_reason_code_travels(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        proxy = _proxy(tenant)
        stub = stub_salon(_StubSalon())

        _post(client, proxy, reason_code="tenant_closed_slot", reason="салон закрыт")

        call = stub.calls[0]
        assert call["reason_code"] == "tenant_closed_slot"
        assert call["reason"] == "салон закрыт"

    def test_no_reason_code_is_sent_as_none(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        """Upstream defaults it to «other», which is the honest value
        when the salon has not said why. Inventing one here would put a
        claim in the record that nobody made."""
        proxy = _proxy(tenant)
        stub = stub_salon(_StubSalon())

        _post(client, proxy)

        assert stub.calls[0]["reason_code"] is None

    def test_an_over_long_reason_is_refused_locally(
        self, client: Client, tenant: Tenant, owner_bot_user, stub_salon
    ) -> None:
        proxy = _proxy(tenant)
        stub = stub_salon(_StubSalon())

        resp = _post(client, proxy, reason="я" * 900)

        assert resp.status_code == 400
        assert stub.calls == []
