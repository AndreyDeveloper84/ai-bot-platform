"""Per-appointment announcement claims — tests (DRF-1069).

### The defect these pin

``handle_booking_created`` announced a new booking to the salon under
``if created:`` — the flag ``get_or_create`` returns when it *inserted*
the :class:`~apps.booking.models.RemoteBookingProxy` mirror row. The
comment above it claimed this handler was the only writer of that
mirror. It is not: the conversational path writes the row itself
(``apps.skills.booking.tools._upsert_remote_booking_proxy``, inside
``execute_confirm``) *before* Ayla's event arrives. So for every booking
made in the bot's own dialog the flag was ``False``, and **the salon was
never told** — verified on the pilot 14.08, when the owner booked
through the chat: two creation events, zero salon notifications.

The dialog is the product's main booking path, so this was most of the
traffic.

### What replaced it

``RemoteBookingProxy.salon_notified_at`` / ``client_notified_at`` —
per-appointment, per-side claims taken by
``apps.eventbus.consumers.booking._claim_announcement`` as a single
``UPDATE … WHERE <slot> IS NULL``. The question changed from «did *we*
insert this row» to «has *anyone* announced this appointment yet», which
is the question that was always being asked. The new answer is durable
(survives re-delivery under a fresh ``event_id``), writer-agnostic
(survives someone else writing the mirror), and transactional (a
rolled-back ingest releases it).

### Covered here

* the fix — a chat booking reaches the salon, exactly once, labelled as
  the bot's;
* the regression it must not cause — a Mini App booking still reaches
  the salon, and still confirms to the client;
* no second «вы записаны» where ``execute_confirm`` already answered in
  the dialog, including the prepayment variant where the client claim
  *is* taken but the send still self-suppresses;
* one announcement per appointment under re-delivery — same
  ``event_id`` and, the case the old gate got wrong, a **fresh** one;
* rollback announces nothing *and leaves the claim free*, so the retry
  that follows still announces;
* rows written before migration ``0018`` are claimed by it and never
  announced — the pilot has live mirror rows and the deploy must not
  page the salon about history;
* a stale ``booking.created`` for an appointment already cancelled /
  completed / no-showed announces nothing and takes no claim.

The salon and the client message ride the same ``send_message`` seam, so
a single recorder sees both and the tests tell them apart by chat id —
which also means every test here implicitly asserts the *other* side
stayed quiet.
"""

from __future__ import annotations

import datetime as dt
import uuid
from importlib import import_module
from typing import Any

import pytest
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from apps.booking.models import BookingRequest, RemoteBookingProxy
from apps.catalog.models import CatalogMaster, CatalogService
from apps.eventbus.consumers.booking import (
    _claim_announcement,
    handle_booking_cancelled,
    handle_booking_confirmed,
    handle_booking_created,
)
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

# Both notifications reuse the DRF-1029 fan-out primitive, so both are
# patched at the same seam — see the module docstring.
NOTIFY_SEND = "apps.handoff.notify.send_message"

TENANT_ID = "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c"
OTHER_TENANT_ID = "1e4b2c9a-7f36-4a58-8d21-6b9e0c4f7a3d"
AYLA_USER_ID = "f1a2b3c4-d5e6-4789-9abc-def012345678"
APPOINTMENT_ID = "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8"
SPECIALIST_ID = "7c2d8e1f-0a5c-4c3a-9e1b-4d52f8eb3a17"
SERVICE_ID = "3d5f7e1c-8a2d-4e6f-b9c0-1d2e3f4a5b6c"

START_AT = "2026-05-22T15:00:00+03:00"
END_AT = "2026-05-22T16:00:00+03:00"

# The salon's rung and the client's chat are distinct ids so one
# recorder can separate the two audiences.
MANAGER_CHAT_ID = "manager-chat-1"
CLIENT_CHAT_ID = "client-chat-1"

EVENT_ID = "01J9HXKM8Z2T4V6R8Q1P3D5F7E"  # pragma: allowlist secret
EVENT_ID_REDELIVERY = "01J9HXKM8Z2T4V6R8Q1P3D5F7F"  # pragma: allowlist secret
EVENT_ID_CONFIRMED = "01J9HXKM8Z2T4V6R8Q1P3D5F7G"  # pragma: allowlist secret
EVENT_ID_CANCELLED = "01J9HXKM8Z2T4V6R8Q1P3D5F7H"  # pragma: allowlist secret

MIGRATION_NAME = "0018_remotebookingproxy_announcement_claims"


