"""DRF-1140 — internal domain-bus emit from the ``booking.created`` consumer.

The internal outbox (:class:`DomainEvent`, dot-notation bus) was fed by
exactly one writer: the ``post_save`` receiver on
:class:`apps.booking.models.BookingRequest`
(:mod:`apps.eventbus.signals`). Under ``BOOKING_VIA_AYLA_REST=True`` the
Ayla-first create surfaces (Mini App, admin console) never write that
table — the real storage is the :class:`RemoteBookingProxy` mirror,
written by the ``booking.created`` round-trip consumer. Result: the
internal bus saw zero ``booking.created`` /
``booking.attribution.assigned``, the audit journal was empty and
``billable`` counters read zero.

This suite pins the fix:

* flag ON — :func:`handle_booking_created` re-emits both events onto the
  internal bus, once per appointment, keyed by the canonical Ayla
  appointment id, on BOTH exits of the proxy upsert (a dialog booking
  lands on the advanced-state no-op branch);
* flag ON — the ``post_save`` receiver stays silent, so the dialog
  booking (which still writes its billing ``BookingRequest``) is not
  double-counted;
* flag OFF — the consumer emits nothing and the ``post_save`` receiver
  keeps owning the mode (covered by ``test_signals.py`` /
  ``test_attribution_signal.py``, which run with the default flag).
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import UUID

import pytest
from django.test import override_settings
from freezegun import freeze_time

from apps.booking.models import BookingRequest, RemoteBookingProxy
from apps.eventbus import vocabulary as V
from apps.eventbus.consumers.booking import handle_booking_created
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.eventbus.models import DomainEvent
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

AYLA_USER_ID = "f1a2b3c4-d5e6-4789-9abc-def012345678"
TENANT_ID = "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c"
APPOINTMENT_ID = "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8"
SERVICE_ID = "3d5f7e1c-8a2d-4e6f-b9c0-1d2e3f4a5b6c"
SPECIALIST_ID = "7c2d8e1f-0a5c-4c3a-9e1b-4d52f8eb3a17"


@pytest.fixture(autouse=True)
def _flag_on(settings) -> None:
    """Every test in this module exercises the Ayla-REST booking path."""
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture(autouse=True)
def _freeze_clock():
    with freeze_time("2026-05-20T10:00:00Z"):
        yield


@pytest.fixture(autouse=True)
def _pilot_allowlist(settings) -> None:
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = False
    settings.EVENT_INGEST_ALLOWED_TENANTS = frozenset({TENANT_ID})
    settings.EVENT_INGEST_ALLOWED_EVENTS = frozenset({"booking.created"})


@pytest.fixture
def tenant(db, _pilot_allowlist) -> Tenant:
    obj, _ = Tenant.objects.get_or_create(
        id=TENANT_ID, defaults={"slug": "t-1140", "name": "DRF-1140 tenant"}
    )
    return obj


@pytest.fixture
def bot_user_linked(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="9001",
        chat_id="chat-9001",
        ayla_user_id=AYLA_USER_ID,
    )


def _envelope(
    *,
    data: dict[str, Any],
    event_id: str = "01J9HXKM8Z2T4V6R8Q1P3D5F7E",  # pragma: allowlist secret
) -> IngestEnvelope:
    return IngestEnvelope(
        event_id=event_id,
        event_name="booking.created",
        event_version=1,
        occurred_at=dt.datetime(2026, 5, 21, 14, 32, 11, tzinfo=dt.timezone.utc),
        tenant_id=TENANT_ID,
        user_id=AYLA_USER_ID,
        actor="user",
        correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        causation_id=None,
        data=data,
    )


def _created_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "appointment_id": APPOINTMENT_ID,
        "specialist_id": SPECIALIST_ID,
        "service_id": SERVICE_ID,
        "start_at": "2026-05-22T15:00:00+03:00",
        "end_at": "2026-05-22T16:00:00+03:00",
        "status": "confirmed",
        "price_total": "1800.00",
        "source": "mobile_app",
    }
    data.update(overrides)
    return data


def _chat_origin_marker(tenant: Tenant, bot_user: BotUser) -> BookingRequest:
    """The billing row ``execute_confirm`` writes for a dialog booking —
    its comment marker is the durable chat-origin proof."""
    return BookingRequest.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        service_name="Массаж",
        master_name="Ольга",
        client_name="Anna",
        client_phone="79991234567",
        comment=f"Bot booking | yclients_record_id={APPOINTMENT_ID}",
        source="bot",
        status=BookingRequest.Status.CONFIRMED,
    )


def _dialog_written_proxy(tenant: Tenant, bot_user: BotUser) -> RemoteBookingProxy:
    """The mirror row ``execute_confirm`` writes before Ayla's event
    arrives — CONFIRMED from the first instant, so the round-trip lands
    on the advanced-state no-op branch."""
    return RemoteBookingProxy.all_tenants.create(
        appointment_id=UUID(APPOINTMENT_ID),
        tenant=tenant,
        bot_user=bot_user,
        start_at=dt.datetime(2026, 5, 22, 12, 0, tzinfo=dt.timezone.utc),
        end_at=dt.datetime(2026, 5, 22, 13, 0, tzinfo=dt.timezone.utc),
        status=RemoteBookingProxy.Status.CONFIRMED,
        source=RemoteBookingProxy.Source.AUTOMATION,
        service_id=UUID(SERVICE_ID),
        specialist_id=UUID(SPECIALIST_ID),
    )


class TestConsumerEmitsInternalPair:
    def test_booking_created_and_attribution_assigned_reach_the_outbox(
        self, tenant: Tenant, bot_user_linked: BotUser
    ) -> None:
        handle_booking_created(_envelope(data=_created_data()))

        created = DomainEvent.objects.get(event_name=V.BOOKING_CREATED)
        attribution = DomainEvent.objects.get(event_name=V.BOOKING_ATTRIBUTION_ASSIGNED)
        assert created.data["booking_id"] == APPOINTMENT_ID
        assert attribution.data["booking_id"] == APPOINTMENT_ID
        assert str(created.tenant_id) == TENANT_ID
        assert str(attribution.tenant_id) == TENANT_ID

    def test_created_payload_carries_canonical_ids(
        self, tenant: Tenant, bot_user_linked: BotUser
    ) -> None:
        handle_booking_created(_envelope(data=_created_data()))

        created = DomainEvent.objects.get(event_name=V.BOOKING_CREATED)
        assert created.data["booking_id"] == APPOINTMENT_ID
        assert created.data["customer_id"] == AYLA_USER_ID
        assert created.data["service_id"] == SERVICE_ID
        assert created.data["master_id"] == SPECIALIST_ID
        assert created.data["slot_start"] == "2026-05-22T15:00:00+03:00"
        assert created.data["slot_end"] == "2026-05-22T16:00:00+03:00"

    def test_pair_shares_correlation_and_names_the_ingest_event(
        self, tenant: Tenant, bot_user_linked: BotUser
    ) -> None:
        env = _envelope(data=_created_data())
        handle_booking_created(env)

        created = DomainEvent.objects.get(event_name=V.BOOKING_CREATED)
        attribution = DomainEvent.objects.get(event_name=V.BOOKING_ATTRIBUTION_ASSIGNED)
        assert created.correlation_id
        assert created.correlation_id == attribution.correlation_id
        assert created.metadata["ingest_event_id"] == env.event_id
        assert attribution.metadata["ingest_event_id"] == env.event_id

    def test_mobile_app_source_is_ai_direct_and_billable(
        self, tenant: Tenant, bot_user_linked: BotUser
    ) -> None:
        """Mini App parity: on the flag-OFF path the same surface writes
        ``booking_source='ai_direct'`` (``create_customer_booking``)."""
        handle_booking_created(_envelope(data=_created_data(source="mobile_app")))

        attribution = DomainEvent.objects.get(event_name=V.BOOKING_ATTRIBUTION_ASSIGNED)
        assert attribution.data["booking_source"] == "ai_direct"
        assert attribution.data["billable"] is True
        assert attribution.data["ai_assist_score"] == pytest.approx(1.0)

    def test_automation_source_is_ai_direct(self, tenant: Tenant, bot_user_linked: BotUser) -> None:
        """Pilot fact (DRF-1110 sweep, 22.08): dialog bookings round-trip
        with ``source='automation'`` — the bot IS the automation."""
        handle_booking_created(_envelope(data=_created_data(source="automation")))

        attribution = DomainEvent.objects.get(event_name=V.BOOKING_ATTRIBUTION_ASSIGNED)
        assert attribution.data["booking_source"] == "ai_direct"
        assert attribution.data["billable"] is True

    def test_admin_console_source_is_human_direct_not_billable(
        self, tenant: Tenant, bot_user_linked: BotUser
    ) -> None:
        handle_booking_created(_envelope(data=_created_data(source="admin_console")))

        attribution = DomainEvent.objects.get(event_name=V.BOOKING_ATTRIBUTION_ASSIGNED)
        assert attribution.data["booking_source"] == "human_direct"
        assert attribution.data["billable"] is False

    def test_unknown_source_defaults_to_external_not_billable(
        self, tenant: Tenant, bot_user_linked: BotUser
    ) -> None:
        """Ambiguity resolves towards NOT billable — undercharging is the
        safe direction for a finance-facing field."""
        handle_booking_created(_envelope(data=_created_data(source="some_future_source")))

        attribution = DomainEvent.objects.get(event_name=V.BOOKING_ATTRIBUTION_ASSIGNED)
        assert attribution.data["booking_source"] == "external"
        assert attribution.data["billable"] is False

    def test_pending_payment_is_not_billable(
        self, tenant: Tenant, bot_user_linked: BotUser
    ) -> None:
        handle_booking_created(_envelope(data=_created_data(status="pending_payment")))

        attribution = DomainEvent.objects.get(event_name=V.BOOKING_ATTRIBUTION_ASSIGNED)
        assert attribution.data["billable"] is False


class TestExactlyOncePerAppointment:
    def test_redelivery_with_fresh_event_id_does_not_double_emit(
        self, tenant: Tenant, bot_user_linked: BotUser
    ) -> None:
        """Ayla's redelivery carries a NEW event_id, so the ingest dedupe
        ledger does not catch it — the emit must dedupe on the canonical
        appointment id instead."""
        handle_booking_created(_envelope(data=_created_data()))
        handle_booking_created(
            _envelope(
                data=_created_data(),
                event_id="01J9HXKM8Z2T4V6R8Q1P3D5F9X",  # pragma: allowlist secret
            )
        )

        assert DomainEvent.objects.filter(event_name=V.BOOKING_CREATED).count() == 1
        assert DomainEvent.objects.filter(event_name=V.BOOKING_ATTRIBUTION_ASSIGNED).count() == 1

    def test_dialog_booking_emits_once_from_the_advanced_state_branch(
        self, tenant: Tenant, bot_user_linked: BotUser
    ) -> None:
        """The pilot sequence: ``execute_confirm`` writes BOTH the billing
        row (chat-origin marker) and the CONFIRMED mirror before Ayla's
        event arrives, so the round-trip exits through the advanced-state
        no-op. The internal pair must still be emitted — exactly once,
        and not again by the marker row's own post_save."""
        _chat_origin_marker(tenant, bot_user_linked)
        _dialog_written_proxy(tenant, bot_user_linked)

        handle_booking_created(_envelope(data=_created_data(source="automation")))

        created = DomainEvent.objects.filter(event_name=V.BOOKING_CREATED)
        attribution = DomainEvent.objects.filter(event_name=V.BOOKING_ATTRIBUTION_ASSIGNED)
        assert created.count() == 1
        assert attribution.count() == 1
        # Keyed by the canonical appointment id, not the billing row's PK.
        assert created.first().data["booking_id"] == APPOINTMENT_ID
        # Chat origin is the durable local proof of ai_direct.
        assert attribution.first().data["booking_source"] == "ai_direct"
        assert attribution.first().data["billable"] is True


