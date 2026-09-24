"""DRF-1942 — голосовое как вход на ГЛОБАЛЬНОМ пути MAX (этап 1, PR 3).

Тот же контракт, что у per-tenant теста (``apps/channels/max/tests/
test_handler_voice_input.py``), доказанный на втором входе: флаг выключен →
заглушка DRF-1939 байт в байт; расшифровка X доходит до консьержа как
набранный X; S1 в расшифровке останавливает ход до консьержа; отказ —
фразой человеку. Онбординг выключен, чтобы свободный текст шёл прямо к
модели-шпиону, как в ``test_first_contact_c01``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from apps.channels.max import handler as max_handler
from apps.channels.max.voice import VOICE_ACTION_TYPE, VOICE_NOT_SUPPORTED_TEXT
from apps.conversations.models import Message
from apps.orchestrator.memory import short_term
from apps.orchestrator.safety.gate import CRISIS_REPLY_TEXT
from apps.speech import registry
from apps.speech.providers.fake import FakeSpeechProvider
from apps.speech.tests.test_ogg import opus_head, page

pytestmark = pytest.mark.django_db

_DOWNLOAD = "apps.channels.max.voice_turn.download_audio"
AUDIO = {
    "type": "audio",
    "payload": {"id": 7, "url": "https://a.oneme.ru/v.ogg?sig=S", "token": "t"},
}
S1_TEXT = "последние дни мне так тяжело что хочу умереть"


def ogg_of(seconds: float) -> bytes:
    return page(0, opus_head(0), flags=2) + page(int(seconds * 48_000), b"x", seq=1)


def _msg(
    *,
    text: str = "",
    user_id: int,
    chat_id: int = 8899,
    mid: str = "m-1",
    attachments=None,
) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Ирина"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {
                "mid": mid,
                "seq": 1,
                "text": text,
                "attachments": attachments or [],
            },
        },
    }


@pytest.fixture(autouse=True)
def _flags(settings):
    settings.GLOBAL_BOT_ONBOARDING = False
    settings.VOICE_INPUT_ENABLED = True
    settings.VOICE_CROSS_BORDER_ALLOWED = True
    settings.VOICE_STT_PROVIDER = "fake"
    settings.VOICE_GATE_STRIP_PUNCT = True
    settings.VOICE_ECHO_MODE = "never"
    registry.set_provider_for_tests(None)
    yield
    registry.set_provider_for_tests(None)


@pytest.fixture(autouse=True)
def _no_chat_actions(monkeypatch):
    monkeypatch.setattr(
        "apps.channels.max.outbound.send_chat_action", lambda **kwargs: {"ok": True}
    )


@pytest.fixture(autouse=True)
def _no_upcoming(monkeypatch):
    from apps.booking.services.records import VisitsResult

    monkeypatch.setattr(
        "apps.booking.services.records.list_upcoming",
        lambda **kwargs: VisitsResult(status="empty"),
    )


@pytest.fixture
def sent(monkeypatch):
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
def concierge(monkeypatch):
    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Расскажи чуть подробнее?", persisted=False))
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


def _provider(text: str) -> FakeSpeechProvider:
    provider = FakeSpeechProvider(text=text)
    registry.set_provider_for_tests(provider)
    return provider


class TestGlobalVoice:
    def test_flag_off_is_the_drf1939_stub(self, sent, fake_redis, concierge, settings):
        settings.VOICE_INPUT_ENABLED = False
        provider = _provider("привет")
        with patch(_DOWNLOAD) as dl:
            max_handler.handle_global_max_event(_msg(user_id=70000, attachments=[AUDIO]))
        assert [c["text"] for c in sent] == [VOICE_NOT_SUPPORTED_TEXT]
        assert Message.all_tenants.filter(action_type=VOICE_ACTION_TYPE).count() == 1
        concierge.assert_not_called()
        dl.assert_not_called()
        assert provider.calls == []

    def test_voice_reaches_the_model_exactly_like_typed_text(self, sent, fake_redis, concierge):
        _provider("хочу снять напряжение")
        with patch(_DOWNLOAD, return_value=ogg_of(2)):
            max_handler.handle_global_max_event(
                _msg(user_id=70001, chat_id=1, mid="v-1", attachments=[AUDIO])
            )
        max_handler.handle_global_max_event(
            _msg(text="хочу снять напряжение", user_id=70002, chat_id=2, mid="t-1")
        )
        assert concierge.call_count == 2
        voice_text = concierge.call_args_list[0].args[0]
        typed_text = concierge.call_args_list[1].args[0]
        assert voice_text == typed_text == "хочу снять напряжение"
        assert sent[0]["text"] == sent[1]["text"]

    def test_s1_in_voice_stops_before_the_model(self, sent, fake_redis, concierge):
        _provider(S1_TEXT)
        with patch(_DOWNLOAD, return_value=ogg_of(3)):
            max_handler.handle_global_max_event(_msg(user_id=70003, attachments=[AUDIO]))
        assert [c["text"] for c in sent] == [CRISIS_REPLY_TEXT]
        assert Message.all_tenants.filter(action_type="safety_pre_check").count() == 1
        concierge.assert_not_called()

    def test_k19_comma_hyperbole_goes_to_the_model_with_strip(self, sent, fake_redis, concierge):
        _provider("Умираю, хочу кофе.")
        with patch(_DOWNLOAD, return_value=ogg_of(2)):
            max_handler.handle_global_max_event(_msg(user_id=70004, attachments=[AUDIO]))
        assert concierge.call_count == 1
        assert (
            concierge.call_args.args[0] == "Умираю, хочу кофе."
        )  # модели — оригинал, не копия без знаков

    def test_k19_same_transcript_stops_when_strip_is_off(
        self, sent, fake_redis, concierge, settings
    ):
        settings.VOICE_GATE_STRIP_PUNCT = False
        _provider("Умираю, хочу кофе.")
        with patch(_DOWNLOAD, return_value=ogg_of(2)):
            max_handler.handle_global_max_event(_msg(user_id=70005, attachments=[AUDIO]))
        assert Message.all_tenants.filter(action_type="safety_pre_check").count() == 1
        concierge.assert_not_called()

    def test_too_long_is_answered_and_the_model_is_not_called(
        self, sent, fake_redis, concierge, settings
    ):
        settings.VOICE_MAX_DURATION_S = 60
        provider = _provider("привет")
        with patch(_DOWNLOAD, return_value=ogg_of(61)):
            max_handler.handle_global_max_event(_msg(user_id=70006, attachments=[AUDIO]))
        assert len(sent) == 1
        assert "60 секунд" in sent[0]["text"]
        assert Message.all_tenants.filter(action_type="voice_too_long").count() == 1
        concierge.assert_not_called()
        assert provider.calls == []

    def test_echo_always_prefixes_the_model_reply(self, sent, fake_redis, concierge, settings):
        settings.VOICE_ECHO_MODE = "always"
        _provider("привет")
        with patch(_DOWNLOAD, return_value=ogg_of(2)):
            max_handler.handle_global_max_event(_msg(user_id=70007, attachments=[AUDIO]))
        assert sent[0]["text"] == "Я услышала: «привет»\n\nРасскажи чуть подробнее?"
