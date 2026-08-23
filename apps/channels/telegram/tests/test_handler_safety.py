"""Telegram inbound safety gate (DRF-1300).

Telegram shipped after the MAX safety gate (#1053) and never called it, so a
crisis phrase typed into the Telegram bot was answered by the ordinary brain.
These tests pin the fix on the LIVE handler entrypoint
(``apps.channels.telegram.handler.handle_inbound``), through the real outbound
module (only ``requests.post`` is stubbed) — so a regression that removes the
gate, and one that keeps the gate but breaks delivery, both fail here.

What is deliberately asserted:

* the exact founder-approved texts are IMPORTED from
  :mod:`apps.orchestrator.safety.gate`, never restated — a test that hard-coded
  the copy would happily pass on a bot that silently reworded the helpline;
* the brain never runs on a gated turn (``apps.skills.registry.dispatch``);
* the assistant turn carries ``action_type="safety_pre_check"``, the same
  marker both MAX paths write, so one analytics query covers all surfaces;
* the emitted observability event is PII-safe — verdict + match COUNT only;
* the HUMAN_HANDOFF barge-guard: no canned reply over a live operator;
* a failed crisis delivery is LOUD (its own event) and still does not raise.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.channels.telegram import handler as tg_handler
from apps.channels.telegram import outbound
from apps.conversations.models import Conversation, Message
from apps.events.models import Event
from apps.orchestrator.memory import short_term
from apps.orchestrator.safety.gate import (
    BLOCK_REPLY_TEXT,
    CRISIS_HOTLINE,
    CRISIS_REPLY_TEXT,
)
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

CRISIS_TEXT = "я думаю о суициде"
BLOCK_TEXT = "посоветуйте ибупрофен от боли"


@pytest.fixture
def tenant():
    return Tenant.objects.create(
        slug="tg-safety",
        name="TG Safety",
        telegram_bot_token="bot-token-tg",  # pragma: allowlist secret
        telegram_webhook_secret="secret-tg",  # pragma: allowlist secret
    )


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture
def mock_post():
    with patch.object(
        outbound.requests,
        "post",
        return_value=SimpleNamespace(ok=True, status_code=200, text='{"ok":true}'),
    ) as m:
        yield m


def _payload(text: str, *, user_id: int = 4242, chat_id: int = 4242, message_id: int = 1) -> dict:
    return {
        "update_id": 500 + message_id,
        "message": {
            "message_id": message_id,
            "date": 1731320000,
            "from": {"id": user_id, "is_bot": False, "first_name": "Иван"},
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


def _sent_texts(mock_post) -> list[str]:
    """Every text Telegram was actually asked to deliver, in order."""
    return [
        call.kwargs["json"]["text"]
        for call in mock_post.call_args_list
        if "sendMessage" in call.args[0]
    ]


class TestCrisisShortCircuit:
    def test_crisis_phrase_gets_the_founder_approved_reply(self, tenant, fake_redis, mock_post):
        with tenant_scope(tenant):
            tg_handler.handle_inbound(_payload(CRISIS_TEXT), tenant=tenant)

        assert _sent_texts(mock_post) == [CRISIS_REPLY_TEXT]

    def test_crisis_reply_actually_carries_the_helpline(self, tenant, fake_redis, mock_post):
        # The point of the ticket, stated as a behaviour rather than as string
        # equality: the person reads a phone number they can call.
        with tenant_scope(tenant):
            tg_handler.handle_inbound(_payload(CRISIS_TEXT), tenant=tenant)

        assert CRISIS_HOTLINE in _sent_texts(mock_post)[0]

    def test_block_phrase_gets_the_block_reply(self, tenant, fake_redis, mock_post):
        with tenant_scope(tenant):
            tg_handler.handle_inbound(_payload(BLOCK_TEXT), tenant=tenant)

        assert _sent_texts(mock_post) == [BLOCK_REPLY_TEXT]

    def test_brain_never_runs_on_a_gated_turn(self, tenant, fake_redis, mock_post):
        # A gated turn must not reach the skill registry at all — not merely be
        # overwritten afterwards. An LLM skill that runs anyway costs money and,
        # worse, can persist its own answer alongside the crisis reply.
        with patch("apps.skills.registry.dispatch") as spy:
            with tenant_scope(tenant):
                tg_handler.handle_inbound(_payload(CRISIS_TEXT), tenant=tenant)

        spy.assert_not_called()

    def test_gated_turn_is_tagged_for_analytics(self, tenant, fake_redis, mock_post):
        with tenant_scope(tenant):
            tg_handler.handle_inbound(_payload(CRISIS_TEXT), tenant=tenant)

        last = Message.all_tenants.filter(role="assistant").order_by("-created_at", "-id").first()
        assert last is not None
        assert last.action_type == "safety_pre_check"
        assert last.content == CRISIS_REPLY_TEXT

    def test_inbound_crisis_message_is_still_recorded(self, tenant, fake_redis, mock_post):
        # The short-circuit happens AFTER the user turn is persisted: an operator
        # reviewing the conversation must see what the person actually wrote.
        with tenant_scope(tenant):
            tg_handler.handle_inbound(_payload(CRISIS_TEXT), tenant=tenant)

        user_msgs = Message.all_tenants.filter(role="user")
        assert [m.content for m in user_msgs] == [CRISIS_TEXT]


class TestObservabilityIsPIISafe:
    def test_event_carries_verdict_and_count_but_no_text(self, tenant, fake_redis, mock_post):
        with tenant_scope(tenant):
            tg_handler.handle_inbound(_payload(CRISIS_TEXT), tenant=tenant)

        ev = Event.objects.filter(event_type="channels.telegram.safety.pre_check_triggered").first()
        assert ev is not None
        assert ev.payload["verdict"] == "handoff"
        assert ev.payload["matched_count"] >= 1
        assert ev.payload["is_global_bot"] is False
        # The phrase itself must never reach the analytics bus.
        serialized = str(ev.payload)
        assert CRISIS_TEXT not in serialized
        assert "суицид" not in serialized


class TestHumanHandoffBargeGuard:
    def test_gate_stays_silent_while_an_operator_is_driving(self, tenant, fake_redis, mock_post):
        # Same rule as the per-tenant MAX gate: a canned reply barged over a live
        # human is the worst possible moment for an auto-response.
        with tenant_scope(tenant):
            tg_handler.handle_inbound(_payload("привет", message_id=1), tenant=tenant)
        conv = Conversation.all_tenants.get()
        Conversation.all_tenants.filter(pk=conv.pk).update(state=Conversation.State.HUMAN_HANDOFF)
        mock_post.reset_mock()

        with tenant_scope(tenant):
            tg_handler.handle_inbound(_payload(CRISIS_TEXT, message_id=2), tenant=tenant)

        assert CRISIS_REPLY_TEXT not in _sent_texts(mock_post)


class TestHappyPathUnchanged:
    @pytest.mark.parametrize(
        "text",
        [
            "хочу записаться на массаж",
            "сколько стоит стрижка",
            # CLARIFY verdict — broad on a beauty marketplace, must NOT block.
            "почему болит спина после массажа",
        ],
    )
    def test_ordinary_phrase_reaches_the_brain(self, text, tenant, fake_redis, mock_post):
        from apps.skills.base import SkillResult

        with patch("apps.skills.registry.dispatch", return_value=SkillResult(reply_text="ok")):
            with tenant_scope(tenant):
                tg_handler.handle_inbound(_payload(text), tenant=tenant)

        assert _sent_texts(mock_post) == ["ok"]


class TestCrisisDeliveryFailureIsLoud:
    def test_failed_crisis_send_emits_its_own_alert_and_does_not_raise(self, tenant, fake_redis):
        # Telegram's outbound returns False instead of raising, so a silently
        # undelivered crisis reply would otherwise look identical to a delivered
        # one in the DB. It gets an event of its own, distinct from the routine
        # channels.telegram.outbound.failed.
        with patch.object(
            outbound.requests,
            "post",
            return_value=SimpleNamespace(ok=False, status_code=500, text="srv err"),
        ):
            with tenant_scope(tenant):
                tg_handler.handle_inbound(_payload(CRISIS_TEXT), tenant=tenant)

        assert Event.objects.filter(
            event_type="channels.telegram.safety.crisis_delivery_failed"
        ).exists()