class SendRecorder:
    """Stand-in for ``channels.max.outbound.send_message``.

    Same shape as the DRF-1029 / DRF-1030 recorders in the sibling
    suites.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        *,
        chat_id: str,
        text: str,
        attachments: Any = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        self.calls.append({"chat_id": chat_id, "text": text})
        return {}

    def to(self, chat_id: str) -> list[dict[str, Any]]:
        return [call for call in self.calls if call["chat_id"] == chat_id]

    @property
    def salon(self) -> list[dict[str, Any]]:
        return self.to(MANAGER_CHAT_ID)

    @property
    def client(self) -> list[dict[str, Any]]:
        return self.to(CLIENT_CHAT_ID)


# ─── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def tenant() -> Tenant:
    """The pilot salon, with the manager rung of the cascade wired.

    ``manager_chat_id`` rather than a linked master, so the salon
    message has a fixed, recognisable destination in every test here.
    """

    return Tenant.objects.create(
        id=TENANT_ID,
        slug="notify-claims",
        name="Формула тела",
        timezone="Europe/Moscow",
        manager_chat_id=MANAGER_CHAT_ID,
    )


@pytest.fixture
def client_bot_user(tenant: Tenant) -> BotUser:
    """The customer, reachable in the bot."""

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


@pytest.fixture
def send(monkeypatch: pytest.MonkeyPatch) -> SendRecorder:
    recorder = SendRecorder()
    monkeypatch.setattr(NOTIFY_SEND, recorder)
    return recorder


@pytest.fixture(autouse=True)
def _no_fallback_channel(settings: Any) -> None:
    """No configured fallback rung — the manager rung answers.

    Leaves the salon message with exactly one destination, so counting
    sends to :data:`MANAGER_CHAT_ID` counts announcements.
    """

    settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = []


@pytest.fixture(autouse=True)
def _ingest_allowlist(settings: Any) -> None:
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = False
    settings.EVENT_INGEST_ALLOWED_TENANTS = frozenset({TENANT_ID})
    settings.EVENT_INGEST_ALLOWED_EVENTS = frozenset(
        {"booking.created", "booking.confirmed", "booking.cancelled"}
    )


# ─── helpers ───────────────────────────────────────────────────────────────


def _created_envelope(
    *,
    event_id: str = EVENT_ID,
    status: str = "confirmed",
    source: str = "miniapp",
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


def _lifecycle_envelope(*, event_name: str, event_id: str) -> IngestEnvelope:
    return IngestEnvelope(
        event_id=event_id,
        event_name=event_name,
        event_version=1,
        occurred_at=dt.datetime(2026, 5, 21, 14, 40, 0, tzinfo=dt.timezone.utc),
        tenant_id=TENANT_ID,
        user_id=AYLA_USER_ID,
        actor="user",
        correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        causation_id=None,
        data={"appointment_id": APPOINTMENT_ID},
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


def _make_chat_mirror_row(
    tenant: Tenant,
    bot_user: BotUser | None = None,
    *,
    status: str = RemoteBookingProxy.Status.CONFIRMED,
) -> RemoteBookingProxy:
    """Reproduce the mirror row the dialog path writes for itself.

    ``apps.skills.booking.tools._upsert_remote_booking_proxy`` runs
    inside ``execute_confirm``, i.e. **before** Ayla's ``booking.created``
    reaches the consumer, and labels the row ``automation`` (there is no
    bot value in ``RemoteBookingProxy.Source``). Its existence is what
    made ``get_or_create`` report ``created=False`` and the old gate stay
    silent.
    """

    return RemoteBookingProxy.all_tenants.create(
        appointment_id=uuid.UUID(APPOINTMENT_ID),
        tenant=tenant,
        bot_user=bot_user,
        start_at=dt.datetime.fromisoformat(START_AT),
        end_at=dt.datetime.fromisoformat(END_AT),
        status=status,
        source=RemoteBookingProxy.Source.AUTOMATION,
        service_id=uuid.UUID(SERVICE_ID),
        specialist_id=uuid.UUID(SPECIALIST_ID),
    )


def _make_chat_booking_marker(
    tenant: Tenant,
    bot_user: BotUser,
    *,
    appointment_id: str = APPOINTMENT_ID,
) -> BookingRequest:
    """Reproduce the ``BookingRequest`` marker ``execute_confirm`` writes.

    Field-for-field the shape of the real write, so the origin guard is
    tested against the actual marker rather than a convenient stand-in.
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


def _proxy() -> RemoteBookingProxy:
    return RemoteBookingProxy.all_tenants.get(appointment_id=uuid.UUID(APPOINTMENT_ID))


