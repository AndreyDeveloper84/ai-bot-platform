"""New-booking → MAX notification tests (DRF-1030).

The audit that produced this ticket found that over the entire pilot
history **not one** booking notification reached a human: the only
channel was a push into a mobile app with zero registered devices, and
the bot never addressed the salon at all. These tests pin the
replacement so the failure mode cannot come back silently.

Covered:

* every rung of the addressing cascade — linked master, tenant
  ``manager_chat_id``, configured fallback chat ids;
* precedence between the rungs (first hit wins, exclusively);
* **no address at all** → nothing sent, WARNING logged (the branch that
  used to be silent);
* message content — service, master, tenant-local time, source — and
  the DRF-1039 rule that no client data is present;
* best-effort containment: a MAX failure (or any other exception) never
  escapes into the ingest path;
* wiring through ``handle_booking_created``: the send happens after
  commit, a rolled-back ingest sends nothing, and a re-delivered
  ``booking.created`` does not announce the same appointment twice.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from django.db import transaction
from django.utils import timezone

from apps.booking.master_notify import (
    build_booking_created_notification,
    notify_booking_created,
    resolve_target,
)
from apps.booking.models import RemoteBookingProxy
from apps.catalog.models import CatalogMaster, CatalogService
from apps.channels.max.outbound import MaxAPIError
from apps.eventbus.consumers.booking import handle_booking_created
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

# The notification reuses the DRF-1029 fan-out primitive, so the send
# is patched where that primitive imports it.
NOTIFY_SEND = "apps.handoff.notify.send_message"

TENANT_ID = "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c"
AYLA_USER_ID = "f1a2b3c4-d5e6-4789-9abc-def012345678"
APPOINTMENT_ID = "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8"
SPECIALIST_ID = "7c2d8e1f-0a5c-4c3a-9e1b-4d52f8eb3a17"
SERVICE_ID = "3d5f7e1c-8a2d-4e6f-b9c0-1d2e3f4a5b6c"

START_AT = "2026-05-22T15:00:00+03:00"
END_AT = "2026-05-22T16:00:00+03:00"


class SendRecorder:
    """Stand-in for ``channels.max.outbound.send_message``.

    Same shape as the DRF-1029 recorder: records every call and replays
    ``side_effects`` so MAX failures can be simulated per recipient.
    """

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
        if isinstance(effects, list):
            effect = effects[min(len(self.calls), len(effects)) - 1]
            if isinstance(effect, Exception):
                raise effect
            return {}
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
def _no_fallback_by_default(settings) -> None:
    """Default every test to «fallback channel not configured».

    Tests that exercise the fallback rung opt in explicitly, so a rung
    can never pass by accident.
    """

    settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = []


def _make_master(
    tenant: Tenant,
    *,
    name: str = "Тихонова Ольга",
    linked_chat_id: str | None = None,
    ayla_user_id: str = SPECIALIST_ID,
) -> CatalogMaster:
    linked = None
    if linked_chat_id is not None:
        linked = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id=f"master-{uuid.uuid4().hex[:8]}",
            chat_id=linked_chat_id,
        )
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=1,
        external_updated_at=timezone.now(),
        name=name,
        ayla_user_id=ayla_user_id,
        linked_bot_user=linked,
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


def _notify(tenant: Tenant, *, raw_source: str = "mobile_app") -> None:
    notify_booking_created(
        tenant=tenant,
        appointment_id=uuid.UUID(APPOINTMENT_ID),
        start_at=dt.datetime.fromisoformat(START_AT),
        specialist_id=uuid.UUID(SPECIALIST_ID),
        service_id=uuid.UUID(SERVICE_ID),
        raw_source=raw_source,
    )


# ─── cascade ───────────────────────────────────────────────────────────────


class TestAddressingCascade:
    def test_linked_master_receives_it(self, tenant: Tenant, send: SendRecorder) -> None:
        """Rung 1 — the master's own MAX chat, once the link exists.

        Impossible on the pilot today (``linked_bot_user`` is NULL for
        all four masters); implemented so that linking them is a data
        change, not a code change.
        """

        _make_master(tenant, linked_chat_id="master-chat-1")
        _notify(tenant)
        assert [c["chat_id"] for c in send.calls] == ["master-chat-1"]

    def test_manager_chat_id_when_master_not_linked(
        self, tenant: Tenant, send: SendRecorder
    ) -> None:
        """Rung 2 — the salon manager."""

        _make_master(tenant, linked_chat_id=None)
        tenant.manager_chat_id = "manager-chat-1"
        tenant.save(update_fields=["manager_chat_id"])
        _notify(tenant)
        assert [c["chat_id"] for c in send.calls] == ["manager-chat-1"]

    def test_settings_fallback_when_nothing_else(
        self, tenant: Tenant, send: SendRecorder, settings
    ) -> None:
        """Rung 3 — the configured fallback chat(s), fanned out."""

        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = ["owner-chat", "ops-chat"]
        _make_master(tenant, linked_chat_id=None)
        _notify(tenant)
        assert [c["chat_id"] for c in send.calls] == ["owner-chat", "ops-chat"]

    def test_no_recipient_anywhere_warns_and_sends_nothing(
        self, tenant: Tenant, send: SendRecorder, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Rung 4 — the branch that used to be silence.

        No linked master, no ``manager_chat_id``, no fallback setting:
        exactly the pilot's current configuration. Nothing is sent, and
        the gap is recorded at WARNING so it is discoverable in logs
        instead of vanishing.
        """

        _make_master(tenant, linked_chat_id=None)
        with caplog.at_level("DEBUG", logger="apps.booking.master_notify"):
            _notify(tenant)
        assert send.calls == []
        warnings = [
            r for r in caplog.records if r.name == "apps.booking.master_notify" and r.levelno >= 30
        ]
        assert len(warnings) == 1
        assert "booking.notify.no_recipients" in warnings[0].getMessage()

    def test_master_matched_by_specialist_profile_id(
        self, tenant: Tenant, send: SendRecorder
    ) -> None:
        """The mirror is keyed on ``SpecialistProfile.id``, not the user id.

        ``upsert_specialists`` writes ``CatalogMaster.id =
        ayla_master_id`` and ``ayla_user_id = user_id``; the event
        contract does not say which of the two ``specialist_id``
        carries, so both must resolve.
        """

        linked = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="master-by-profile",
            chat_id="master-chat-2",
        )
        CatalogMaster.all_tenants.create(
            id=uuid.UUID(SPECIALIST_ID),
            tenant=tenant,
            external_id=2,
            external_updated_at=timezone.now(),
            name="Сазонова Инна",
            ayla_user_id=None,
            linked_bot_user=linked,
        )
        _notify(tenant)
        assert [c["chat_id"] for c in send.calls] == ["master-chat-2"]
        assert "Сазонова Инна" in send.calls[0]["text"]

    def test_no_master_row_at_all_still_falls_back(
        self, tenant: Tenant, send: SendRecorder
    ) -> None:
        """An unmirrored specialist must not swallow the notification."""

        tenant.manager_chat_id = "manager-chat-1"
        tenant.save(update_fields=["manager_chat_id"])
        _notify(tenant)
        assert [c["chat_id"] for c in send.calls] == ["manager-chat-1"]

    def test_blank_chat_ids_are_treated_as_absent(
        self, tenant: Tenant, send: SendRecorder, settings
    ) -> None:
        """Whitespace is not an address — the cascade keeps walking."""

        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = ["fallback-chat"]
        _make_master(tenant, linked_chat_id="   ")
        tenant.manager_chat_id = "  "
        tenant.save(update_fields=["manager_chat_id"])
        _notify(tenant)
        assert [c["chat_id"] for c in send.calls] == ["fallback-chat"]


