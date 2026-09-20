"""Час отчёта из чата на ЖИВОМ глобальном пути (DRF-2141).

Пилот — глобальный бот, поэтому ступень стоит в его лестнице, рядом с
отпиской: «присылай итоги в 21:00» отвечается детерминированно (модель не
зовётся), «не присылай отчёт» гасит один отчёт и оставляет воду, а при
выключенных флагах ход уходит туда же, куда и у соседей — к консьержу.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from apps.channels.max import handler as max_handler
from apps.conversations.models import Message
from apps.identity.models import BotUser
from apps.nutrition_proactive import prefs, report_hour
from apps.nutrition_proactive.optout import SURFACE_CONFIRMATIONS
from apps.orchestrator.memory import short_term

pytestmark = pytest.mark.django_db


def _payload(*, text, user_id=7771, chat_id=8881, mid="m-1"):
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Иван"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


@pytest.fixture
def mock_send(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture
def spy_concierge(monkeypatch):
    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Какая услуга интересует?"))
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


@pytest.fixture
def flags_on(settings):
    settings.NUTRITION_ENABLED = True
    settings.NUTRITION_PROACTIVE_ENABLED = True
    return settings


@pytest.fixture
def bot_user(db) -> BotUser:
    """Подписан на воду и отчёт в 19:00, под глобальным тенантом —
    иначе на этом пути это другой человек."""
    from apps.identity.services.global_tenant import get_global_bot_tenant

    return BotUser.all_tenants.create(
        tenant=get_global_bot_tenant(),
        channel="max",
        channel_user_id="7771",
        chat_id="8881",
        context={prefs.CONTEXT_KEY: {"water_reminders": True, "daily_report_time": "19:00"}},
    )


class TestGlobalReportHour:
    def test_set_is_answered_without_the_model(
        self, mock_send, fake_redis, spy_concierge, flags_on, bot_user
    ):
        max_handler.handle_global_max_event(
            _payload(text="присылай итоги в 21:00"), trace_id=str(uuid.uuid4())
        )

        assert mock_send[0]["text"] == report_hour.SET_CONFIRMATION.format(time="21:00")
        spy_concierge.assert_not_called()
        stored_user = BotUser.all_tenants.get(pk=bot_user.pk)
        stored = prefs.get_prefs(stored_user)
        assert stored["daily_report_time"] == "21:00"
        assert stored["water_reminders"] is True
        assert stored_user.proactive_messages_opt_out is False

    def test_off_keeps_water(self, mock_send, fake_redis, spy_concierge, flags_on, bot_user):
        max_handler.handle_global_max_event(
            _payload(text="не присылай отчёт"), trace_id=str(uuid.uuid4())
        )

        assert mock_send[0]["text"] == SURFACE_CONFIRMATIONS["report"]
        spy_concierge.assert_not_called()
        stored_user = BotUser.all_tenants.get(pk=bot_user.pk)
        stored = prefs.get_prefs(stored_user)
        assert stored["daily_report_time"] == prefs.REPORT_OFF
        assert stored["water_reminders"] is True
        assert stored_user.proactive_messages_opt_out is False

    def test_ask_reads_the_hour(self, mock_send, fake_redis, spy_concierge, flags_on, bot_user):
        max_handler.handle_global_max_event(
            _payload(text="во сколько ты присылаешь итоги?"), trace_id=str(uuid.uuid4())
        )

        assert mock_send[0]["text"] == report_hour.ASK_REPLY.format(time="19:00")
        spy_concierge.assert_not_called()

    def test_the_turn_is_in_history_with_its_action_type(
        self, mock_send, fake_redis, spy_concierge, flags_on, bot_user
    ):
        max_handler.handle_global_max_event(
            _payload(text="присылай итоги в 21:00"), trace_id=str(uuid.uuid4())
        )

        msgs = list(Message.all_tenants.order_by("created_at"))
        assert len(msgs) >= 1
        assistant = [m for m in msgs if m.role != "user"]
        assert assistant
        assert assistant[-1].action_type == report_hour.ACTION_TYPE

    def test_a_bare_time_goes_to_the_model(
        self, mock_send, fake_redis, spy_concierge, flags_on, bot_user
    ):
        """«21:00» без глагола — не команда: ход идёт своим путём."""
        max_handler.handle_global_max_event(_payload(text="21:00"), trace_id=str(uuid.uuid4()))

        spy_concierge.assert_called_once()
        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=bot_user.pk))
        assert stored["daily_report_time"] == "19:00"

    def test_with_flags_off_the_turn_goes_where_the_neighbours_send_it(
        self, mock_send, fake_redis, spy_concierge, settings, bot_user
    ):
        settings.NUTRITION_ENABLED = False
        settings.NUTRITION_PROACTIVE_ENABLED = False

        max_handler.handle_global_max_event(
            _payload(text="присылай итоги в 21:00"), trace_id=str(uuid.uuid4())
        )

        spy_concierge.assert_called_once()
        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=bot_user.pk))
        assert stored["daily_report_time"] == "19:00"
