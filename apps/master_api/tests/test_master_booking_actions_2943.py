"""DRF-2943 — Mini App bridge for the master's own booking actions."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone as dj_timezone

from apps.booking.models import RemoteBookingProxy
from apps.catalog.models import CatalogMaster
from apps.integrations.ayla.booking_client import BookingBadRequestError, BookingUnavailableError
from apps.master_api.tests.conftest import init_data_header, make_master

pytestmark = pytest.mark.django_db


def _visit(master: CatalogMaster, *, version: int | None = 7) -> RemoteBookingProxy:
    now = dj_timezone.now()
    return RemoteBookingProxy.all_tenants.create(
        tenant=master.tenant,
        appointment_id=uuid.uuid4(),
        specialist_id=master.id,
        start_at=now + timedelta(hours=2),
        end_at=now + timedelta(hours=3),
        status="confirmed",
        appointment_version=version,
    )


def _get(client: Client, appointment_id, uid: str = "12345"):
    return client.get(
        reverse("master_api:booking_detail", args=[appointment_id]),
        HTTP_AUTHORIZATION=init_data_header(uid),
    )


def _act(client: Client, appointment_id, body: dict, uid: str = "12345"):
    return client.post(
        reverse("master_api:booking_action", args=[appointment_id]),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(uid),
    )


class StubActions:
    def __init__(self, *, exc: Exception | None = None) -> None:
        self.exc = exc
        self.calls: list[dict] = []

    def act_as_specialist(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return SimpleNamespace(
            appointment_id=kwargs["appointment_id"],
            status="confirmed",
            version=kwargs.get("expected_version") or 9,
            start_at=kwargs.get("new_start_datetime") or "2026-10-21T15:00:00+03:00",
        )


@pytest.fixture
def stub_actions(monkeypatch):
    def install(stub: StubActions) -> StubActions:
        monkeypatch.setattr(
            "apps.master_api.views_bookings.get_ayla_booking_client",
            lambda: stub,
        )
        return stub

    return install


def test_detail_exposes_the_version_of_the_mirror_the_operator_saw(client, accepted_master):
    row = _visit(accepted_master, version=7)
    body = _get(client, row.appointment_id).json()
    assert body["appointment_version"] == 7


def test_reschedule_passes_exact_displayed_version_and_subject_identity(
    client, accepted_master, stub_actions
):
    row = _visit(accepted_master, version=7)
    stub = stub_actions(StubActions())

    response = _act(
        client,
        row.appointment_id,
        {
            "action": "reschedule",
            "expected_version": 7,
            "new_start_datetime": "2026-10-21T17:00:00+03:00",
        },
    )

    assert response.status_code == 200, response.content
    call = stub.calls[0]
    assert call["appointment_id"] == str(row.appointment_id)
    assert call["expected_version"] == 7
    assert call["new_start_datetime"] == "2026-10-21T17:00:00+03:00"
    assert call["external_user_id"] == "bot:max:12345"
    assert call["specialist_id"] == str(accepted_master.catalog_specialist_id)


def test_foreign_and_missing_are_both_404_before_outbound_call(
    client, tenant, accepted_master, stub_actions
):
    colleague = make_master(tenant, name="Ольга", external_id=901)
    foreign = _visit(colleague)
    stub = stub_actions(StubActions())

    foreign_response = _act(client, foreign.appointment_id, {"action": "cancel"})
    missing_response = _act(client, uuid.uuid4(), {"action": "cancel"})

    assert foreign_response.status_code == 404
    assert missing_response.status_code == 404
    assert foreign_response.json() == missing_response.json()
    assert stub.calls == []


def test_versioned_write_without_version_is_refused_before_outbound(
    client, accepted_master, stub_actions
):
    row = _visit(accepted_master, version=None)
    stub = stub_actions(StubActions())

    response = _act(
        client,
        row.appointment_id,
        {
            "action": "reschedule",
            "new_start_datetime": "2026-10-21T17:00:00+03:00",
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "outcome": "conflict",
        "reason_code": "version_unknown",
    }
    assert stub.calls == []


def test_stale_version_surfaces_conflict_and_is_never_retried(
    client, accepted_master, stub_actions
):
    row = _visit(accepted_master, version=7)
    stub = stub_actions(
        StubActions(
            exc=BookingBadRequestError(
                "stale",
                status_code=409,
                code="STALE_VERSION",
                details={"current_version": 8},
            )
        )
    )

    response = _act(
        client,
        row.appointment_id,
        {
            "action": "reschedule",
            "expected_version": 7,
            "new_start_datetime": "2026-10-21T17:00:00+03:00",
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "outcome": "conflict",
        "reason_code": "stale_version",
    }
    assert len(stub.calls) == 1


def test_unknown_result_becomes_pending_not_a_retryable_failure(
    client, accepted_master, stub_actions
):
    row = _visit(accepted_master, version=7)
    stub = stub_actions(StubActions(exc=BookingUnavailableError("timeout")))

    response = _act(
        client,
        row.appointment_id,
        {"action": "cancel"},
    )

    assert response.status_code == 202
    assert response.json() == {
        "outcome": "pending",
        "reason_code": "result_pending",
    }
    assert len(stub.calls) == 1


@pytest.mark.parametrize("action", ["delete", "", None, 123])
def test_unknown_action_is_rejected_before_outbound(client, accepted_master, stub_actions, action):
    row = _visit(accepted_master)
    stub = stub_actions(StubActions())

    response = _act(client, row.appointment_id, {"action": action})

    assert response.status_code == 400
    assert stub.calls == []