class TestCascadePrecedence:
    def test_master_wins_over_manager_and_fallback(
        self, tenant: Tenant, send: SendRecorder, settings
    ) -> None:
        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = ["owner-chat"]
        _make_master(tenant, linked_chat_id="master-chat-1")
        tenant.manager_chat_id = "manager-chat-1"
        tenant.save(update_fields=["manager_chat_id"])
        _notify(tenant)
        assert [c["chat_id"] for c in send.calls] == ["master-chat-1"]

    def test_manager_wins_over_fallback(self, tenant: Tenant, send: SendRecorder, settings) -> None:
        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = ["owner-chat"]
        tenant.manager_chat_id = "manager-chat-1"
        tenant.save(update_fields=["manager_chat_id"])
        _notify(tenant)
        assert [c["chat_id"] for c in send.calls] == ["manager-chat-1"]

    def test_resolve_target_reports_the_rung(self, tenant: Tenant) -> None:
        """The resolver names its own decision — logs and future
        personal-addressing work depend on that label."""

        master = _make_master(tenant, linked_chat_id="master-chat-1")
        assert resolve_target(tenant=tenant, master=master).channel == "master"
        assert resolve_target(tenant=tenant, master=None).channel == "none"


# ─── message body ──────────────────────────────────────────────────────────