# ─── the fix ───────────────────────────────────────────────────────────────


class TestSalonHearsAboutChatBookings:
    """DRF-1069's whole point: the dialog booking reaches the salon."""

    def test_chat_booking_notifies_the_salon(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        """The pilot's 14.08 scenario, end to end.

        Both footprints of the dialog path are present — the mirror row
        it wrote (already ``CONFIRMED``, so the consumer takes its
        advanced-state no-op branch) and the ``BookingRequest`` marker.
        Under the old ``created`` gate this produced nothing at all.
        """

        _make_service(tenant)
        _make_master(tenant)
        _make_chat_mirror_row(tenant, client_bot_user)
        _make_chat_booking_marker(tenant, client_bot_user)

        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope(source=""))

        assert len(send.salon) == 1
        text = send.salon[0]["text"]
        assert "🆕 Новая запись" in text
        assert "УЗ-кавитация — 1 зона" in text
        assert "Тихонова Ольга" in text
        assert "22.05.2026 в 15:00" in text

    def test_the_salon_message_names_the_bot_as_the_source(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        """«Источник: бот Ayla», not the mirror row's «автоматизация».

        The event carries no usable source for a dialog booking (the bot
        does not pass one through ``provider.create_record``), and this
        message is the salon's first ever sight of these bookings — so
        the consumer substitutes what local state proves.
        """

        _make_chat_mirror_row(tenant, client_bot_user)
        _make_chat_booking_marker(tenant, client_bot_user)

        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope(source="automation"))

        assert "Источник: бот Ayla" in send.salon[0]["text"]

    def test_the_claim_is_stamped_on_the_row(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        """The durable half — the fact outlives the process that sent."""

        _make_chat_mirror_row(tenant, client_bot_user)
        _make_chat_booking_marker(tenant, client_bot_user)

        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope())

        proxy = _proxy()
        assert proxy.salon_notified_at is not None
        # The client side deliberately stays free: this channel sent the
        # customer nothing, so it records nothing. The dialog reply is
        # not this channel's to claim.
        assert proxy.client_notified_at is None

    def test_a_mirror_row_written_by_anyone_else_is_still_announced(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        """Insert-agnostic, not merely chat-aware.

        A mirror row with no chat marker behind it — a backfill, a
        reconciliation job, any future writer — must not silence the
        salon either. The claim asks «has anyone announced this», and
        nobody has.
        """

        _make_chat_mirror_row(tenant, client_bot_user)

        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope())

        assert len(send.salon) == 1

    def test_chat_booking_does_not_confirm_the_client_again(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        """``execute_confirm`` already replied «Готово! Записала.».

        The salon gains a message and the client gains nothing — the two
        sides are now decided separately, which is the whole reason the
        conflated ``created`` flag had to go.
        """

        _make_chat_mirror_row(tenant, client_bot_user)
        _make_chat_booking_marker(tenant, client_bot_user)

        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope())

        assert len(send.salon) == 1
        assert send.client == []

    def test_chat_booking_without_its_mirror_row_still_says_nothing_to_the_client(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        """The origin marker on its own, with the insert gate wide open.

        ``_upsert_remote_booking_proxy`` is best-effort and races the
        inbound event; when it loses or fails, this handler really does
        insert the row. The salon must still be told, the client must
        still not be.
        """

        _make_chat_booking_marker(tenant, client_bot_user)

        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope())

        assert len(send.salon) == 1
        assert send.client == []

    def test_prepaid_chat_booking_is_not_confirmed_on_the_transition_either(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        """The one path where the client claim *is* taken for a chat booking.

        With prepayment the dialog booking is born ``awaiting_payment``,
        so ``booking.confirmed`` reaches a proxy that was not yet
        confirmed and the claim is available. It is taken — and the send
        still produces nothing, because ``client_notify`` re-reads the
        origin marker inside the callback. Claim and delivery are
        different facts on purpose; this pins that the *customer* sees
        no second confirmation regardless.
        """

        _make_chat_mirror_row(
            tenant, client_bot_user, status=RemoteBookingProxy.Status.PENDING_PAYMENT
        )
        _make_chat_booking_marker(tenant, client_bot_user)

        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope(status="awaiting_payment"))
        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_confirmed(
                _lifecycle_envelope(event_name="booking.confirmed", event_id=EVENT_ID_CONFIRMED)
            )

        assert send.client == []
        assert len(send.salon) == 1


# ─── the regression it must not cause ──────────────────────────────────────


