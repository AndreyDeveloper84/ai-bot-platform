"""DRF-2213 Q2 — red flag классификатора отвечает в ОДНОЙ точке, сразу после гейта.

Замер на ``fix/drf2000-medical-escalation`` (22a9e029), 21.09: детерминированное
замыкание red flag G1–G7 (DRF-2000) стояло внутри консьержа, а ветки
обработчика MAX, решающие раньше консьержа, отвечали на red flag своим текстом:
онбординг нового человека — приветствием («Привет! Я Ayla 👋» вместо 103 / 112).
Продолжение записи и ответ на вопрос памяти забирают свободный текст так же.

Теперь проверка стоит в обработчике сразу после ``evaluate_inbound``
(:mod:`apps.orchestrator.red_flag_turn`), замыкание в консьерже снято.
"""

from __future__ import annotations

import uuid

import pytest

from apps.channels.max import handler as max_handler
from apps.conversations.models import Conversation, Message
from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.identity.models import BotUser
from apps.orchestrator.memory import short_term
from apps.orchestrator.safety.medical_emergency import MEDICAL_EMERGENCY_TEXT_V2

pytestmark = pytest.mark.django_db

RED_FLAG = "онемела половина лица"


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture(autouse=True)
def _harness(monkeypatch):
    import apps.channels.max.outbound as outbound
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    monkeypatch.setattr(outbound, "send_chat_action", lambda **kw: None)
    monkeypatch.setattr(short_term, "_redis_client", lambda: _FakeRedis())


@pytest.fixture
def spy_concierge(monkeypatch):
    from unittest.mock import MagicMock

    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Какая услуга интересует?"))
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


def _run_global(text: str, *, user_id: int, mid: str) -> None:
    max_handler.handle_global_max_event(
        {
            "update_type": "message_created",
            "timestamp": 1731320000000,
            "message": {
                "sender": {"user_id": user_id, "name": "Иван"},
                "recipient": {"chat_id": user_id, "chat_type": "dialog"},
                "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
            },
        },
        trace_id=str(uuid.uuid4()),
    )


def _global_conversation(user_id: int) -> Conversation:
    bot_user = BotUser.all_tenants.get(
        channel="max", channel_user_id=str(user_id), tenant__slug=GLOBAL_BOT_TENANT_SLUG
    )
    return Conversation.all_tenants.get(bot_user=bot_user, tenant__slug=GLOBAL_BOT_TENANT_SLUG)


class TestRedFlagAnswersRightAfterTheGate:
    def test_first_message_red_flag_beats_onboarding(self, sent, spy_concierge, settings) -> None:
        from apps.skills.health_screening.classifier import PainSignal, classify

        settings.GLOBAL_BOT_ONBOARDING = True
        assert classify(RED_FLAG) == PainSignal.RED_FLAG  # положительно: red flag классификатора

        _run_global(RED_FLAG, user_id=4405, mid="a")

        assert sent, "ничего не отправлено"
        assert sent[0]["text"] == MEDICAL_EMERGENCY_TEXT_V2
        spy_concierge.assert_not_called()

    @pytest.mark.parametrize("claimant", ["try_continue_booking", "try_handle_answer"])
    def test_a_claiming_branch_does_not_eat_the_red_flag(
        self, sent, spy_concierge, monkeypatch, claimant
    ) -> None:
        """Продолжение записи / ответ на вопрос памяти забирают свободный текст
        раньше консьержа. Моделируем ветку, которая ЗАБИРАЕТ ход, — red flag
        всё равно получает 103 / 112."""
        from apps.orchestrator.discovery import DiscoveryReply

        _run_global("привет", user_id=4407, mid="a")
        claimed = DiscoveryReply(text="Какое время удобно?")
        monkeypatch.setattr(max_handler, claimant, lambda *a, **kw: claimed)
        _run_global("какое время?", user_id=4407, mid="b")
        assert sent[-1]["text"] == "Какое время удобно?"  # положительно: ветка забирает ход

        _run_global(RED_FLAG, user_id=4407, mid="c")

        assert sent[-1]["text"] == MEDICAL_EMERGENCY_TEXT_V2

    def test_the_red_flag_turn_is_recorded(self, sent, spy_concierge) -> None:
        """Ход пишется в Message: навык не пишет сам, пишет обработчик
        (``persisted=False``) — стенограмма держит то, что человек прочёл."""
        _run_global(RED_FLAG, user_id=4408, mid="a")

        last = Message.all_tenants.filter(
            conversation=_global_conversation(4408), role="assistant"
        ).latest("created_at")
        assert last.content == MEDICAL_EMERGENCY_TEXT_V2
        assert last.action_type == "health_screening"

    def test_a_plain_turn_still_reaches_the_concierge(self, sent, spy_concierge) -> None:
        """Положительная пара: без red flag ход идёт по лестнице, как раньше."""
        _run_global("хочу на массаж", user_id=4409, mid="a")
        assert MEDICAL_EMERGENCY_TEXT_V2 not in [call["text"] for call in sent]
        assert sent  # положительно: ответ ушёл
