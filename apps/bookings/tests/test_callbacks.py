"""Callback skill tests (DRF-844 / Phase 1 / R1).

Covers each action's happy path + idempotency (replay after handled)
+ wrong-user authorisation rejection + matches() coverage.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from apps.booking.models import BookingReminder
from apps.bookings.callbacks import (
    REPLY_ALREADY_HANDLED,
    REPLY_CANCELLED,
    REPLY_CONFIRMED,
    REPLY_FORBIDDEN,
    REPLY_NOT_FOUND,
    REPLY_RESCHEDULE,
    BookingReminderCallbackSkill,
)
from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.skills.base import SkillContext
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="rem-cb", name="Salon CB")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-cb",
        chat_id="chat-cb",
        phone="79991234567",
        client_name="Anna",
    )


@pytest.fixture
def other_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-other",
        chat_id="chat-other",
        phone="79990000000",
        client_name="Boris",
    )


@pytest.fixture
def conversation(tenant: Tenant, bot_user: BotUser) -> Conversation:
    with tenant_scope(tenant):
        return Conversation.objects.create(
            tenant=tenant,
            bot_user=bot_user,
            state=Conversation.State.IDLE,
        )


def _reminder(
    tenant: Tenant,
    bot_user: BotUser,
    *,
    status: str = BookingReminder.Status.SENT_NO_REPLY,
    kind: str = BookingReminder.Kind.DAY_BEFORE,
    yc_id: str = "yc-cb-1",
) -> BookingReminder:
    visit_at = timezone.now() + timedelta(hours=24)
    return BookingReminder.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        yclients_record_id=yc_id,
        chat_id=bot_user.chat_id,
        visit_at=visit_at,
        kind=kind,
        status=status,
        scheduled_at=visit_at - timedelta(hours=24),
        master_name="Lera",
        service_name="Массаж",
    )


def _ctx(text: str, *, bot_user: BotUser, conversation: Conversation) -> SkillContext:
    return SkillContext(
        conversation=conversation,
        bot_user=bot_user,
        message_text=text,
        has_attachments=False,
    )


class TestMatches:
    def test_matches_confirm_callback(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        ctx = _ctx("cb:rem:confirm:abc", bot_user=bot_user, conversation=conversation)
        assert BookingReminderCallbackSkill().matches(ctx)

    def test_matches_cancel_callback(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        ctx = _ctx("cb:rem:cancel:abc", bot_user=bot_user, conversation=conversation)
        assert BookingReminderCallbackSkill().matches(ctx)

    def test_does_not_match_plain_text(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        ctx = _ctx("Hello!", bot_user=bot_user, conversation=conversation)
        assert not BookingReminderCallbackSkill().matches(ctx)

    def test_does_not_match_other_cb_namespaces(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        ctx = _ctx("cb:food:to_diary:scan-1", bot_user=bot_user, conversation=conversation)
        assert not BookingReminderCallbackSkill().matches(ctx)


class TestConfirm:
    def test_happy_path(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        reminder = _reminder(tenant, bot_user)
        ctx = _ctx(
            f"cb:rem:confirm:{reminder.pk}",
            bot_user=bot_user,
            conversation=conversation,
        )
        result = BookingReminderCallbackSkill().handle(ctx)
        assert result.reply_text == REPLY_CONFIRMED
        reminder.refresh_from_db()
        assert reminder.status == BookingReminder.Status.CONFIRMED
        assert reminder.replied_at is not None

    def test_replay_after_confirmed_is_polite(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        reminder = _reminder(
            tenant,
            bot_user,
            status=BookingReminder.Status.CONFIRMED,
        )
        ctx = _ctx(
            f"cb:rem:confirm:{reminder.pk}",
            bot_user=bot_user,
            conversation=conversation,
        )
        result = BookingReminderCallbackSkill().handle(ctx)
        assert result.reply_text == REPLY_ALREADY_HANDLED


class TestCancel:
    def test_happy_path(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        reminder = _reminder(tenant, bot_user, yc_id="123456")
        ctx = _ctx(
            f"cb:rem:cancel:{reminder.pk}",
            bot_user=bot_user,
            conversation=conversation,
        )
        # Stub the upstream cancel — we test the path independently.
        with patch("apps.bookings.callbacks._try_yclients_cancel", return_value=True):
            result = BookingReminderCallbackSkill().handle(ctx)
        assert result.reply_text == REPLY_CANCELLED
        reminder.refresh_from_db()
        assert reminder.status == BookingReminder.Status.CANCELLED
        assert reminder.replied_at is not None

    def test_upstream_failure_does_not_block_local_cancel(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        reminder = _reminder(tenant, bot_user, yc_id="123456")
        ctx = _ctx(
            f"cb:rem:cancel:{reminder.pk}",
            bot_user=bot_user,
            conversation=conversation,
        )
        with patch("apps.bookings.callbacks._try_yclients_cancel", return_value=False):
            result = BookingReminderCallbackSkill().handle(ctx)
        # Still cancelled locally + still tells the user it's cancelled.
        assert result.reply_text == REPLY_CANCELLED
        reminder.refresh_from_db()
        assert reminder.status == BookingReminder.Status.CANCELLED

    def test_replay_after_cancelled(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        reminder = _reminder(tenant, bot_user, status=BookingReminder.Status.CANCELLED)
        ctx = _ctx(
            f"cb:rem:cancel:{reminder.pk}",
            bot_user=bot_user,
            conversation=conversation,
        )
        result = BookingReminderCallbackSkill().handle(ctx)
        assert result.reply_text == REPLY_ALREADY_HANDLED


class TestReschedule:
    def test_happy_path(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        reminder = _reminder(tenant, bot_user)
        ctx = _ctx(
            f"cb:rem:reschedule:{reminder.pk}",
            bot_user=bot_user,
            conversation=conversation,
        )
        result = BookingReminderCallbackSkill().handle(ctx)
        assert result.reply_text == REPLY_RESCHEDULE
        reminder.refresh_from_db()
        assert reminder.status == BookingReminder.Status.RESCHEDULE_REQUESTED
        assert reminder.replied_at is not None


class TestAuthorisation:
    def test_other_user_cannot_act_on_someone_elses_reminder(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        other_user: BotUser,
        conversation: Conversation,
    ) -> None:
        reminder = _reminder(tenant, bot_user)  # Owned by bot_user.
        # other_user tries to cancel.
        ctx = _ctx(
            f"cb:rem:cancel:{reminder.pk}",
            bot_user=other_user,
            conversation=conversation,
        )
        result = BookingReminderCallbackSkill().handle(ctx)
        assert result.reply_text == REPLY_FORBIDDEN
        reminder.refresh_from_db()
        # Status unchanged.
        assert reminder.status == BookingReminder.Status.SENT_NO_REPLY


class TestNotFound:
    def test_unknown_reminder_pk_returns_polite_not_found(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        # Random UUID — well-formed but no row.
        ctx = _ctx(
            "cb:rem:confirm:00000000-0000-0000-0000-000000000000",
            bot_user=bot_user,
            conversation=conversation,
        )
        result = BookingReminderCallbackSkill().handle(ctx)
        assert result.reply_text == REPLY_NOT_FOUND

    def test_malformed_pk_returns_not_found(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        ctx = _ctx(
            "cb:rem:confirm:not-a-uuid",
            bot_user=bot_user,
            conversation=conversation,
        )
        result = BookingReminderCallbackSkill().handle(ctx)
        assert result.reply_text == REPLY_NOT_FOUND


class TestUpstreamCancelHelper:
    def test_calls_cancel_record_with_int(self) -> None:
        from apps.bookings.callbacks import _try_yclients_cancel

        mock_client = MagicMock()
        with patch(
            "apps.integrations.yclients.get_yclients_client",
            return_value=mock_client,
        ):
            ok = _try_yclients_cancel("123456")
        assert ok is True
        mock_client.cancel_record.assert_called_once_with(record_id=123456)

    def test_swallows_exceptions(self) -> None:
        from apps.bookings.callbacks import _try_yclients_cancel

        mock_client = MagicMock()
        mock_client.cancel_record.side_effect = RuntimeError("upstream 503")
        with patch(
            "apps.integrations.yclients.get_yclients_client",
            return_value=mock_client,
        ):
            ok = _try_yclients_cancel("123")
        assert ok is False

    def test_non_numeric_yc_id_returns_false(self) -> None:
        from apps.bookings.callbacks import _try_yclients_cancel

        mock_client = MagicMock()
        with patch(
            "apps.integrations.yclients.get_yclients_client",
            return_value=mock_client,
        ):
            ok = _try_yclients_cancel("not-an-int")
        assert ok is False
