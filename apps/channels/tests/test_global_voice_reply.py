"""DRF-1939 — голосовое сообщение на глобальном (tenant-less) пути MAX.

Голосовое сообщение приходило как ход без текста с вложением: предикат
``nutrition_global`` («вложение и нет текста → фото») принимал его за фото еды,
картинки не находил, и ход уходил консьержу с пустой строкой. Теперь — честный
детерминированный ответ сразу после safety: без консьержа и LLM, аудио не
скачивается и не хранится.

Форма вложения — по документации MAX Bot API v0.0.33 (``docs/RECON_MAX_VOICE.md``):
``{"type": "audio", "payload": {"url", "token"}, "transcription": str | null}``.
**Не подтверждена живым вебхуком** — фикстура обновится по замеру на пилоте.
"""

from __future__ import annotations

import uuid

import pytest

from apps.channels.max import handler as max_handler
from apps.conversations.models import Message
from apps.orchestrator.memory import short_term

pytestmark = pytest.mark.django_db

VOICE_TEXT = "Голосовые сообщения я пока не понимаю — напиши, пожалуйста, текстом."

# По документации v0.0.33, не подтверждено живым вебхуком.
AUDIO = {
    "type": "audio",
    "payload": {"url": "https://cdn.max.test/voice.ogg", "token": "tok-voice-1"},
    "transcription": None,
}
IMAGE = {"type": "image", "payload": {"url": "https://cdn.max.test/p.jpg"}}


def _payload(*, text="", attachments, user_id=7771, chat_id=8881, mid=None):
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Иван"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {
                "mid": mid or f"m-{uuid.uuid4()}",
                "seq": 1,
                "text": text,
                "attachments": attachments,
            },
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
    from unittest.mock import MagicMock

    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Чем помочь?"))
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


@pytest.fixture(autouse=True)
def _strict(settings):
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.STRICT_TENANT_REFUSE = True


def _voice_rows() -> int:
    return Message.all_tenants.filter(role="assistant", action_type="voice_not_supported").count()


class TestVoiceOnly:
    def test_a_voice_message_gets_the_honest_reply_not_the_concierge(
        self, mock_send, fake_redis, spy_concierge
    ):
        max_handler.handle_global_max_event(
            _payload(attachments=[AUDIO]), trace_id=str(uuid.uuid4())
        )

        assert len(mock_send) == 1
        assert mock_send[0]["text"] == VOICE_TEXT
        spy_concierge.assert_not_called()
        assert _voice_rows() == 1

    def test_several_voice_attachments_get_the_same_reply(
        self, mock_send, fake_redis, spy_concierge
    ):
        max_handler.handle_global_max_event(
            _payload(
                attachments=[AUDIO, dict(AUDIO, payload={"url": "https://cdn.max.test/2.ogg"})]
            ),
            trace_id=str(uuid.uuid4()),
        )

        assert len(mock_send) == 1
        assert mock_send[0]["text"] == VOICE_TEXT
        spy_concierge.assert_not_called()


class TestNotVoiceOnly:
    """Сторожа: заглушка не перехватывает ходы, которые голосовыми не являются."""

    def test_voice_with_a_photo_keeps_todays_path(self, mock_send, fake_redis, spy_concierge):
        # Известное ограничение (решение главного окна 15.09): есть image —
        # идёт фото-путь как сегодня, голосовое не слушаем.
        max_handler.handle_global_max_event(
            _payload(attachments=[AUDIO, IMAGE]), trace_id=str(uuid.uuid4())
        )

        assert len(mock_send) == 1
        assert mock_send[0]["text"] != VOICE_TEXT
        assert _voice_rows() == 0

    def test_text_with_a_voice_attachment_is_a_text_turn(
        self, mock_send, fake_redis, spy_concierge
    ):
        max_handler.handle_global_max_event(
            _payload(text="подскажи, где сделать маникюр", attachments=[AUDIO]),
            trace_id=str(uuid.uuid4()),
        )

        assert len(mock_send) == 1
        assert mock_send[0]["text"] != VOICE_TEXT
        assert _voice_rows() == 0
