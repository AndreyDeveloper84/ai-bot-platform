"""Booking confirmation to the client in chat — tests (DRF-1066).

On 14.08 a customer completed the Mini App booking funnel, the success
screen never rendered, and — with nothing anywhere telling them the
booking had happened — they tapped again and ended up with two
confirmed appointments with two different masters. These tests pin the
replacement guarantee: the bot says «вы записаны» in the chat, once,
whatever surface the booking came from.

Covered:

* the message reaches the client's own chat, and carries service,
  master, date and time in the tenant's timezone;
* nobody else's data is in it;
* **no duplicate for a booking made in the dialog** — where
  ``execute_confirm`` already replied — via both guards independently:
  the consumer's ``created`` gate and the chat-origin marker;
* a client with no bot chat is skipped quietly, not warned about;
* wiring: sent after commit for a Mini App booking, nothing on
  rollback, nothing on event re-delivery;
* the prepayment flow — silence on ``awaiting_payment`` creation, one
  message on the transition through ``booking.confirmed``, and no
  second message when the booking was already announced at creation;
* best-effort containment: a dead messenger never escapes into ingest.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from django.db import transaction
from django.utils import timezone

from apps.booking.client_notify import (
    build_booking_confirmation,
    notify_client_booking_confirmed,
    was_confirmed_in_chat,
)
from apps.booking.models import BookingRequest, RemoteBookingProxy
from apps.catalog.models import CatalogMaster, CatalogService
from apps.channels.max.outbound import MaxAPIError
from apps.eventbus.consumers.booking import (
    handle_booking_confirmed,
    handle_booking_created,
)
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

# The confirmation reuses the DRF-1029 fan-out primitive, so the send is
# patched where that primitive imports it — same seam as DRF-1030.
NOTIFY_SEND = "apps.handoff.notify.send_message"

TENANT_ID = "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c"
AYLA_USER_ID = "f1a2b3c4-d5e6-4789-9abc-def012345678"
APPOINTMENT_ID = "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8"
SPECIALIST_ID = "7c2d8e1f-0a5c-4c3a-9e1b-4d52f8eb3a17"
SERVICE_ID = "3d5f7e1c-8a2d-4e6f-b9c0-1d2e3f4a5b6c"

START_AT = "2026-05-22T15:00:00+03:00"
END_AT = "2026-05-22T16:00:00+03:00"

CLIENT_CHAT_ID = "client-chat-1"


class SendRecorder:
    """Stand-in for ``channels.max.outbound.send_message``."""

    def __init__(self, side_effects: Any = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.side_effects = side_effects

    def __call__(
        self,
        *,
        chat_id: str,
        text: str,
        attachments: Any = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        self.calls.append({"chat_id": chat_id, "text": text, "timeout": timeout})
        effects = self.side_effects
        if isinstance(effects, Exception):
            raise effects
        return {}


# ─── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(
        id=TENANT_ID,
        slug="notify-salon",
        name="Формула тела",
        timezone="Europe/Moscow",
    )


@pytest.fixture
def send(monkeypatch: pytest.MonkeyPatch) -> SendRecorder:
    recorder = SendRecorder()
    monkeypatch.setattr(NOTIFY_SEND, recorder)
    return recorder


@pytest.fixture(autouse=True)
def _no_salon_fallback(settings: Any) -> None:
    """Keep the salon notification (DRF-1030) off the wire.

    Both messages ride the same ``send_message`` seam. With the fallback
    chat configured, every assertion below would have to filter the
    salon's copy out of the recorder — so the fallback rung is left
    unconfigured and the salon message only appears when a test wires a
    ``manager_chat_id`` on purpose.
    """

    settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = []


@pytest.fixture
def client_bot_user(tenant: Tenant) -> BotUser:
    """The customer, reachable in the bot — the DRF-1066 happy path."""

    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="client-1",
        chat_id=CLIENT_CHAT_ID,
        display_name="Иван Клиентов",
        client_name="Иван Клиентов",
        phone="+79991234567",
        ayla_user_id=AYLA_USER_ID,
    )


def _make_master(tenant: Tenant, *, name: str = "Тихонова Ольга") -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=1,
        external_updated_at=timezone.now(),
        name=name,
        ayla_user_id=SPECIALIST_ID,
    )


def _make_service(tenant: Tenant, *, name: str = "УЗ-кавитация — 1 зона") -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=1,
        external_updated_at=timezone.now(),
        slug="uz-cavitation",
        name=name,
        duration_min=30,
        is_active=True,
        ayla_service_id=SERVICE_ID,
    )


def _make_chat_booking_marker(
    tenant: Tenant,
    bot_user: BotUser,
    *,
    appointment_id: str = APPOINTMENT_ID,
) -> BookingRequest:
    """Reproduce the row ``execute_confirm`` writes for a dialog booking.

    Field-for-field the shape of the real write (``comment`` prefix,
    ``source="bot"``, CONFIRMED) so the guard is tested against the
    actual marker, not a convenient stand-in.
    """

    return BookingRequest.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        category_name="",
        service_name="УЗ-кавитация — 1 зона",
        master_name="Тихонова Ольга",
        client_name="Иван Клиентов",
        client_phone="+79991234567",
        comment=f"Bot booking | yclients_record_id={appointment_id}",
        source="bot",
        is_processed=False,
        status=BookingRequest.Status.CONFIRMED,
        visit_at=dt.datetime.fromisoformat(START_AT),
    )


def _notify(tenant: Tenant, bot_user: BotUser | None) -> None:
    notify_client_booking_confirmed(
        tenant=tenant,
        bot_user=bot_user,
        appointment_id=uuid.UUID(APPOINTMENT_ID),
        start_at=dt.datetime.fromisoformat(START_AT),
        specialist_id=uuid.UUID(SPECIALIST_ID),
        service_id=uuid.UUID(SERVICE_ID),
    )


# ─── addressing ────────────────────────────────────────────────────────────


class TestAddressing:
    def test_goes_to_the_clients_own_chat(
        self, tenant: Tenant, client_bot_user: BotUser, send: SendRecorder
    ) -> None:
        _notify(tenant, client_bot_user)
        assert [c["chat_id"] for c in send.calls] == [CLIENT_CHAT_ID]

    def test_no_bot_user_is_a_quiet_skip(self, tenant: Tenant, send: SendRecorder) -> None:
        """Booking in the Ayla app without ever opening the bot is normal.

        No message is possible and none is warned about — a WARNING on
        ordinary traffic would bury the salon-side ``no_recipients``
        warning, which does mean something is misconfigured.
        """

        _notify(tenant, None)
        assert send.calls == []

    def test_blank_chat_id_is_treated_as_absent(self, tenant: Tenant, send: SendRecorder) -> None:
        bot_user = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="client-2",
            chat_id="   ",
            ayla_user_id=AYLA_USER_ID,
        )
        _notify(tenant, bot_user)
        assert send.calls == []

    def test_skip_is_logged_at_info_not_warning(
        self, tenant: Tenant, send: SendRecorder, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("DEBUG", logger="apps.booking.client_notify"):
            _notify(tenant, None)
        records = [r for r in caplog.records if r.name == "apps.booking.client_notify"]
        assert [r.levelname for r in records] == ["INFO"]
        assert "booking.client_notify.no_chat" in records[0].getMessage()


# ─── message body ──────────────────────────────────────────────────────────


class TestConfirmationText:
    def test_carries_service_master_and_time(
        self, tenant: Tenant, client_bot_user: BotUser, send: SendRecorder
    ) -> None:
        _make_service(tenant)
        _make_master(tenant)
        _notify(tenant, client_bot_user)
        text = send.calls[0]["text"]
        assert "Вы записаны" in text
        assert "УЗ-кавитация — 1 зона" in text
        assert "Тихонова Ольга" in text
        assert "22.05.2026 в 15:00" in text

    def test_time_is_rendered_in_tenant_timezone(
        self, tenant: Tenant, client_bot_user: BotUser, send: SendRecorder
    ) -> None:
        """The whole point is a customer who knows when to turn up."""

        tenant.timezone = "Asia/Yekaterinburg"  # UTC+5
        tenant.save(update_fields=["timezone"])
        _notify(tenant, client_bot_user)
        assert "22.05.2026 в 17:00" in send.calls[0]["text"]

    def test_invalid_tenant_timezone_degrades_to_msk(
        self, tenant: Tenant, client_bot_user: BotUser, send: SendRecorder
    ) -> None:
        tenant.timezone = "Not/AZone"
        tenant.save(update_fields=["timezone"])
        _notify(tenant, client_bot_user)
        assert "22.05.2026 в 15:00" in send.calls[0]["text"]

    def test_unmirrored_catalog_still_confirms(
        self, tenant: Tenant, client_bot_user: BotUser, send: SendRecorder
    ) -> None:
        """A missing catalog row must not cost the customer the answer."""

        _notify(tenant, client_bot_user)
        text = send.calls[0]["text"]
        assert "Услуга: —" in text
        assert "Мастер: —" in text
        assert "22.05.2026 в 15:00" in text

    def test_no_other_persons_data(
        self, tenant: Tenant, client_bot_user: BotUser, send: SendRecorder
    ) -> None:
        """Only this booking, to the person who made it.

        Another customer's booking with the same master at another hour
        exists in the same tenant; none of it may appear.
        """

        other = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="client-other",
            chat_id="other-chat",
            client_name="Мария Другая",
            phone="+79997654321",
            ayla_user_id="c1d2e3f4-a5b6-4789-9abc-def012345679",
        )
        _make_chat_booking_marker(
            tenant,
            other,
            appointment_id="11111111-2222-4333-8444-555555555555",
        )
        _make_master(tenant)
        _notify(tenant, client_bot_user)
        text = send.calls[0]["text"]
        assert "Мария Другая" not in text
        assert "+79997654321" not in text
        # Not even the customer's own phone or the internal id.
        assert "+79991234567" not in text
        assert APPOINTMENT_ID not in text

    def test_build_is_pure(self, tenant: Tenant, django_assert_num_queries: Any) -> None:
        """Formatting touches the DB zero times.

        The callback runs outside ``tenant_scope``; under the pilot's
        audit-mode scoping a stray query there returns emptiness rather
        than raising, silently rendering wrong data.
        """

        with django_assert_num_queries(0):
            text = build_booking_confirmation(
                tenant=tenant,
                start_at=dt.datetime.fromisoformat(START_AT),
                service_name="Массаж",
                master_name="Ольга",
            )
        assert "Массаж" in text


# ─── no duplicate for a booking made in the dialog ─────────────────────────


class TestChatOriginGuard:
    def test_marker_detected(self, tenant: Tenant, client_bot_user: BotUser) -> None:
        _make_chat_booking_marker(tenant, client_bot_user)
        assert was_confirmed_in_chat(tenant=tenant, appointment_id=uuid.UUID(APPOINTMENT_ID))

    def test_no_marker_no_suppression(self, tenant: Tenant) -> None:
        assert not was_confirmed_in_chat(tenant=tenant, appointment_id=uuid.UUID(APPOINTMENT_ID))

    def test_another_appointments_marker_does_not_suppress(
        self, tenant: Tenant, client_bot_user: BotUser
    ) -> None:
        _make_chat_booking_marker(
            tenant,
            client_bot_user,
            appointment_id="11111111-2222-4333-8444-555555555555",
        )
        assert not was_confirmed_in_chat(tenant=tenant, appointment_id=uuid.UUID(APPOINTMENT_ID))

    def test_marker_matched_case_insensitively(
        self, tenant: Tenant, client_bot_user: BotUser
    ) -> None:
        """UUID hex casing is the producer's choice on both sides."""

        _make_chat_booking_marker(tenant, client_bot_user, appointment_id=APPOINTMENT_ID.upper())
        assert was_confirmed_in_chat(tenant=tenant, appointment_id=uuid.UUID(APPOINTMENT_ID))

    def test_marker_on_another_bot_user_row_still_suppresses(
        self, tenant: Tenant, client_bot_user: BotUser, send: SendRecorder
    ) -> None:
        """``(tenant, ayla_user_id)`` is not unique — one row per channel.

        The consumer resolves the most recently active ``BotUser``,
        which need not be the row that made the dialog booking. The
        guard is keyed on the appointment alone precisely so that
        mismatch cannot leak a duplicate.
        """

        other_channel_row = BotUser.all_tenants.create(
            tenant=tenant,
            channel="telegram",
            channel_user_id="client-1-tg",
            chat_id="tg-chat",
            ayla_user_id=AYLA_USER_ID,
        )
        _make_chat_booking_marker(tenant, other_channel_row)
        _notify(tenant, client_bot_user)
        assert send.calls == []

    def test_suppressed_send_is_logged(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        _make_chat_booking_marker(tenant, client_bot_user)
        with caplog.at_level("DEBUG", logger="apps.booking.client_notify"):
            _notify(tenant, client_bot_user)
        assert send.calls == []
        messages = [
            r.getMessage() for r in caplog.records if r.name == "apps.booking.client_notify"
        ]
        assert any("skipped_chat_origin" in m for m in messages)

    def test_lookup_failure_fails_towards_silence(
        self, tenant: Tenant, client_bot_user: BotUser, send: SendRecorder, monkeypatch: Any
    ) -> None:
        """With the DB refusing to confirm silence, stay silent.

        Idempotency beats completeness: a missed confirmation is a
        smaller harm than the duplicate that opened this ticket.
        """

        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("db down")

        monkeypatch.setattr(
            "apps.booking.models.BookingRequest.all_tenants.filter",
            _boom,
        )
        _notify(tenant, client_bot_user)
        assert send.calls == []


# ─── containment ───────────────────────────────────────────────────────────


class TestBestEffort:
    def test_max_failure_never_escapes(
        self, tenant: Tenant, client_bot_user: BotUser, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A dead MAX must not dead-letter the booking event."""

        monkeypatch.setattr(NOTIFY_SEND, SendRecorder(side_effects=MaxAPIError(500, "down")))
        _notify(tenant, client_bot_user)  # must not raise

    def test_unexpected_exception_never_escapes(
        self, tenant: Tenant, client_bot_user: BotUser, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(NOTIFY_SEND, SendRecorder(side_effects=RuntimeError("boom")))
        _notify(tenant, client_bot_user)  # must not raise

    def test_send_uses_the_short_timeout(
        self, tenant: Tenant, client_bot_user: BotUser, send: SendRecorder
    ) -> None:
        """The ingest consumer is single-threaded — never block it."""

        _notify(tenant, client_bot_user)
        assert send.calls[0]["timeout"] <= 5.0


# ─── wiring through the consumer ───────────────────────────────────────────


def _created_envelope(
    *,
    event_id: str = "01J9HXKM8Z2T4V6R8Q1P3D5F7E",
    status: str = "confirmed",
    source: str = "miniapp",
) -> IngestEnvelope:
    return IngestEnvelope(
        event_id=event_id,  # pragma: allowlist secret
        event_name="booking.created",
        event_version=1,
        occurred_at=dt.datetime(2026, 5, 21, 14, 32, 11, tzinfo=dt.timezone.utc),
        tenant_id=TENANT_ID,
        user_id=AYLA_USER_ID,
        actor="user",
        correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        causation_id=None,
        data={
            "appointment_id": APPOINTMENT_ID,
            "specialist_id": SPECIALIST_ID,
            "service_id": SERVICE_ID,
            "start_at": START_AT,
            "end_at": END_AT,
            "status": status,
            "source": source,
        },
    )


def _confirmed_envelope(*, event_id: str = "01J9HXKM8Z2T4V6R8Q1P3D5F7G") -> IngestEnvelope:
    return IngestEnvelope(
        event_id=event_id,  # pragma: allowlist secret
        event_name="booking.confirmed",
        event_version=1,
        occurred_at=dt.datetime(2026, 5, 21, 14, 40, 0, tzinfo=dt.timezone.utc),
        tenant_id=TENANT_ID,
        user_id=AYLA_USER_ID,
        actor="user",
        correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        causation_id=None,
        data={"appointment_id": APPOINTMENT_ID},
    )


@pytest.fixture
def _ingest_allowlist(settings: Any) -> None:
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = False
    settings.EVENT_INGEST_ALLOWED_TENANTS = frozenset({TENANT_ID})
    settings.EVENT_INGEST_ALLOWED_EVENTS = frozenset({"booking.created", "booking.confirmed"})


@pytest.mark.usefixtures("_ingest_allowlist")
class TestConsumerWiring:
    def test_miniapp_booking_is_confirmed_in_chat_after_commit(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        """The incident's own path: booked in the Mini App, told in chat."""

        _make_service(tenant)
        _make_master(tenant)
        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope())
        assert [c["chat_id"] for c in send.calls] == [CLIENT_CHAT_ID]
        text = send.calls[0]["text"]
        assert "Вы записаны" in text
        assert "УЗ-кавитация — 1 зона" in text
        assert "Тихонова Ольга" in text
        assert "22.05.2026 в 15:00" in text

    def test_nothing_sent_before_commit(
        self, tenant: Tenant, client_bot_user: BotUser, send: SendRecorder
    ) -> None:
        """The send is queued, never executed inside the handler."""

        handle_booking_created(_created_envelope())
        assert send.calls == []
        assert RemoteBookingProxy.all_tenants.filter(
            appointment_id=uuid.UUID(APPOINTMENT_ID)
        ).exists()

    def test_chat_booking_sends_nothing(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        """Booked in the dialog → ``execute_confirm`` already replied.

        Both of that path's footprints are reproduced: the mirror row it
        upserts (which turns the consumer's insert into an update) and
        the ``BookingRequest`` marker. Neither may produce a second
        «вы записаны».
        """

        RemoteBookingProxy.all_tenants.create(
            appointment_id=uuid.UUID(APPOINTMENT_ID),
            tenant=tenant,
            bot_user=client_bot_user,
            start_at=dt.datetime.fromisoformat(START_AT),
            end_at=dt.datetime.fromisoformat(END_AT),
            status=RemoteBookingProxy.Status.CONFIRMED,
            source=RemoteBookingProxy.Source.AUTOMATION,
            service_id=uuid.UUID(SERVICE_ID),
            specialist_id=uuid.UUID(SPECIALIST_ID),
        )
        _make_chat_booking_marker(tenant, client_bot_user)
        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope(source="ayla_bot"))
        assert send.calls == []

    def test_chat_booking_without_its_mirror_row_still_sends_nothing(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        """The second guard, on its own.

        ``_upsert_remote_booking_proxy`` is best-effort and swallows its
        exceptions, and it races the inbound event. When it loses or
        fails, the consumer's ``created`` gate is open — the marker is
        what keeps the customer from being told twice.
        """

        _make_chat_booking_marker(tenant, client_bot_user)
        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope(source="ayla_bot"))
        assert send.calls == []
        # The proxy really was inserted by this handler — i.e. guard 1
        # was genuinely open and guard 2 is what did the work.
        assert RemoteBookingProxy.all_tenants.filter(
            appointment_id=uuid.UUID(APPOINTMENT_ID)
        ).exists()

    def test_redelivery_with_new_event_id_does_not_re_confirm(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        """One booking, one «вы записаны»."""

        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope())
        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope(event_id="01J9HXKM8Z2T4V6R8Q1P3D5F7F"))
        assert len(send.calls) == 1

    def test_exact_replay_does_not_re_confirm(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        for _ in range(3):
            with django_capture_on_commit_callbacks(execute=True):
                handle_booking_created(_created_envelope())
        assert len(send.calls) == 1

    def test_client_without_bot_account_breaks_nothing(
        self,
        tenant: Tenant,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        """Orphan proxy — no ``BotUser`` at all. Ingest proceeds."""

        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope())
        assert send.calls == []
        assert RemoteBookingProxy.all_tenants.filter(
            appointment_id=uuid.UUID(APPOINTMENT_ID)
        ).exists()

    def test_send_failure_does_not_break_the_handler(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        monkeypatch: pytest.MonkeyPatch,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        """A dead messenger must not turn into a dead-lettered event."""

        monkeypatch.setattr(NOTIFY_SEND, SendRecorder(side_effects=MaxAPIError(503, "down")))
        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope())  # must not raise
        assert RemoteBookingProxy.all_tenants.filter(
            appointment_id=uuid.UUID(APPOINTMENT_ID)
        ).exists()


@pytest.mark.usefixtures("_ingest_allowlist")
class TestPrepaymentFlow:
    """Created ``awaiting_payment`` → nothing; confirmed → exactly one."""

    def test_awaiting_payment_creation_says_nothing(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        """«Вы записаны» before payment would be untrue."""

        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope(status="awaiting_payment"))
        assert send.calls == []

    def test_confirmation_transition_announces_once(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        _make_service(tenant)
        _make_master(tenant)
        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope(status="awaiting_payment"))
        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_confirmed(_confirmed_envelope())
        assert [c["chat_id"] for c in send.calls] == [CLIENT_CHAT_ID]
        text = send.calls[0]["text"]
        assert "УЗ-кавитация — 1 зона" in text
        assert "Тихонова Ольга" in text
        assert "22.05.2026 в 15:00" in text

    def test_already_confirmed_booking_is_not_announced_twice(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        """Born confirmed → announced at creation, silent on confirm.

        The two call sites are mutually exclusive by construction: this
        pins that a ``booking.confirmed`` following a confirmed
        ``booking.created`` adds nothing.
        """

        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope())
        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_confirmed(_confirmed_envelope())
        assert len(send.calls) == 1

    def test_confirmed_replay_does_not_re_announce(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope(status="awaiting_payment"))
        for event_id in (
            "01J9HXKM8Z2T4V6R8Q1P3D5F7G",  # pragma: allowlist secret
            "01J9HXKM8Z2T4V6R8Q1P3D5F7H",  # pragma: allowlist secret
        ):
            with django_capture_on_commit_callbacks(execute=True):
                handle_booking_confirmed(_confirmed_envelope(event_id=event_id))
        assert len(send.calls) == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("_ingest_allowlist")
class TestRollback:
    def test_rolled_back_ingest_confirms_nothing(
        self, tenant: Tenant, client_bot_user: BotUser, send: SendRecorder
    ) -> None:
        """A booking that was never persisted must never be confirmed."""

        with pytest.raises(RuntimeError, match="force rollback"):
            with transaction.atomic():
                handle_booking_created(_created_envelope())
                raise RuntimeError("force rollback")
        assert send.calls == []
        assert not RemoteBookingProxy.all_tenants.filter(
            appointment_id=uuid.UUID(APPOINTMENT_ID)
        ).exists()
