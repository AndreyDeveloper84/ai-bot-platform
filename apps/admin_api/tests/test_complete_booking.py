"""Closing a visit — and the reason the version read is a separate call.

The load-bearing test is
:meth:`TestTheGuardCanActuallyFire.test_the_version_is_never_fetched_by_the_write`.
`expected_version` protects against acting on a booking that changed
since the operator looked at it. A server that fetched the version inside
the same request that writes would always send the current one, and the
guard would match every time — machinery that runs and catches nothing.

That is the DRF-1232 defect exactly, one repo over: a fresh idempotency
key invented per request, and a unique constraint that stood without ever
firing. Repeating it here would be worse for having been seen once.
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
from apps.integrations.ayla.booking_client import (
    BookingAPIError,
    BookingUnavailableError,
)
from apps.integrations.ayla.salon_client import (
    SalonForbidden,
    SalonNotAllowed,
    SalonNotFound,
    SalonStaleVersion,
    SalonUnauthorized,
    SalonUnavailable,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _read_url(appointment_id) -> str:
    return reverse("admin_api:booking_version", args=[str(appointment_id)])


def _complete_url(appointment_id) -> str:
    return reverse("admin_api:complete_booking", args=[str(appointment_id)])


def _proxy(tenant: Tenant) -> RemoteBookingProxy:
    start = datetime.now(tz=timezone.utc) - timedelta(hours=2)
    return RemoteBookingProxy.all_tenants.create(
        tenant=tenant,
        appointment_id=uuid.uuid4(),
        start_at=start,
        end_at=start + timedelta(hours=1),
        status=RemoteBookingProxy.Status.CONFIRMED,
    )


class _Version:
    def __init__(self, version=3, status="confirmed"):
        self.id = "appt"
        self.version = version
        self.status = status
        self.start_datetime = "2026-08-21T15:00:00+03:00"


class _StubBooking:
    """Stands in for the internal-tree client (the canonical read)."""

    def __init__(self, *, exc: Exception | None = None, record=None) -> None:
        self.exc = exc
        self.record = record or _Version()
        self.calls: list[dict] = []

    def get_appointment_version(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return self.record


class _StubSalon:
    """Stands in for the salon surface (the write)."""

    def __init__(self, *, exc: Exception | None = None) -> None:
        self.exc = exc
        self.calls: list[dict] = []

    def complete_appointment(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return {"id": kwargs["appointment_id"], "status": "completed"}


@pytest.fixture
def stubs(monkeypatch):
    def _install(*, booking=None, salon=None):
        b = booking or _StubBooking()
        s = salon or _StubSalon()
        monkeypatch.setattr(
            "apps.integrations.ayla.booking_client.get_ayla_booking_client",
            lambda: b,
        )
        monkeypatch.setattr(
            "apps.integrations.ayla.salon_client.get_salon_client",
            lambda: s,
        )
        return b, s

    return _install


def _get(client: Client, proxy, *, uid="5001"):
    return client.get(
        _read_url(proxy.appointment_id),
        HTTP_AUTHORIZATION=init_data_header(uid),
    )


def _post(client: Client, proxy, *, uid="5001", **body):
    return client.post(
        _complete_url(proxy.appointment_id),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(uid),
    )


class TestTheGuardCanActuallyFire:
    def test_the_version_is_never_fetched_by_the_write(
        self, client: Client, tenant: Tenant, owner_bot_user, stubs
    ) -> None:
        """The whole reason these are two endpoints.

        If the write read the version itself, it would send whatever is
        current and the guard would match every time — protecting
        nothing, exactly like an idempotency key invented per request.
        """
        proxy = _proxy(tenant)
        booking, salon = stubs()

        _post(client, proxy, expected_version=3)

        assert booking.calls == []
        assert salon.calls[0]["expected_version"] == 3

    def test_the_operators_version_is_sent_verbatim(
        self, client: Client, tenant: Tenant, owner_bot_user, stubs
    ) -> None:
        """Even when it is stale — that is the point. Ayla decides."""
        proxy = _proxy(tenant)
        _, salon = stubs()

        _post(client, proxy, expected_version=1)

        assert salon.calls[0]["expected_version"] == 1

    def test_a_stale_version_sends_the_operator_back_to_look(
        self, client: Client, tenant: Tenant, owner_bot_user, stubs
    ) -> None:
        proxy = _proxy(tenant)
        stubs(salon=_StubSalon(exc=SalonStaleVersion("moved")))

        resp = _post(client, proxy, expected_version=2)

        assert resp.status_code == 409
        data = resp.json()
        assert data["outcome"] == "conflict"
        assert "обновите" in data["detail"]


class TestVersionIsRequired:
    @pytest.mark.parametrize("body", [{}, {"expected_version": None}])
    def test_a_missing_version_is_refused_before_the_write(
        self, client: Client, tenant: Tenant, owner_bot_user, stubs, body
    ) -> None:
        proxy = _proxy(tenant)
        _, salon = stubs()

        resp = _post(client, proxy, **body)

        assert resp.status_code == 400
        assert salon.calls == []

    @pytest.mark.parametrize("value", ["3", 0, -1, 1.5])
    def test_a_bogus_version_never_reaches_ayla(
        self, client: Client, tenant: Tenant, owner_bot_user, stubs, value
    ) -> None:
        proxy = _proxy(tenant)
        _, salon = stubs()

        resp = _post(client, proxy, expected_version=value)

        assert resp.status_code == 400
        assert salon.calls == []

    def test_true_is_not_a_version(
        self, client: Client, tenant: Tenant, owner_bot_user, stubs
    ) -> None:
        """`isinstance(True, int)` is True in Python — a real footgun for
        a field that decides whether a concurrency guard fires."""
        proxy = _proxy(tenant)
        _, salon = stubs()

        resp = _post(client, proxy, expected_version=True)

        assert resp.status_code == 400
        assert salon.calls == []


class TestTheRead:
    def test_returns_the_canonical_facts(
        self, client: Client, tenant: Tenant, owner_bot_user, stubs
    ) -> None:
        proxy = _proxy(tenant)
        stubs(booking=_StubBooking(record=_Version(version=7, status="confirmed")))

        data = _get(client, proxy).json()

        assert data["version"] == 7
        assert data["status"] == "confirmed"

    def test_the_actor_is_the_calling_admin(
        self, client: Client, tenant: Tenant, owner_bot_user, admin_bot_user, stubs
    ) -> None:
        proxy = _proxy(tenant)
        booking, _ = stubs()

        _get(client, proxy, uid="5002")

        assert booking.calls[0]["external_user_id"] == (f"bot:max:{admin_bot_user.channel_user_id}")

    @pytest.mark.parametrize(
        "exc",
        [BookingUnavailableError("timeout"), BookingAPIError("boom")],
    )
    def test_an_unreadable_version_is_503_and_not_a_guess(
        self, client: Client, tenant: Tenant, owner_bot_user, stubs, exc
    ) -> None:
        """No version means no action. Defaulting to 1 would aim the
        write at whatever revision happens to exist."""
        proxy = _proxy(tenant)
        stubs(booking=_StubBooking(exc=exc))

        resp = _get(client, proxy)

        assert resp.status_code == 503
        assert "version" not in resp.json()

    def test_a_booking_of_another_salon_is_not_found(
        self, client: Client, tenant: Tenant, other_tenant: Tenant, owner_bot_user, stubs
    ) -> None:
        stranger = _proxy(other_tenant)
        booking, _ = stubs()

        resp = _get(client, stranger)

        assert resp.status_code == 404
        assert booking.calls == []


class TestOutcomes:
    def test_committed(self, client: Client, tenant: Tenant, owner_bot_user, stubs) -> None:
        proxy = _proxy(tenant)
        stubs()

        resp = _post(client, proxy, expected_version=3)

        assert resp.status_code == 200
        assert resp.json()["outcome"] == "committed"

    def test_timeout_is_pending_and_never_failed(
        self, client: Client, tenant: Tenant, owner_bot_user, stubs
    ) -> None:
        proxy = _proxy(tenant)
        stubs(salon=_StubSalon(exc=SalonUnavailable("network: ReadTimeout")))

        data = _post(client, proxy, expected_version=3).json()

        assert data["outcome"] == "pending"
        assert data["outcome"] != "failed"

    def test_an_already_closed_visit_is_blocked_not_conflict(
        self, client: Client, tenant: Tenant, owner_bot_user, stubs
    ) -> None:
        """Settled, not contended: refreshing will not change the answer."""
        proxy = _proxy(tenant)
        stubs(salon=_StubSalon(exc=SalonNotAllowed("already completed")))

        resp = _post(client, proxy, expected_version=3)

        assert resp.status_code == 409
        assert resp.json()["outcome"] == "blocked"

    def test_upstream_401_is_blocked_and_leaks_nothing(
        self, client: Client, tenant: Tenant, owner_bot_user, stubs
    ) -> None:
        proxy = _proxy(tenant)
        stubs(salon=_StubSalon(exc=SalonUnauthorized("token_not_valid")))

        resp = _post(client, proxy, expected_version=3)

        assert resp.status_code == 503
        assert resp.json()["outcome"] == "blocked"
        assert "token" not in resp.json()["detail"]

    def test_forbidden_is_blocked(
        self, client: Client, tenant: Tenant, owner_bot_user, stubs
    ) -> None:
        proxy = _proxy(tenant)
        stubs(salon=_StubSalon(exc=SalonForbidden("not a tenant admin")))

        assert _post(client, proxy, expected_version=3).status_code == 403

    def test_upstream_404_reads_as_divergence(
        self, client: Client, tenant: Tenant, owner_bot_user, stubs
    ) -> None:
        proxy = _proxy(tenant)
        stubs(salon=_StubSalon(exc=SalonNotFound("no such appointment")))

        assert _post(client, proxy, expected_version=3).json()["outcome"] == "conflict"


class TestScope:
    def test_a_booking_of_another_salon_is_not_closed(
        self, client: Client, tenant: Tenant, other_tenant: Tenant, owner_bot_user, stubs
    ) -> None:
        stranger = _proxy(other_tenant)
        _, salon = stubs()

        resp = _post(client, stranger, expected_version=3)

        assert resp.status_code == 404
        assert salon.calls == []

    def test_a_master_only_caller_is_forbidden(
        self, client: Client, tenant: Tenant, master_only_bot_user, stubs
    ) -> None:
        proxy = _proxy(tenant)
        _, salon = stubs()

        resp = _post(client, proxy, uid="5004", expected_version=3)

        assert resp.status_code == 403
        assert salon.calls == []

    def test_the_actor_is_never_taken_from_the_body(
        self, client: Client, tenant: Tenant, owner_bot_user, admin_bot_user, stubs
    ) -> None:
        proxy = _proxy(tenant)
        _, salon = stubs()

        _post(
            client,
            proxy,
            uid="5002",
            expected_version=3,
            actor_external_id="bot:max:5001",
        )

        assert salon.calls[0]["actor_external_id"] == (f"bot:max:{admin_bot_user.channel_user_id}")
