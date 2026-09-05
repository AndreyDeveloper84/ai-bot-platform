"""DRF-1492 — the booking gate's answers carry their next step.

``apps/bookings/callbacks.py`` ran 960 lines without a single ``action_data``.
Three of its replies are the ENDS of a funnel — «Готово! Записала…», «Запись
отменена…», «Ок, не записываю.» — and the owner's ruling of 04.09
(``OPEN_DECISIONS`` §25 п.3) is that the end of a booking must not be a dead
end. Two more name a next step in words and gave nothing to press:
«давайте подберём слот заново», «откройте актуальные записи».

### Why every chip here is ``cb:menu:*`` or ``cb:book:pick_master:``

This skill answers on two surfaces. On a salon's own bot the tap reaches the
skill registry; on the global Ayla bot the SAME dispatch is reached through
``apps.orchestrator.handoff.route_booking_callback``. A chip that executes in
one chat and lands in «я вас не понял» in the other is worse than no chip —
the trust is already spent by then. Both families cross that boundary by
construction, and the last test in this module is what proves it rather than
asserting it: it resolves each callback through both translators.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.booking.models import BookingReminder, BookingRequest, PendingBookingAction
from apps.bookings.callbacks import (
    REPLY_BOOK_CANCELLED_PREVIEW,
    REPLY_BOOK_EXPIRED,
    REPLY_BOOK_KEPT_PREVIEW,
    REPLY_CANCELLED,
    REPLY_CONFIRMED,
    REPLY_RESCHEDULE,
    BookingGateCallbackSkill,
    BookingReminderCallbackSkill,
)
from apps.bookings.keyboards import CALLBACK_BOOK_PICK_MASTER_PREFIX
from apps.bookings.pending_actions import create_pending
from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.integrations.yclients import AvailableTime, BookingRecord
from apps.skills.base import SkillContext
from apps.skills.menu.matching import CALLBACK_MENU_BOOK, CALLBACK_MENU_MY_BOOKINGS
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="dead-ends", name="Dead Ends", manager_chat_id="mgr-1")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="de-1",
        chat_id="de-1",
        phone="79991234567",
        client_name="Anna",
    )


@pytest.fixture
def conversation(tenant: Tenant, bot_user: BotUser) -> Conversation:
    with tenant_scope(tenant):
        return Conversation.objects.create(
            tenant=tenant, bot_user=bot_user, state=Conversation.State.IDLE
        )


class _FakeYClients:
    def __init__(self) -> None:
        self.create_calls: list[dict] = []
        self.cancel_calls: list[int] = []
        self.times: list[AvailableTime] = []

    def create_record(self, **kwargs):
        self.create_calls.append(kwargs)
        return BookingRecord(record_id=777, record_hash="h", raw={})

    def cancel_record(self, *, record_id: int) -> bool:
        self.cancel_calls.append(record_id)
        return True

    def get_available_times(self, **_: Any) -> list[AvailableTime]:
        return list(self.times)


def _patched(client: _FakeYClients):
    return patch("apps.integrations.yclients.get_yclients_client", return_value=client)


def _ctx(text: str, *, bot_user: BotUser, conversation: Conversation) -> SkillContext:
    return SkillContext(
        conversation=conversation,
        bot_user=bot_user,
        message_text=text,
        has_attachments=False,
    )


def _callbacks(result) -> list[str]:
    attachments = (result.action_data or {}).get("attachments") or []
    return [
        button["callback"]
        for att in attachments
        for button in (att.get("payload") or {}).get("buttons") or []
    ]


def _confirm_payload(slot_iso: str) -> dict[str, Any]:
    return {
        "master_id": 11,
        "service_id": 22,
        "slot_datetime": slot_iso,
        "client_phone": "79991234567",
        "client_name": "Anna",
        "master_name": "Ольга",
        "service_name": "Массаж",
    }


def _future_iso() -> str:
    return (timezone.now() + timedelta(days=2)).replace(microsecond=0).isoformat()


def _reminder(tenant: Tenant, bot_user: BotUser, *, yc_id: str = "555") -> BookingReminder:
    visit_at = timezone.now() + timedelta(days=1)
    return BookingReminder.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        yclients_record_id=yc_id,
        chat_id=bot_user.chat_id,
        visit_at=visit_at,
        kind=BookingReminder.Kind.DAY_BEFORE,
        status=BookingReminder.Status.SENT_NO_REPLY,
        scheduled_at=visit_at - timedelta(hours=24),
        master_name="Ольга",
        service_name="Массаж",
    )


class TestFunnelEndsAreNotDeadEnds:
    def test_booking_confirmed_offers_the_booking_it_just_made(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        """§25 п.3 — «Готово! Записала…» was the end of the conversation too.

        «Мои записи» is the one next step that is true right after a confirm:
        it reads the backend and shows the row that was just created.
        """
        token = create_pending(
            tenant=tenant,
            bot_user=bot_user,
            kind=PendingBookingAction.Kind.CONFIRM,
            payload=_confirm_payload(_future_iso()),
        )
        client = _FakeYClients()
        with _patched(client):
            result = BookingGateCallbackSkill().handle(
                _ctx(f"cb:book:confirm:{token}", bot_user=bot_user, conversation=conversation)
            )

        assert client.create_calls  # the booking really happened
        assert "Готово! Записала." in result.reply_text
        assert _callbacks(result) == [CALLBACK_MENU_MY_BOOKINGS]

    def test_booking_cancelled_offers_the_way_back(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        with tenant_scope(tenant):
            BookingRequest.objects.create(
                tenant=tenant,
                bot_user=bot_user,
                service_name="Массаж",
                master_name="Ольга",
                client_name="Anna",
                client_phone="79991234567",
                comment="Bot booking | yclients_record_id=555",
                source="bot",
                status=BookingRequest.Status.CONFIRMED,
            )
        token = create_pending(
            tenant=tenant,
            bot_user=bot_user,
            kind=PendingBookingAction.Kind.CANCEL,
            payload={"record_id": 555, "reason": "", "booking_request_id": ""},
        )
        client = _FakeYClients()
        with _patched(client):
            result = BookingGateCallbackSkill().handle(
                _ctx(f"cb:book:confirm:{token}", bot_user=bot_user, conversation=conversation)
            )

        assert client.cancel_calls == [555]
        assert "отменена" in result.reply_text
        assert _callbacks(result) == [CALLBACK_MENU_BOOK]

    def test_reminder_cancel_offers_the_way_back(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        reminder = _reminder(tenant, bot_user)

        result = BookingReminderCallbackSkill().handle(
            _ctx(f"cb:rem:cancel:{reminder.pk}", bot_user=bot_user, conversation=conversation)
        )

        assert result.reply_text == REPLY_CANCELLED
        assert _callbacks(result) == [CALLBACK_MENU_BOOK]

    def test_abandoned_booking_preview_reopens_the_picker(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        """«Ок, не записываю.» — the pair is still on the row, so the chip
        re-enters the date picker for THAT master and service rather than
        starting the whole search over."""
        token = create_pending(
            tenant=tenant,
            bot_user=bot_user,
            kind=PendingBookingAction.Kind.CONFIRM,
            payload=_confirm_payload(_future_iso()),
        )
        client = _FakeYClients()
        with _patched(client):
            result = BookingGateCallbackSkill().handle(
                _ctx(f"cb:book:cancel:{token}", bot_user=bot_user, conversation=conversation)
            )

        assert result.reply_text == REPLY_BOOK_CANCELLED_PREVIEW
        assert _callbacks(result) == [f"{CALLBACK_BOOK_PICK_MASTER_PREFIX}11:22"]
        assert client.create_calls == []  # nothing was booked

    def test_abandoned_cancel_preview_says_what_it_actually_did(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        """The ticket's other rule applied to wording.

        ❌ over a CANCEL preview means «оставь мою запись». Answering «Ок, не
        записываю» told the person their booking would not be MADE — a
        sentence about the wrong verb, and an alarming one. Each verb says
        what it did now, and the step that follows differs with it.
        """
        token = create_pending(
            tenant=tenant,
            bot_user=bot_user,
            kind=PendingBookingAction.Kind.CANCEL,
            payload={"record_id": 555, "reason": "", "booking_request_id": ""},
        )
        client = _FakeYClients()
        with _patched(client):
            result = BookingGateCallbackSkill().handle(
                _ctx(f"cb:book:cancel:{token}", bot_user=bot_user, conversation=conversation)
            )

        assert result.reply_text == REPLY_BOOK_KEPT_PREVIEW
        assert result.reply_text != REPLY_BOOK_CANCELLED_PREVIEW
        assert _callbacks(result) == [CALLBACK_MENU_MY_BOOKINGS]
        assert client.cancel_calls == []  # the booking is untouched

    def test_expired_preview_offers_another_time_for_the_same_pair(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        token = create_pending(
            tenant=tenant,
            bot_user=bot_user,
            kind=PendingBookingAction.Kind.CONFIRM,
            payload=_confirm_payload(_future_iso()),
        )
        PendingBookingAction.all_tenants.filter(pk=token).update(
            expires_at=timezone.now() - timedelta(seconds=30)
        )
        client = _FakeYClients()
        with _patched(client):
            result = BookingGateCallbackSkill().handle(
                _ctx(f"cb:book:confirm:{token}", bot_user=bot_user, conversation=conversation)
            )

        assert result.reply_text == REPLY_BOOK_EXPIRED
        assert _callbacks(result) == [f"{CALLBACK_BOOK_PICK_MASTER_PREFIX}11:22"]
        assert client.create_calls == []  # no mutation on stale state


class TestRepliesThatStayButtonless:
    """The paired negatives (DRF-1411), each with its positive on the same
    handler and the same fixtures.

    These are not oversights. «Подтверждено, ждём вас!» and «Передал
    администратору, скоро напишут» name no action for the person to take —
    the first is an acknowledgement, the second says somebody else will act —
    and hanging a chip under them would invent a step nobody asked for.
    """

    def test_reminder_confirm_and_reschedule_stay_plain(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        # Positive guard first: the SAME skill, on the same fixture shape,
        # does draw a keyboard for the cancel branch.
        cancelled = BookingReminderCallbackSkill().handle(
            _ctx(
                f"cb:rem:cancel:{_reminder(tenant, bot_user, yc_id='901').pk}",
                bot_user=bot_user,
                conversation=conversation,
            )
        )
        assert _callbacks(cancelled) == [CALLBACK_MENU_BOOK]

        confirmed = BookingReminderCallbackSkill().handle(
            _ctx(
                f"cb:rem:confirm:{_reminder(tenant, bot_user, yc_id='902').pk}",
                bot_user=bot_user,
                conversation=conversation,
            )
        )
        assert confirmed.reply_text == REPLY_CONFIRMED
        assert confirmed.action_data is None

        moved = BookingReminderCallbackSkill().handle(
            _ctx(
                f"cb:rem:reschedule:{_reminder(tenant, bot_user, yc_id='903').pk}",
                bot_user=bot_user,
                conversation=conversation,
            )
        )
        assert moved.reply_text == REPLY_RESCHEDULE
        assert moved.action_data is None


class TestChipsExecuteOnBothSurfaces:
    """The property that made ``cb:menu:*`` the right family.

    Resolved through BOTH translators — the per-tenant ``MenuSkill`` map and
    the global handler's own — so a divergence between them fails here rather
    than in a chat.
    """

    def test_menu_callbacks_resolve_on_the_tenant_and_the_global_path(self) -> None:
        from apps.channels.max.quick_actions import resolve_tap_text
        from apps.skills.menu.matching import MENU_CALLBACK_TEXT

        for callback in (CALLBACK_MENU_MY_BOOKINGS, CALLBACK_MENU_BOOK):
            per_tenant = MENU_CALLBACK_TEXT.get(callback)
            assert per_tenant, callback
            assert resolve_tap_text(callback) == per_tenant

    def test_my_bookings_phrase_reaches_the_bookings_lookup(self) -> None:
        """And the phrase behind the chip is one the booking lookup claims —
        otherwise «Мои записи» would resolve to text nobody answers."""
        from apps.skills.booking.lookup import is_personal_booking_lookup
        from apps.skills.menu.matching import MENU_CALLBACK_TEXT

        assert is_personal_booking_lookup(MENU_CALLBACK_TEXT[CALLBACK_MENU_MY_BOOKINGS])
        # Paired negative on the same predicate: it is not a rubber stamp.
        assert not is_personal_booking_lookup("расскажи анекдот")