class TestMiniAppBookingsUnchanged:
    """The path that already worked, pinned so the fix cannot break it."""

    def test_miniapp_booking_still_notifies_the_salon(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        _make_service(tenant)
        _make_master(tenant)

        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope(source="miniapp"))

        assert len(send.salon) == 1
        assert "Источник: мини-приложение" in send.salon[0]["text"]

    def test_miniapp_booking_still_confirms_to_the_client(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        """DRF-1066's own incident path — both sides, one event."""

        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope())

        assert len(send.client) == 1
        assert "Вы записаны" in send.client[0]["text"]
        assert len(send.salon) == 1

        proxy = _proxy()
        assert proxy.salon_notified_at is not None
        assert proxy.client_notified_at is not None

    def test_orphan_booking_still_notifies_the_salon(
        self,
        tenant: Tenant,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        """No ``BotUser`` at all — the salon still gets its message.

        The two audiences fail independently: a customer who has never
        opened the bot is not a reason to keep the salon in the dark.
        """

        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope())

        assert len(send.salon) == 1
        assert send.client == []


# ─── one announcement per appointment ──────────────────────────────────────


class TestOneAnnouncementPerAppointment:
    def test_redelivery_under_a_fresh_event_id_announces_nothing_more(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        """The case the old gate could not have handled.

        ``last_synced_event_id`` does not match, so the replay
        short-circuit does not fire; the claim is the only thing
        standing between the salon and a second «🆕 Новая запись».
        """

        for event_id in (EVENT_ID, EVENT_ID_REDELIVERY):
            with django_capture_on_commit_callbacks(execute=True):
                handle_booking_created(_created_envelope(event_id=event_id))

        assert len(send.salon) == 1
        assert len(send.client) == 1

    def test_redelivery_of_a_chat_booking_announces_nothing_more(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        """Same, through the advanced-state branch.

        A chat booking never updates ``last_synced_event_id`` (the no-op
        leaves it for the more advanced event), so *every* delivery
        arrives looking new. The claim is the only guard here.
        """

        _make_chat_mirror_row(tenant, client_bot_user)
        _make_chat_booking_marker(tenant, client_bot_user)

        for event_id in (EVENT_ID, EVENT_ID_REDELIVERY):
            with django_capture_on_commit_callbacks(execute=True):
                handle_booking_created(_created_envelope(event_id=event_id))

        assert len(send.salon) == 1
        assert send.client == []

    def test_exact_replay_announces_nothing_more(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        for _ in range(3):
            with django_capture_on_commit_callbacks(execute=True):
                handle_booking_created(_created_envelope())

        assert len(send.salon) == 1
        assert len(send.client) == 1

    def test_both_client_call_sites_share_one_claim(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        """``handle_booking_created`` and ``handle_booking_confirmed``.

        Previously they were mutually exclusive only by construction —
        each reasoning from its own view of the state machine. Now they
        contend for one durable fact, so the argument no longer has to
        hold for the guarantee to.
        """

        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope())
        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_confirmed(
                _lifecycle_envelope(event_name="booking.confirmed", event_id=EVENT_ID_CONFIRMED)
            )

        assert len(send.client) == 1


# ─── the claim primitive ───────────────────────────────────────────────────


class TestClaimPrimitive:
    def test_exactly_one_caller_wins(self, tenant: Tenant) -> None:
        _make_chat_mirror_row(tenant)
        appointment_id = uuid.UUID(APPOINTMENT_ID)

        first = _claim_announcement(
            tenant=tenant, appointment_id=appointment_id, slot="salon_notified_at"
        )
        second = _claim_announcement(
            tenant=tenant, appointment_id=appointment_id, slot="salon_notified_at"
        )

        assert (first, second) == (True, False)
        assert _proxy().salon_notified_at is not None

    def test_the_two_slots_are_independent(self, tenant: Tenant) -> None:
        """One side being told says nothing about the other."""

        _make_chat_mirror_row(tenant)
        appointment_id = uuid.UUID(APPOINTMENT_ID)

        assert _claim_announcement(
            tenant=tenant, appointment_id=appointment_id, slot="salon_notified_at"
        )
        assert _claim_announcement(
            tenant=tenant, appointment_id=appointment_id, slot="client_notified_at"
        )

    def test_a_missing_row_cannot_be_claimed(self, tenant: Tenant) -> None:
        """No row, no claim — and no crash."""

        assert not _claim_announcement(
            tenant=tenant,
            appointment_id=uuid.UUID(APPOINTMENT_ID),
            slot="salon_notified_at",
        )

    def test_the_claim_is_scoped_by_tenant(self, tenant: Tenant) -> None:
        """Another tenant cannot take — or burn — this one's claim.

        The call sites already assert proxy tenancy, so this cannot
        change an outcome today. It is pinned so the primitive stays
        provably safe on its own if a future caller forgets.
        """

        _make_chat_mirror_row(tenant)
        other = Tenant.objects.create(id=OTHER_TENANT_ID, slug="other-salon", name="Другой салон")

        assert not _claim_announcement(
            tenant=other,
            appointment_id=uuid.UUID(APPOINTMENT_ID),
            slot="salon_notified_at",
        )
        assert _proxy().salon_notified_at is None


# ─── terminal statuses ─────────────────────────────────────────────────────


class TestTerminalStatusesAnnounceNothing:
    """A stale creation event for a booking that is already over."""

    def test_created_after_cancellation_announces_nothing(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope())
        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_cancelled(
                _lifecycle_envelope(event_name="booking.cancelled", event_id=EVENT_ID_CANCELLED)
            )
        send.calls.clear()

        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope(event_id=EVENT_ID_REDELIVERY))

        assert send.calls == []

    def test_a_terminal_state_leaves_the_claim_free(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        """Nothing announced, so nothing recorded as announced.

        The claim means «somebody told this side», and here nobody did.
        Burning it would make the record lie.
        """

        _make_chat_mirror_row(tenant, client_bot_user, status=RemoteBookingProxy.Status.CANCELLED)

        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope())

        assert send.calls == []
        proxy = _proxy()
        assert proxy.salon_notified_at is None
        assert proxy.client_notified_at is None


# ─── rows that predate the columns ─────────────────────────────────────────


class TestPreMigrationRows:
    """The pilot has live mirror rows; the deploy must not announce them."""

    def test_migration_claims_every_pre_existing_row(self, tenant: Tenant) -> None:
        """Run the migration's own data step against a historical model.

        ``claim_pre_migration_rows`` is handed the state Django renders
        for migration ``0018`` — not the live model — so this also pins
        the assumption the function rests on: a historical model carries
        a plain manager, and its ``.objects.update()`` is therefore
        unscoped by tenant, not silently filtered to none by
        ``TenantScopedManager``.
        """

        proxy = _make_chat_mirror_row(tenant)
        assert proxy.salon_notified_at is None

        migration = import_module(f"apps.booking.migrations.{MIGRATION_NAME}")
        historical_apps = (
            MigrationExecutor(connection).loader.project_state(("booking", MIGRATION_NAME)).apps
        )
        migration.claim_pre_migration_rows(historical_apps, connection.schema_editor)

        proxy.refresh_from_db()
        # Stamped with the row's own birth, not the deploy instant: the
        # claim is as old as the row, which is the fact being recorded.
        assert proxy.salon_notified_at == proxy.created_at
        assert proxy.client_notified_at == proxy.created_at

    def test_a_claimed_row_is_never_announced(
        self,
        tenant: Tenant,
        client_bot_user: BotUser,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        """The state migration ``0018`` leaves behind, seen by the consumer.

        Without the backfill, NULL would read as «free» and the first
        ``booking.created`` after the deploy — a re-delivery, a replay,
        a backfill — would page the salon about an appointment that may
        already have happened.
        """

        proxy = _make_chat_mirror_row(tenant, client_bot_user)
        RemoteBookingProxy.all_tenants.filter(pk=proxy.pk).update(
            salon_notified_at=proxy.created_at,
            client_notified_at=proxy.created_at,
        )

        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_created_envelope())

        assert send.calls == []


# ─── rollback ──────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
class TestRollback:
    def test_rolled_back_ingest_announces_nothing_and_releases_the_claim(
        self, tenant: Tenant, client_bot_user: BotUser, send: SendRecorder
    ) -> None:
        """The transactional half of the claim, both directions.

        The mirror row is committed first (as the dialog path would have
        committed it), so what rolls back is only the ingest. Nothing is
        announced — and the claim must come back *free*, or a transient
        ingest failure would silence the appointment permanently. The
        retry that follows proves it did.
        """

        _make_chat_mirror_row(tenant, client_bot_user)

        with pytest.raises(RuntimeError, match="force rollback"):
            with transaction.atomic():
                handle_booking_created(_created_envelope())
                raise RuntimeError("force rollback")

        assert send.calls == []
        assert _proxy().salon_notified_at is None

        handle_booking_created(_created_envelope(event_id=EVENT_ID_REDELIVERY))

        assert len(send.salon) == 1
        assert _proxy().salon_notified_at is not None