class TestNotificationText:
    def test_carries_service_master_time_and_source(
        self, tenant: Tenant, send: SendRecorder
    ) -> None:
        _make_service(tenant)
        _make_master(tenant, linked_chat_id="master-chat-1")
        _notify(tenant)
        text = send.calls[0]["text"]
        assert "УЗ-кавитация — 1 зона" in text
        assert "Тихонова Ольга" in text
        # 15:00 +03:00 rendered in the tenant's own timezone (MSK).
        assert "22.05.2026 в 15:00" in text
        assert "мобильное приложение" in text
        assert APPOINTMENT_ID in text

    def test_time_is_rendered_in_tenant_timezone(self, tenant: Tenant, send: SendRecorder) -> None:
        """A booking shown in the wrong timezone is worse than none."""

        tenant.timezone = "Asia/Yekaterinburg"  # UTC+5
        tenant.manager_chat_id = "manager-chat-1"
        tenant.save(update_fields=["timezone", "manager_chat_id"])
        _notify(tenant)
        assert "22.05.2026 в 17:00" in send.calls[0]["text"]

    def test_invalid_tenant_timezone_degrades_to_msk(
        self, tenant: Tenant, send: SendRecorder
    ) -> None:
        tenant.timezone = "Not/AZone"
        tenant.manager_chat_id = "manager-chat-1"
        tenant.save(update_fields=["timezone", "manager_chat_id"])
        _notify(tenant)
        assert "22.05.2026 в 15:00" in send.calls[0]["text"]

    def test_unmirrored_service_and_master_degrade_gracefully(
        self, tenant: Tenant, send: SendRecorder
    ) -> None:
        """A missing catalog row must not cost the salon the message."""

        tenant.manager_chat_id = "manager-chat-1"
        tenant.save(update_fields=["manager_chat_id"])
        _notify(tenant)
        text = send.calls[0]["text"]
        assert "Услуга: —" in text
        assert "Мастер: —" in text

    def test_unknown_source_passes_through(self, tenant: Tenant, send: SendRecorder) -> None:
        tenant.manager_chat_id = "manager-chat-1"
        tenant.save(update_fields=["manager_chat_id"])
        _notify(tenant, raw_source="brand_new_channel")
        assert "Источник: brand_new_channel" in send.calls[0]["text"]

    def test_no_client_pii(self, tenant: Tenant, send: SendRecorder) -> None:
        """DRF-1039: the performer never receives the client's identity.

        The booking event carries a client (``user_id``) and the bot has
        a ``BotUser`` row with a name and a phone for them. None of it
        may reach the message.
        """

        BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="client-1",
            chat_id="client-chat",
            display_name="Иван Клиентов",
            client_name="Иван Клиентов",
            phone="+79991234567",
            ayla_user_id=AYLA_USER_ID,
        )
        tenant.manager_chat_id = "manager-chat-1"
        tenant.save(update_fields=["manager_chat_id"])
        _notify(tenant)
        text = send.calls[0]["text"]
        assert "+79991234567" not in text
        assert "Иван Клиентов" not in text
        assert AYLA_USER_ID not in text

    def test_build_is_pure(self, tenant: Tenant, django_assert_num_queries) -> None:
        """Formatting touches the DB zero times.

        The callback runs outside ``tenant_scope``; under the pilot's
        audit-mode scoping a stray query there would return emptiness
        rather than raise, silently rendering wrong data.
        """

        with django_assert_num_queries(0):
            text = build_booking_created_notification(
                tenant=tenant,
                appointment_id=uuid.UUID(APPOINTMENT_ID),
                start_at=dt.datetime.fromisoformat(START_AT),
                service_name="Массаж",
                master_name="Ольга",
                raw_source="mobile_app",
            )
        assert "Массаж" in text


# ─── containment ───────────────────────────────────────────────────────────


