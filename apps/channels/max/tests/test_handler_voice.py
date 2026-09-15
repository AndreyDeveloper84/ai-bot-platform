"""DRF-1939 — голосовое сообщение на per-tenant пути MAX.

Ход без текста с вложением шёл в ``food_scanner`` как «фото без байтов» и
получал «Фото пришло, но скачать не получилось». Теперь — честный ответ после
safety и до фото-блока: аудио не скачивается, навыки не вызываются.

Форма вложения — по документации MAX Bot API v0.0.33 (``docs/RECON_MAX_VOICE.md``),
**не подтверждена живым вебхуком**.
"""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest

from apps.channels.max import handler as max_handler
from apps.channels.max.photo import PhotoDownloadError
from apps.conversations.models import Message
from apps.orchestrator.memory import short_term
from apps.tenancy.context import tenant_scope, trace_id_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

VOICE_TEXT = "Голосовые сообщения я пока не понимаю — напиши, пожалуйста, текстом."
_DOWNLOAD_TARGET = "apps.channels.max.handler.download_photo"

# По документации v0.0.33, не подтверждено живым вебхуком.
AUDIO = {
    "type": "audio",
    "payload": {"url": "https://cdn.max.test/voice.ogg", "token": "tok-voice-1"},
    "transcription": None,
}
IMAGE = {"type": "image", "payload": {"url": "https://cdn.max.test/p.jpg"}}


def _payload(*, attachments, text="", user_id=24681, chat_id=13571, mid="m-voice-1"):
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Ольга"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": attachments},
        },
    }


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="voice-handler", name="Voice Handler Test")


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


@pytest.fixture(autouse=True)
def _strict(settings):
    settings.STRICT_TENANT_SCOPE = "strict"


class TestVoiceOnTenantPath:
    def test_a_voice_message_gets_the_honest_reply_and_nothing_is_downloaded(
        self, tenant, mock_send, fake_redis
    ):
        with patch(_DOWNLOAD_TARGET) as mock_dl:
            with tenant_scope(tenant), trace_id_scope(str(uuid4())):
                max_handler.handle_max_event(_payload(attachments=[AUDIO]))

        assert len(mock_send) == 1
        assert mock_send[0]["text"] == VOICE_TEXT
        mock_dl.assert_not_called()
        assert (
            Message.all_tenants.filter(role="assistant", action_type="voice_not_supported").count()
            == 1
        )

    def test_voice_with_a_photo_still_downloads_the_photo(self, tenant, mock_send, fake_redis):
        # Сторож и известное ограничение: есть image — фото-путь как сегодня.
        with patch(_DOWNLOAD_TARGET, side_effect=PhotoDownloadError("mocked")) as mock_dl:
            with tenant_scope(tenant), trace_id_scope(str(uuid4())):
                max_handler.handle_max_event(_payload(attachments=[AUDIO, IMAGE], mid="m-voice-2"))

        mock_dl.assert_called_once()
        assert len(mock_send) == 1
        assert mock_send[0]["text"] != VOICE_TEXT