class TestFlagOffUnchanged:
    @override_settings(BOOKING_VIA_AYLA_REST=False)
    def test_consumer_emits_nothing_when_flag_off(
        self, tenant: Tenant, bot_user_linked: BotUser
    ) -> None:
        """Flag OFF keeps byte-for-byte behaviour: the post_save receiver
        on BookingRequest owns the internal emit; the consumer must not
        double it."""
        handle_booking_created(_envelope(data=_created_data()))

        assert DomainEvent.objects.filter(event_name=V.BOOKING_CREATED).count() == 0
        assert DomainEvent.objects.filter(event_name=V.BOOKING_ATTRIBUTION_ASSIGNED).count() == 0


class TestSignalReceiverStaysSilentOnTheAylaPath:
    def test_booking_request_post_save_emits_nothing_when_flag_on(self, tenant: Tenant) -> None:
        """DRF-1140's other half: on the Ayla path the round-trip consumer
        is the single emitter. If the receiver kept firing, every dialog
        booking would be counted twice — once under the billing row's
        local id, once under the canonical appointment id."""
        with tenant_scope(tenant):
            BookingRequest.all_tenants.create(
                tenant=tenant,
                service_name="hair",
                client_name="Anonymous",
                client_phone="snapshot",
                source="bot",
            )

        assert DomainEvent.objects.filter(event_name=V.BOOKING_CREATED).count() == 0
        assert DomainEvent.objects.filter(event_name=V.BOOKING_ATTRIBUTION_ASSIGNED).count() == 0
