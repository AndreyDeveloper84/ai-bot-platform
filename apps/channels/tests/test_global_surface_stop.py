"""The «Не присылать» button on the LIVE global path (DRF-1468).

The pilot runs the global bot, so the tap must be answered there: the
surface pref flips, the platform-wide veto stays UNSET (the button
silences one surface, not the person), and the raw ``cb:`` payload never
lands in dialog history (the DRF-988 defect class).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from apps.channels.max import handler as max_handler
from apps.conversations.models import Message
from apps.identity.models import BotUser
from apps.nutrition_proactive import prefs
from apps.nutrition_proactive.optout import SURFACE_CONFIRMATIONS
from apps.orchestrator.memory import short_term

pytestmark = pytest.mark.django_db


def _payload(*, text, user_id=7777, chat_id=8888, mid="m-1"):
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
def bot_user(db) -> BotUser:
    """A subscribed person the tap has something to switch off.

    Global-path BotUsers are parked under the sentinel tenant
    (``resolve_or_create_global_bot_user``), so the fixture must be too --
    a row under a regular tenant is a different person on this path.
    """
    from apps.identity.services.global_tenant import get_global_bot_tenant

    return BotUser.all_tenants.create(
        tenant=get_global_bot_tenant(),
        channel="max",
        channel_user_id="7777",
        chat_id="8888",
        context={prefs.CONTEXT_KEY: {"water_reminders": True, "daily_report_time": "19:00"}},
    )


class TestGlobalSurfaceStop:
    def test_the_water_tap_silences_only_water(
        self, mock_send, fake_redis, spy_concierge, bot_user
    ):
        max_handler.handle_global_max_event(
            _payload(text="cb:nutri:stop:water"), trace_id=str(uuid.uuid4())
        )

        assert mock_send[0]["text"] == SURFACE_CONFIRMATIONS["water"]
        spy_concierge.assert_not_called()

        stored_user = BotUser.all_tenants.get(pk=bot_user.pk)
        stored = prefs.get_prefs(stored_user)
        assert stored["water_reminders"] is False
        assert stored["daily_report_time"] == "19:00"
        # One surface, not the person: the global veto stays unset.
        assert stored_user.proactive_messages_opt_out is False

    def test_the_raw_payload_never_reaches_history(
        self, mock_send, fake_redis, spy_concierge, bot_user
    ):
        max_handler.handle_global_max_event(
            _payload(text="cb:nutri:stop:report"), trace_id=str(uuid.uuid4())
        )

        msgs = list(Message.all_tenants.order_by("created_at"))
        # Presence: the turn IS in history -- as the bot's confirmation.
        assert len(msgs) >= 1
        assert all("cb:nutri" not in m.content for m in msgs)
        assert all(m.role != "user" for m in msgs)

    def test_a_stale_surface_gets_an_honest_answer(
        self, mock_send, fake_redis, spy_concierge, bot_user
    ):
        max_handler.handle_global_max_event(
            _payload(text="cb:nutri:stop:hint"), trace_id=str(uuid.uuid4())
        )

        assert mock_send[0]["text"]
        spy_concierge.assert_not_called()
        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=bot_user.pk))
        assert stored["water_reminders"] is True
        assert stored["daily_report_time"] == "19:00"
