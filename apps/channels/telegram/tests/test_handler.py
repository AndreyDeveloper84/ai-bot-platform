"""Telegram handler integration tests (Phase 1 / CH1, DRF-848).

Behaviours 17, 18, 20:
  17. handle_inbound creates BotUser when missing (channel=telegram, tenant set)
  18. handle_inbound reuses BotUser on second message (no duplicate)
  20. answerCallbackQuery called after callback taps

Mocks ``outbound.requests.post`` so no live HTTP. Uses the real DB for
BotUser / Conversation / Message persistence.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.channels.telegram import handler as tg_handler
from apps.channels.telegram import outbound
from apps.conversations.models import Conversation, Message
from apps.identity.models import BotUser
from apps.orchestrator.memory import short_term
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant():
    return Tenant.objects.create(
        slug="tg-handler",
        name="TG Handler",
        telegram_bot_token="bot-token-tg",  # pragma: allowlist secret
        telegram_webhook_secret="secret-tg",  # pragma: allowlist secret
    )


@pytest.fixture
def fake_redis(monkeypatch):
    """Reuse C1's _FakeRedis to keep short_term.append working without Redis."""
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture
def mock_post():
    """Stub requests.post → 200. All outbound calls recorded for assertions."""
    with patch.object(
        outbound.requests,
        "post",
        return_value=SimpleNamespace(ok=True, status_code=200, text='{"ok":true}'),
    ) as m:
        yield m


def _text_payload(text="привет", user_id=12345, chat_id=12345, message_id=1):
    return {
        "update_id": 100 + message_id,
        "message": {
            "message_id": message_id,
            "date": 1731320000,
            "from": {"id": user_id, "is_bot": False, "first_name": "Иван"},
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


def _callback_payload(data="cb:rem:confirm:abc", user_id=12345, chat_id=12345):
    return {
        "update_id": 1000,
        "callback_query": {
            "id": "cqid-xyz",
            "from": {"id": user_id, "is_bot": False, "first_name": "Иван"},
            "message": {
                "message_id": 7,
                "date": 1731320000,
                "from": {"id": 555, "is_bot": True, "first_name": "Bot"},
                "chat": {"id": chat_id, "type": "private"},
            },
            "data": data,
            "chat_instance": "ci-1",
        },
    }


class TestBotUserCreation:
    """Behaviour 17 — first inbound creates BotUser with channel=telegram."""

    def test_creates_bot_user_with_telegram_channel(self, tenant, fake_redis, mock_post):
        with tenant_scope(tenant):
            tg_handler.handle_inbound(_text_payload(user_id=777, chat_id=777), tenant=tenant)

        rows = BotUser.all_tenants.filter(channel="telegram", channel_user_id="777")
        assert rows.count() == 1
        bu = rows.first()
        assert bu.tenant_id == tenant.id
        assert bu.chat_id == "777"


class TestBotUserReuse:
    """Behaviour 18 — second message from same channel_user_id reuses BotUser."""

    def test_second_message_no_duplicate_row(self, tenant, fake_redis, mock_post):
        with tenant_scope(tenant):
            tg_handler.handle_inbound(_text_payload(user_id=42, message_id=1), tenant=tenant)
            tg_handler.handle_inbound(_text_payload(user_id=42, message_id=2), tenant=tenant)

        assert BotUser.all_tenants.filter(channel="telegram").count() == 1
        # Single conversation, two user + two assistant messages.
        assert Conversation.all_tenants.count() == 1
        # echo skill replies to each turn → 2*2=4 messages.
        msgs = Message.all_tenants.all()
        roles = [m.role for m in msgs.order_by("created_at")]
        assert roles.count("user") == 2
        assert roles.count("assistant") == 2


class TestCallbackQueryAnswer:
    """Behaviour 20 — every callback tap triggers answerCallbackQuery."""

    def test_callback_answered_after_handling(self, tenant, fake_redis, mock_post):
        with tenant_scope(tenant):
            tg_handler.handle_inbound(_callback_payload(), tenant=tenant)

        # Two HTTP calls: sendMessage (reply) + answerCallbackQuery.
        called_methods = [call.args[0] for call in mock_post.call_args_list]
        assert any("sendMessage" in u for u in called_methods)
        assert any("answerCallbackQuery" in u for u in called_methods), (
            f"answerCallbackQuery missing — actual calls: {called_methods}"
        )

    def test_text_message_does_not_answer_callback(self, tenant, fake_redis, mock_post):
        """Plain text messages have no callback_query → no answerCallbackQuery."""
        with tenant_scope(tenant):
            tg_handler.handle_inbound(_text_payload(), tenant=tenant)

        called_methods = [call.args[0] for call in mock_post.call_args_list]
        assert not any("answerCallbackQuery" in u for u in called_methods)


class TestIgnoredUpdates:
    """Parser returns None → handler exits without DB writes / outbound."""

    def test_channel_post_no_side_effects(self, tenant, fake_redis, mock_post):
        with tenant_scope(tenant):
            tg_handler.handle_inbound(
                {"update_id": 1, "channel_post": {"message_id": 1, "chat": {"id": 1}}},
                tenant=tenant,
            )
        assert BotUser.all_tenants.count() == 0
        assert not mock_post.called

    def test_edited_message_no_side_effects(self, tenant, fake_redis, mock_post):
        with tenant_scope(tenant):
            tg_handler.handle_inbound(
                {"update_id": 1, "edited_message": {"message_id": 1, "chat": {"id": 1}}},
                tenant=tenant,
            )
        assert Conversation.all_tenants.count() == 0


class TestKeyboardConversion:
    """When the skill returns a keyboard, outbound carries reply_markup."""

    def test_skill_keyboard_lands_in_reply_markup(self, tenant, fake_redis, mock_post):
        """If a skill returns action_data with an inline_keyboard, the
        handler converts and threads it into send_message."""
        from apps.skills.base import SkillResult

        fake_result = SkillResult(
            reply_text="нажмите кнопку",
            action_data={
                "attachments": [
                    {
                        "type": "inline_keyboard",
                        "payload": {"buttons": [{"label": "✅", "callback": "cb:rem:confirm:abc"}]},
                    }
                ]
            },
        )

        with patch("apps.skills.registry.dispatch", return_value=fake_result):
            with tenant_scope(tenant):
                tg_handler.handle_inbound(_text_payload(), tenant=tenant)

        send_message_calls = [
            call for call in mock_post.call_args_list if "sendMessage" in call.args[0]
        ]
        assert len(send_message_calls) == 1
        body = send_message_calls[0].kwargs["json"]
        assert "reply_markup" in body
        assert body["reply_markup"] == {
            "inline_keyboard": [[{"text": "✅", "callback_data": "cb:rem:confirm:abc"}]]
        }


class TestOutboundFailureNotRaised:
    """Outbound returns False on failure — handler MUST NOT raise."""

    def test_500_response_does_not_raise(self, tenant, fake_redis):
        with patch.object(
            outbound.requests,
            "post",
            return_value=SimpleNamespace(ok=False, status_code=500, text="srv err"),
        ):
            with tenant_scope(tenant):
                # Should NOT raise — handler returns normally.
                tg_handler.handle_inbound(_text_payload(), tenant=tenant)
        # Assistant message was still persisted (pre-send write contract).
        assert Message.all_tenants.filter(role="assistant").count() == 1
