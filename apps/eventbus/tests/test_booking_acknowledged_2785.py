"""DRF-2785 — the bot accepts ``booking.acknowledged`` (the master's «✅ Подтверждаю»).

The catalog emits it when a master acknowledges their own appointment; the
booking's status is unchanged. Before this, the name was not in the bot's
lists: the envelope parser refused it as ``invalid_event_name`` and the
dispatcher dead-lettered it — so the catalog topic could not be switched on.

Accepted means: parsed, dispatched to a registered v1 handler, checked
against the tenant and the mirror, logged. The client is not written to —
those words are the owner's, and the handler says so.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any
from uuid import UUID

import pytest

from apps.booking.models import RemoteBookingProxy
from apps.eventbus.ingest_envelope import IngestEnvelope, parse_envelope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

TENANT_ID = "4c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f2785"
OTHER_TENANT_ID = "5d4b8f2c-5e63-4a9f-84b2-8d3e9f2a2785"
CLIENT_ID = "e1a2b3c4-d5e6-4789-9abc-def012342785"
APPOINTMENT_ID = "a8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f62785"


def _data() -> dict[str, Any]:
    return {
        "appointment_id": APPOINTMENT_ID,
        "client_id": CLIENT_ID,
        "specialist_id": "7c2d8e1f-0a5c-4c3a-9e1b-4d52f8eb2785",
        "start_at": "2026-10-06T15:00:00+03:00",
        "version": 1,
        "acknowledged_at": "2026-10-05T12:00:00+00:00",
        "acknowledged_by": "specialist",
        "acknowledged_by_user_id": "f2b3c4d5-e6f7-4890-abcd-ef0123452785",
    }


def _wire(*, event_id: str = "01J9ACK0000000000000002785") -> dict[str, Any]:
    """The envelope as the catalog sends it (actor «admin» — specialist → admin)."""

    return {
        "event_id": event_id,
        "event_name": "booking.acknowledged",
        "event_version": 1,
        "occurred_at": "2026-10-05T12:00:00Z",
        "tenant_id": TENANT_ID,
        "user_id": CLIENT_ID,
        "actor": "admin",
        "correlation_id": "a1b2c3d4-e5f6-7890-abcd-ef1234562785",
        "causation_id": None,
        "data": _data(),
    }


@pytest.fixture(autouse=True)
def _allowlist(settings) -> None:
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = False
    settings.EVENT_INGEST_ALLOWED_TENANTS = frozenset({TENANT_ID, OTHER_TENANT_ID})
    settings.EVENT_INGEST_ALLOWED_EVENTS = frozenset({"booking.acknowledged"})


@pytest.fixture
def tenant() -> Tenant:
    obj, _ = Tenant.objects.get_or_create(
        id=TENANT_ID, defaults={"slug": "t-ack-2785", "name": "Ack tenant"}
    )
    return obj


def _proxy(tenant: Tenant) -> RemoteBookingProxy:
    return RemoteBookingProxy.all_tenants.create(
        appointment_id=UUID(APPOINTMENT_ID),
        tenant=tenant,
        bot_user=None,
        start_at=dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.timezone.utc),
        end_at=dt.datetime(2026, 10, 6, 13, 0, tzinfo=dt.timezone.utc),
        status=RemoteBookingProxy.Status.CONFIRMED,
    )


def _parsed(**kw) -> IngestEnvelope:
    return parse_envelope(json.loads(json.dumps(_wire(**kw))))


class TestAccepted:
    def test_a1_the_envelope_parses(self) -> None:
        env = _parsed()
        assert (env.event_name, env.event_version, env.actor) == (
            "booking.acknowledged",
            1,
            "admin",
        )

    def test_a2_dispatch_reaches_a_handler_once(self, tenant: Tenant) -> None:
        from apps.eventbus.ingest_dispatcher import DispatchOutcome, dispatch_envelope

        _proxy(tenant)
        env = _parsed()

        assert dispatch_envelope(env).outcome == DispatchOutcome.OK
        assert dispatch_envelope(env).outcome == DispatchOutcome.DUPLICATE

    def test_a3_the_status_is_not_touched(self, tenant: Tenant) -> None:
        from apps.eventbus.ingest_dispatcher import dispatch_envelope

        _proxy(tenant)
        dispatch_envelope(_parsed())

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.status == RemoteBookingProxy.Status.CONFIRMED

    def test_a4_another_salons_appointment_dead_letters(self, tenant: Tenant) -> None:
        # The proxy belongs to another tenant: the tenant guard of every
        # booking.* handler applies here too, not just a log line.
        from apps.eventbus.ingest_dispatcher import DispatchOutcome, dispatch_envelope

        other, _ = Tenant.objects.get_or_create(
            id=OTHER_TENANT_ID, defaults={"slug": "t-ack-other", "name": "Other"}
        )
        _proxy(other)

        assert dispatch_envelope(_parsed()).outcome != DispatchOutcome.OK
