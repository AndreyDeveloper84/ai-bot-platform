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
    REPLY_BOOK_EXPIRED_UNCHANGED,
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


def _menu_callbacks(action_data) -> list[str]:
    attachments = (action_data or {}).get("attachments") or []
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

    def test_abandoned_reschedule_preview_says_what_it_actually_did(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        """The third ``Kind``. Same branch as CANCEL, and the reason it must
        be: the reschedule payload DOES carry master + service, so a
        kind-blind chip would have offered «выбрать другое время» — a second
        booking beside the live one it failed to move."""
        token = create_pending(
            tenant=tenant,
            bot_user=bot_user,
            kind=PendingBookingAction.Kind.RESCHEDULE,
            payload={
                "record_id": 555,
                "new_datetime": _future_iso(),
                "master_id": 11,
                "service_id": 22,
            },
        )
        client = _FakeYClients()
        with _patched(client):
            result = BookingGateCallbackSkill().handle(
                _ctx(f"cb:book:cancel:{token}", bot_user=bot_user, conversation=conversation)
            )

        assert result.reply_text == REPLY_BOOK_KEPT_PREVIEW
        assert _callbacks(result) == [CALLBACK_MENU_MY_BOOKINGS]
        assert client.cancel_calls == []

    def test_expired_cancel_preview_does_not_offer_to_book(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        """A CANCEL preview that timed out. The reply used to say «давайте
        подберём слот заново» and — after this ticket's first pass — carried
        📅 Записаться: one tap to CREATE a booking, offered to somebody who
        was trying to remove one."""
        token = create_pending(
            tenant=tenant,
            bot_user=bot_user,
            kind=PendingBookingAction.Kind.CANCEL,
            payload={"record_id": 555, "reason": "", "booking_request_id": ""},
        )
        PendingBookingAction.all_tenants.filter(pk=token).update(
            expires_at=timezone.now() - timedelta(seconds=30)
        )
        client = _FakeYClients()
        with _patched(client):
            result = BookingGateCallbackSkill().handle(
                _ctx(f"cb:book:confirm:{token}", bot_user=bot_user, conversation=conversation)
            )

        assert result.reply_text == REPLY_BOOK_EXPIRED_UNCHANGED
        assert "подберём слот" not in result.reply_text
        assert _callbacks(result) == [CALLBACK_MENU_MY_BOOKINGS]
        assert client.cancel_calls == []

    def test_stale_version_opens_the_records_its_text_names(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        """«Откройте актуальные записи» — asserted where the text is, so the
        sentence and the button cannot drift apart."""
        from apps.bookings import callbacks as cb

        assert "актуальные записи" in cb.REPLY_BOOK_STALE_VERSION
        assert _menu_callbacks(cb._my_bookings_keyboard()) == [CALLBACK_MENU_MY_BOOKINGS]

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


class TestChipsLandOnBothSurfaces:
    """The property that made ``cb:menu:*`` the right family.

    Deliberately NOT «the two translation tables agree»: the global one is
    built as ``{**MENU_CALLBACK_TEXT, …}``
    (``apps.channels.max.quick_actions._global_menu_text``), so that equality
    is guaranteed by the unpacking and can never fail. What has to be true is
    that the phrase each chip resolves to LANDS somewhere — a translator that
    yields text nobody claims is a dead button with extra steps.
    """

    def test_both_chips_translate_to_a_phrase_on_the_global_path(self) -> None:
        from apps.channels.max.quick_actions import resolve_tap_text

        for callback in (CALLBACK_MENU_MY_BOOKINGS, CALLBACK_MENU_BOOK):
            assert resolve_tap_text(callback), callback
        # Paired negative on the same resolver: it is not a rubber stamp that
        # would make the loop above pass for any string at all.
        assert resolve_tap_text("cb:menu:no_such_slug") == "Что ты умеешь?"
        assert resolve_tap_text("просто текст") is None

    def test_my_bookings_lands_on_the_bookings_lookup(self) -> None:
        """«Покажи мои записи» is claimed by the SAME predicate both surfaces
        branch on — ``apps/channels/max/handler.py`` for the global bot,
        ``apps/skills/booking/skill.py`` for the tenant's own."""
        from apps.skills.booking.lookup import is_personal_booking_lookup
        from apps.skills.menu.matching import MENU_CALLBACK_TEXT

        assert is_personal_booking_lookup(MENU_CALLBACK_TEXT[CALLBACK_MENU_MY_BOOKINGS])
        assert not is_personal_booking_lookup("расскажи анекдот")

    def test_book_again_lands_on_the_booking_skill(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        """«Хочу записаться» — the phrase behind 📅 Записаться. It had no
        landing check at all, and that chip hangs under two of this module's
        replies.
        """
        from apps.skills.booking.skill import BookingSkill
        from apps.skills.menu.matching import MENU_CALLBACK_TEXT

        phrase = MENU_CALLBACK_TEXT[CALLBACK_MENU_BOOK]
        with tenant_scope(tenant):
            claimed = BookingSkill().matches(
                _ctx(phrase, bot_user=bot_user, conversation=conversation)
            )
            # Paired negative on the same skill and the same fixtures.
            unrelated = BookingSkill().matches(
                _ctx("расскажи анекдот", bot_user=bot_user, conversation=conversation)
            )
        assert claimed, phrase
        # empty-assert-ok: the identical call above returned True on the same fixtures
        assert not unrelated


class TestChipsFitTheWire:
    def test_the_pick_master_chip_is_never_offered_over_telegrams_limit(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        """Telegram raises on a ``callback_data`` over 64 bytes, and nothing in
        the send path catches it — an over-long payload costs the whole
        message, not one button. Under the pilot flag both ids are UUIDs, so
        ``cb:book:pick_master:<uuid>:<uuid>`` is 93 bytes and must not ship.
        """
        import uuid as _uuid

        from apps.bookings import callbacks as cb

        # Presence first: the same builder DOES produce this chip for ids that
        # fit, so an empty result below means «too long», not «never builds».
        short = cb._another_time_keyboard({"master_id": "11", "service_id": "22"})
        assert [
            b["callback"] for b in (short or {}).get("attachments", [])[0]["payload"]["buttons"]
        ] == [f"{CALLBACK_BOOK_PICK_MASTER_PREFIX}11:22"]

        long_ids = cb._another_time_keyboard(
            {"master_id": str(_uuid.uuid4()), "service_id": str(_uuid.uuid4())}
        )
        callbacks = [
            b["callback"]
            for att in (long_ids or {}).get("attachments", [])
            for b in att["payload"]["buttons"]
        ]
        assert callbacks == [CALLBACK_MENU_BOOK]
        assert all(len(c.encode("utf-8")) <= 64 for c in callbacks)


class TestRollbackSwitch:
    def test_menu_chips_disappear_when_the_menu_surface_is_off(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation, settings
    ) -> None:
        """``PILOT_CONVERSATIONAL_UX`` off makes ``MenuSkill`` stand down, so a
        ``cb:menu:*`` chip would be claimed by nobody and the raw payload
        would reach the model — the DRF-1051 defect the rollback exists to
        restore away FROM. The chips have to go with it.
        """
        reminder = _reminder(tenant, bot_user, yc_id="910")

        settings.PILOT_CONVERSATIONAL_UX = True
        on = BookingReminderCallbackSkill().handle(
            _ctx(f"cb:rem:cancel:{reminder.pk}", bot_user=bot_user, conversation=conversation)
        )
        assert _callbacks(on) == [CALLBACK_MENU_BOOK]

        settings.PILOT_CONVERSATIONAL_UX = False
        off = BookingReminderCallbackSkill().handle(
            _ctx(
                f"cb:rem:cancel:{_reminder(tenant, bot_user, yc_id='911').pk}",
                bot_user=bot_user,
                conversation=conversation,
            )
        )
        assert off.reply_text == REPLY_CANCELLED
        assert off.action_data is None