class TestBestEffort:
    def test_max_failure_never_escapes(
        self, tenant: Tenant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A dead MAX must not dead-letter the booking event."""

        tenant.manager_chat_id = "manager-chat-1"
        tenant.save(update_fields=["manager_chat_id"])
        monkeypatch.setattr(NOTIFY_SEND, SendRecorder(side_effects=MaxAPIError(500, "down")))
        _notify(tenant)  # must not raise

    def test_unexpected_exception_never_escapes(
        self, tenant: Tenant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tenant.manager_chat_id = "manager-chat-1"
        tenant.save(update_fields=["manager_chat_id"])
        monkeypatch.setattr(NOTIFY_SEND, SendRecorder(side_effects=RuntimeError("boom")))
        _notify(tenant)  # must not raise

    def test_one_failing_recipient_does_not_cancel_the_others(
        self, tenant: Tenant, monkeypatch: pytest.MonkeyPatch, settings
    ) -> None:
        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = ["bad", "good"]
        recorder = SendRecorder(side_effects=[MaxAPIError(500, "boom"), {}])
        monkeypatch.setattr(NOTIFY_SEND, recorder)
        _notify(tenant)
        assert [c["chat_id"] for c in recorder.calls] == ["bad", "good"]

    def test_send_uses_the_short_timeout(self, tenant: Tenant, send: SendRecorder) -> None:
        """The ingest consumer is single-threaded — never block it."""

        tenant.manager_chat_id = "manager-chat-1"
        tenant.save(update_fields=["manager_chat_id"])
        _notify(tenant)
        assert send.calls[0]["timeout"] <= 5.0


# ─── wiring through the consumer ───────────────────────────────────────────


def _envelope(*, event_id: str = "01J9HXKM8Z2T4V6R8Q1P3D5F7E") -> IngestEnvelope:
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
            "status": "confirmed",
            "source": "mobile_app",
        },
    )


@pytest.fixture
def _ingest_allowlist(settings) -> None:
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = False
    settings.EVENT_INGEST_ALLOWED_TENANTS = frozenset({TENANT_ID})
    settings.EVENT_INGEST_ALLOWED_EVENTS = frozenset({"booking.created"})


@pytest.mark.usefixtures("_ingest_allowlist")
class TestConsumerWiring:
    def test_booking_created_announces_after_commit(
        self,
        tenant: Tenant,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        _make_service(tenant)
        _make_master(tenant, linked_chat_id="master-chat-1")
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            handle_booking_created(_envelope())
        # Exactly one notification callback, and it actually sent.
        assert len(callbacks) >= 1
        assert [c["chat_id"] for c in send.calls] == ["master-chat-1"]
        assert "УЗ-кавитация — 1 зона" in send.calls[0]["text"]

    def test_nothing_sent_before_commit(self, tenant: Tenant, send: SendRecorder) -> None:
        """The send is queued, never executed inside the handler.

        pytest-django's non-transactional ``db`` keeps the test inside an
        atomic block, so a callback registered here can only run if
        something explicitly flushes it — nothing does.
        """

        tenant.manager_chat_id = "manager-chat-1"
        tenant.save(update_fields=["manager_chat_id"])
        handle_booking_created(_envelope())
        assert send.calls == []
        assert RemoteBookingProxy.all_tenants.filter(
            appointment_id=uuid.UUID(APPOINTMENT_ID)
        ).exists()

    def test_redelivery_with_new_event_id_does_not_re_announce(
        self,
        tenant: Tenant,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        """One appointment, one announcement.

        The fallback channel is the owner's personal chat on the pilot —
        duplicates there are how a channel gets muted.
        """

        tenant.manager_chat_id = "manager-chat-1"
        tenant.save(update_fields=["manager_chat_id"])
        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_envelope())
        with django_capture_on_commit_callbacks(execute=True):
            handle_booking_created(_envelope(event_id="01J9HXKM8Z2T4V6R8Q1P3D5F7F"))
        assert len(send.calls) == 1

    def test_exact_replay_does_not_re_announce(
        self,
        tenant: Tenant,
        send: SendRecorder,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        tenant.manager_chat_id = "manager-chat-1"
        tenant.save(update_fields=["manager_chat_id"])
        for _ in range(3):
            with django_capture_on_commit_callbacks(execute=True):
                handle_booking_created(_envelope())
        assert len(send.calls) == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("_ingest_allowlist")
class TestRollback:
    def test_rolled_back_ingest_announces_nothing(self, tenant: Tenant, send: SendRecorder) -> None:
        """A booking that was never persisted must never be announced."""

        tenant.manager_chat_id = "manager-chat-1"
        tenant.save(update_fields=["manager_chat_id"])
        with pytest.raises(RuntimeError, match="force rollback"):
            with transaction.atomic():
                handle_booking_created(_envelope())
                raise RuntimeError("force rollback")
        assert send.calls == []
        assert not RemoteBookingProxy.all_tenants.filter(
            appointment_id=uuid.UUID(APPOINTMENT_ID)
        ).exists()


class TestSenderIdentity:
    """Whose avatar the booking notice arrives from (DRF-1030 + DRF-1061).

    This message is work — "you have a new booking". Arriving from the
    customer-facing bot it reads as a marketing push to the very people
    meant to act on it, and a reply lands in the customer funnel. The two
    conversations are separate bots precisely so this does not happen.
    """

    @pytest.fixture
    def _tokens(self, settings):
        settings.MAX_BOT_TOKEN = "token-client"  # pragma: allowlist secret

    def _salon_entry(self, tenant_slug: str):
        from apps.channels.bot_registry import BotEntry

        return BotEntry(
            slug="salon",
            webhook_secret="wh-salon",  # pragma: allowlist secret
            api_token="token-salon",  # pragma: allowlist secret
            tenant_slug=tenant_slug,
            stream="max_salon",
        )

    def _capture_token(self, monkeypatch) -> list[str]:
        """Record the token outbound WOULD use at send time."""

        seen: list[str] = []

        def _fake_send(*, text, chat_ids, **kwargs):
            from apps.channels.max.outbound import _token

            seen.append(_token())
            return 0

        monkeypatch.setattr("apps.booking.master_notify.send_max_notification", _fake_send)
        return seen

    def test_sent_as_the_salon_bot_when_the_salon_has_one(
        self, tenant, settings, monkeypatch, _tokens
    ):
        settings.MAX_BOT_REGISTRY = (self._salon_entry(tenant.slug),)
        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = ["999"]
        seen = self._capture_token(monkeypatch)

        notify_booking_created(
            tenant=tenant,
            appointment_id=uuid.uuid4(),
            start_at=timezone.now(),
            specialist_id=None,
            service_id=None,
            raw_source="chat",
        )

        assert seen == ["token-salon"]

    def test_falls_back_to_the_configured_bot_when_there_is_no_salon_bot(
        self, tenant, settings, monkeypatch, _tokens
    ):
        # Deliberate: a notice from the wrong avatar beats no notice at
        # all. Silence is what made this gap invisible for months.
        settings.MAX_BOT_REGISTRY = ()
        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = ["999"]
        seen = self._capture_token(monkeypatch)

        notify_booking_created(
            tenant=tenant,
            appointment_id=uuid.uuid4(),
            start_at=timezone.now(),
            specialist_id=None,
            service_id=None,
            raw_source="chat",
        )

        assert seen == ["token-client"]

    def test_another_salons_bot_is_not_borrowed(self, tenant, settings, monkeypatch, _tokens):
        settings.MAX_BOT_REGISTRY = (self._salon_entry("some-other-salon"),)
        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = ["999"]
        seen = self._capture_token(monkeypatch)

        notify_booking_created(
            tenant=tenant,
            appointment_id=uuid.uuid4(),
            start_at=timezone.now(),
            specialist_id=None,
            service_id=None,
            raw_source="chat",
        )

        # Not token-salon: that bot belongs to a different salon.
        assert seen == ["token-client"]

    def test_a_client_bot_on_the_same_tenant_is_not_mistaken_for_the_staff_bot(
        self, tenant, settings, monkeypatch, _tokens
    ):
        from apps.channels.bot_registry import BotEntry

        settings.MAX_BOT_REGISTRY = (
            BotEntry(
                slug="client",
                webhook_secret="wh-c",  # pragma: allowlist secret
                api_token="token-per-tenant-client",  # pragma: allowlist secret
                tenant_slug=tenant.slug,
                stream="max",
            ),
        )
        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = ["999"]
        seen = self._capture_token(monkeypatch)

        notify_booking_created(
            tenant=tenant,
            appointment_id=uuid.uuid4(),
            start_at=timezone.now(),
            specialist_id=None,
            service_id=None,
            raw_source="chat",
        )

        # Matched on tenant AND stream — a same-tenant client bot must not
        # be picked up as the staff one.
        assert seen == ["token-client"]

    def test_identity_failure_never_breaks_the_notification(
        self, tenant, settings, monkeypatch, _tokens
    ):
        # Hard containment: a broken registry must degrade to "sent by the
        # default bot", never to "booking event dead-lettered".
        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = ["999"]

        def _boom(*_a, **_k):
            raise RuntimeError("registry exploded")

        monkeypatch.setattr("apps.channels.bot_registry.effective_registry", _boom)
        seen = self._capture_token(monkeypatch)

        notify_booking_created(
            tenant=tenant,
            appointment_id=uuid.uuid4(),
            start_at=timezone.now(),
            specialist_id=None,
            service_id=None,
            raw_source="chat",
        )

        assert seen == ["token-client"]
