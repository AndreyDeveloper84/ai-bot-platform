"""«Не пришёл» силами салона из бота (DRF-1851, карта кабинета K8, OD-V1).

Что заперто:

- ручка зовёт ``mark_no_show`` салонного клиента — не ``complete`` и не
  запись статуса в зеркало: статус меняет машина состояний Ayla, зеркало
  следует её событию;
- версия — та, что показали оператору, обязательна, не выдумывается;
- те же исходы, что у закрытия (общий ``_settle_visit``): committed /
  pending на таймаут / blocked на уже закрытый / conflict на устаревшую
  версию;
- чужой салон — 404 без вызова; мастер без роли админа — 403 без вызова;
- положительная стража: ``complete`` по-прежнему зовёт ``complete_appointment``.
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
    SalonNotAllowed,
    SalonStaleVersion,
    SalonUnavailable,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _proxy(tenant: Tenant) -> RemoteBookingProxy:
    start = datetime.now(tz=timezone.utc) - timedelta(hours=2)
    return RemoteBookingProxy.all_tenants.create(
        tenant=tenant,
        appointment_id=uuid.uuid4(),
        start_at=start,
        end_at=start + timedelta(hours=1),
        status=RemoteBookingProxy.Status.CONFIRMED,
    )


class _StubSalon:
    def __init__(self, *, exc: Exception | None = None) -> None:
        self.exc = exc
        self.calls: list[tuple[str, dict]] = []

    def mark_no_show(self, **kwargs):
        self.calls.append(("mark_no_show", kwargs))
        if self.exc:
            raise self.exc
        return {"id": kwargs["appointment_id"], "status": "no_show"}

    def complete_appointment(self, **kwargs):
        self.calls.append(("complete_appointment", kwargs))
        return {"id": kwargs["appointment_id"], "status": "completed"}


@pytest.fixture
def salon(monkeypatch):
    def _install(stub: _StubSalon | None = None) -> _StubSalon:
        s = stub or _StubSalon()
        monkeypatch.setattr("apps.integrations.ayla.salon_client.get_salon_client", lambda: s)
        return s

    return _install


def _post(client: Client, proxy, *, name="no_show_booking", uid="5001", **body):
    return client.post(
        reverse(f"admin_api:{name}", args=[str(proxy.appointment_id)]),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(uid),
    )


class TestItGoesThroughTheStateMachine:
    def test_committed_calls_mark_no_show_with_the_operators_version(
        self, client: Client, tenant: Tenant, owner_bot_user, salon
    ) -> None:
        proxy = _proxy(tenant)
        stub = salon()

        resp = _post(client, proxy, expected_version=7)

        assert resp.status_code == 200, resp.content
        assert resp.json()["outcome"] == "committed"
        assert [c[0] for c in stub.calls] == ["mark_no_show"]
        sent = stub.calls[0][1]
        assert sent["appointment_id"] == str(proxy.appointment_id)
        assert sent["expected_version"] == 7
        assert sent["tenant_slug"] == tenant.slug
        # Зеркало не трогается ручкой — оно следует событию Ayla.
        proxy.refresh_from_db()
        assert proxy.status == RemoteBookingProxy.Status.CONFIRMED

    def test_complete_still_calls_complete(
        self, client: Client, tenant: Tenant, owner_bot_user, salon
    ) -> None:
        proxy = _proxy(tenant)
        stub = salon()

        assert _post(client, proxy, name="complete_booking", expected_version=3).status_code == 200
        assert [c[0] for c in stub.calls] == ["complete_appointment"]


class TestVersion:
    @pytest.mark.parametrize("body", [{}, {"expected_version": 0}, {"expected_version": True}])
    def test_a_missing_or_bogus_version_never_reaches_ayla(
        self, client: Client, tenant: Tenant, owner_bot_user, salon, body
    ) -> None:
        proxy = _proxy(tenant)
        stub = salon()

        assert _post(client, proxy, **body).status_code == 400
        assert stub.calls == []

    def test_a_stale_version_sends_the_operator_back_to_look(
        self, client: Client, tenant: Tenant, owner_bot_user, salon
    ) -> None:
        proxy = _proxy(tenant)
        salon(_StubSalon(exc=SalonStaleVersion("moved")))

        resp = _post(client, proxy, expected_version=3)

        assert resp.status_code == 409
        assert resp.json()["outcome"] == "conflict"


class TestOutcomes:
    def test_timeout_is_pending_and_never_failed(
        self, client: Client, tenant: Tenant, owner_bot_user, salon
    ) -> None:
        proxy = _proxy(tenant)
        salon(_StubSalon(exc=SalonUnavailable("network: ReadTimeout")))

        data = _post(client, proxy, expected_version=3).json()

        assert data["outcome"] == "pending"

    def test_an_already_settled_visit_is_blocked(
        self, client: Client, tenant: Tenant, owner_bot_user, salon
    ) -> None:
        proxy = _proxy(tenant)
        salon(_StubSalon(exc=SalonNotAllowed("already completed")))

        resp = _post(client, proxy, expected_version=3)

        assert resp.status_code == 409
        assert resp.json()["outcome"] == "blocked"


class TestScope:
    def test_a_booking_of_another_salon_is_not_touched(
        self, client: Client, tenant: Tenant, other_tenant: Tenant, owner_bot_user, salon
    ) -> None:
        stranger = _proxy(other_tenant)
        stub = salon()

        assert _post(client, stranger, expected_version=3).status_code == 404
        assert stub.calls == []

    def test_a_master_only_caller_is_forbidden(
        self, client: Client, tenant: Tenant, master_only_bot_user, salon
    ) -> None:
        proxy = _proxy(tenant)
        stub = salon()

        assert _post(client, proxy, uid="5004", expected_version=3).status_code == 403
        assert stub.calls == []
