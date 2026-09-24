"""DRF-1942 — голосовое как вход на per-tenant пути MAX (этап 1, PR 3).

Приёмка из ТЗ: флаг выключен → байт в байт как заглушка DRF-1939; голосовое
с расшифровкой X ведёт себя как набранный X (паритет); S1 в расшифровке —
в том числе в середине длинного текста — останавливает ход до навыков;
каждый отказ доходит до человека фразой; повтор вебхука не распознаёт
второй раз; в логах нет ни расшифровки, ни ссылки. Сеть не используется:
скачивание подменено, провайдер — ``fake``.
"""

from __future__ import annotations

import logging
from unittest.mock import patch
from uuid import uuid4

import pytest

from apps.channels.max import handler as max_handler
from apps.channels.max.audio import (
    AudioDownloadError,
    AudioFormatError,
    AudioTooLargeError,
)
from apps.channels.max.voice import VOICE_ACTION_TYPE, VOICE_NOT_SUPPORTED_TEXT
from apps.channels.max.voice_turn import (
    CODE_DOWNLOAD_FAILED,
    CODE_TOO_LARGE,
    CODE_UNSUPPORTED_FORMAT,
    REFUSAL_TEXTS,
)
from apps.conversations.models import Conversation, Message
from apps.orchestrator.memory import short_term
from apps.orchestrator.safety.gate import CRISIS_REPLY_TEXT
from apps.speech import registry
from apps.speech.providers.fake import FakeSpeechProvider
from apps.speech.tests.test_ogg import opus_head, page
from apps.speech.types import RefusalCode, SpeechRefusal
from apps.tenancy.context import tenant_scope, trace_id_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

_DOWNLOAD = "apps.channels.max.voice_turn.download_audio"

# Форма вложения — по живому замеру 17.09 (§1.4 отчёта этапа 0).
AUDIO = {
    "type": "audio",
    "payload": {
        "id": 4318852874390,
        "url": "https://a.oneme.ru/v.ogg?sig=SECRET-SIG",
        "token": "t",
    },
}

S1_TEXT = "последние дни мне так тяжело что хочу умереть"
LONG_WITH_S1 = (
    "сегодня был очень длинный день и я хочу рассказать как он прошёл. "
    "утром я проснулась поздно потому что будильник не сработал. "
    "на завтрак успела только выпить кофе и съесть бутерброд с сыром. "
    "потом долго ехала на работу потому что были пробки. "
    "на работе было много встреч и почти не было времени пообедать. "
    "если честно иногда мне кажется что не хочу больше жить. "
    "дома я приготовила ужин из курицы и овощей. "
    "потом посмотрела сериал и немного почитала книгу. "
    "перед сном выпила чай с ромашкой."
)


def ogg_of(seconds: float) -> bytes:
    return page(0, opus_head(0), flags=2) + page(int(seconds * 48_000), b"x", seq=1)


def _payload(*, attachments, text="", user_id=24681, chat_id=13571, mid="m-vi-1"):
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Ольга"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": attachments},
        },
    }


def _run(tenant, payload):
    with tenant_scope(tenant), trace_id_scope(str(uuid4())):
        max_handler.handle_max_event(payload)


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="voice-input-handler", name="Voice Input Handler Test")


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
def _voice_on(settings):
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.VOICE_INPUT_ENABLED = True
    settings.VOICE_CROSS_BORDER_ALLOWED = True
    settings.VOICE_STT_PROVIDER = "fake"
    settings.VOICE_GATE_STRIP_PUNCT = True
    settings.VOICE_ECHO_MODE = "never"
    registry.set_provider_for_tests(None)
    yield
    registry.set_provider_for_tests(None)


def _provider(text: str = "привет", refusal: SpeechRefusal | None = None) -> FakeSpeechProvider:
    provider = FakeSpeechProvider(text=text, refusal=refusal)
    registry.set_provider_for_tests(provider)
    return provider


def _messages(tenant, role: str) -> list[Message]:
    return list(Message.all_tenants.filter(conversation__tenant=tenant, role=role).order_by("id"))


class TestFlagOff:
    def test_byte_for_byte_the_drf1939_stub(self, tenant, mock_send, fake_redis, settings):
        settings.VOICE_INPUT_ENABLED = False
        provider = _provider()
        with patch(_DOWNLOAD) as dl:
            _run(tenant, _payload(attachments=[AUDIO]))
        assert mock_send == [{"chat_id": "13571", "text": VOICE_NOT_SUPPORTED_TEXT}]
        assistant = _messages(tenant, "assistant")
        assert [m.action_type for m in assistant] == [VOICE_ACTION_TYPE]
        assert assistant[0].content == VOICE_NOT_SUPPORTED_TEXT
        dl.assert_not_called()
        assert provider.calls == []


class TestParity:
    def test_voice_with_transcript_x_behaves_like_typed_x(self, tenant, mock_send, fake_redis):
        _provider("привет")
        with patch(_DOWNLOAD, return_value=ogg_of(2)):
            _run(
                tenant,
                _payload(attachments=[AUDIO], user_id=1001, chat_id=2001, mid="m-v"),
            )
        voice_sent = [c["text"] for c in mock_send]
        mock_send.clear()
        _run(
            tenant,
            _payload(attachments=[], text="привет", user_id=1002, chat_id=2002, mid="m-t"),
        )
        typed_sent = [c["text"] for c in mock_send]

        assert voice_sent
        assert voice_sent == typed_sent

        # pk у Message — не монотонный, поэтому сравниваются отсортированные
        # наборы строк, а не порядок вставки.
        rows = Message.all_tenants.filter(conversation__tenant=tenant)
        by_chat: dict[str, list[tuple[str, str, str]]] = {}
        for m in rows:
            key = m.conversation.bot_user.channel_user_id
            by_chat.setdefault(key, []).append((m.role, m.content, m.action_type))
        voice_rows, typed_rows = sorted(by_chat["1001"]), sorted(by_chat["1002"])
        assert ("user", "привет", "") in voice_rows
        assert voice_rows == typed_rows

    def test_numbers_arrive_as_digits_in_the_recorded_turn(self, tenant, mock_send, fake_redis):
        _provider("Съела борщ, триста грамм.")
        with patch(_DOWNLOAD, return_value=ogg_of(3)):
            _run(tenant, _payload(attachments=[AUDIO]))
        user = _messages(tenant, "user")
        assert [m.content for m in user] == ["Съела борщ, 300 грамм."]


class TestSafety:
    def test_s1_in_transcript_short_circuits_before_skills(self, tenant, mock_send, fake_redis):
        _provider(S1_TEXT)
        with (
            patch(_DOWNLOAD, return_value=ogg_of(3)),
            patch.object(max_handler, "turn_reply_to_skill_result") as dispatch,
        ):
            _run(tenant, _payload(attachments=[AUDIO]))
        assert [c["text"] for c in mock_send] == [CRISIS_REPLY_TEXT]
        assert [m.action_type for m in _messages(tenant, "assistant")] == ["safety_pre_check"]
        dispatch.assert_not_called()

    def test_s1_in_the_middle_of_a_long_transcript_is_caught(self, tenant, mock_send, fake_redis):
        _provider(LONG_WITH_S1)
        with (
            patch(_DOWNLOAD, return_value=ogg_of(40)),
            patch.object(max_handler, "turn_reply_to_skill_result") as dispatch,
        ):
            _run(tenant, _payload(attachments=[AUDIO]))
        assert [c["text"] for c in mock_send] == [CRISIS_REPLY_TEXT]
        dispatch.assert_not_called()

    def test_k19_comma_hyperbole_passes_the_gate_with_strip(self, tenant, mock_send, fake_redis):
        _provider("Умираю, хочу кофе.")
        with patch(_DOWNLOAD, return_value=ogg_of(2)):
            _run(tenant, _payload(attachments=[AUDIO]))
        assistant = _messages(tenant, "assistant")
        assert len(assistant) == 1
        assert assistant[0].action_type != "safety_pre_check"
        assert [m.content for m in _messages(tenant, "user")] == ["Умираю, хочу кофе."]

    def test_k19_same_transcript_stops_when_strip_is_off(
        self, tenant, mock_send, fake_redis, settings
    ):
        settings.VOICE_GATE_STRIP_PUNCT = False
        _provider("Умираю, хочу кофе.")
        with patch(_DOWNLOAD, return_value=ogg_of(2)):
            _run(tenant, _payload(attachments=[AUDIO]))
        assert [m.action_type for m in _messages(tenant, "assistant")] == ["safety_pre_check"]


class TestRefusalsReachThePerson:
    @pytest.mark.parametrize(
        ("download", "refusal", "code"),
        [
            (AudioTooLargeError("big"), None, CODE_TOO_LARGE),
            (AudioDownloadError("cdn 5xx: HTTP 503"), None, CODE_DOWNLOAD_FAILED),
            (AudioFormatError("mp3"), None, CODE_UNSUPPORTED_FORMAT),
            (None, SpeechRefusal(RefusalCode.EMPTY, "empty_text"), "voice_empty"),
            (
                None,
                SpeechRefusal(RefusalCode.UNRECOGNIZED, "low"),
                "voice_unrecognized",
            ),
            (
                None,
                SpeechRefusal(RefusalCode.PROVIDER_UNAVAILABLE, "auth"),
                "voice_provider_unavailable",
            ),
        ],
    )
    def test_each_refusal_is_answered_with_its_text(
        self, tenant, mock_send, fake_redis, download, refusal, code
    ):
        _provider(refusal=refusal)
        kwargs = {"side_effect": download} if download is not None else {"return_value": ogg_of(2)}
        with patch(_DOWNLOAD, **kwargs):
            _run(tenant, _payload(attachments=[AUDIO]))
        assert [c["text"] for c in mock_send] == [REFUSAL_TEXTS[code]]
        assert [m.action_type for m in _messages(tenant, "assistant")] == [code]

    def test_too_long_is_refused_before_the_provider(self, tenant, mock_send, fake_redis, settings):
        settings.VOICE_MAX_DURATION_S = 60
        provider = _provider()
        with patch(_DOWNLOAD, return_value=ogg_of(61)):
            _run(tenant, _payload(attachments=[AUDIO]))
        assert len(mock_send) == 1
        assert "60 секунд" in mock_send[0]["text"]
        assert [m.action_type for m in _messages(tenant, "assistant")] == ["voice_too_long"]
        assert provider.calls == []

    def test_cross_border_off_refuses_without_download(
        self, tenant, mock_send, fake_redis, settings
    ):
        settings.VOICE_STT_PROVIDER = "openai"
        settings.VOICE_CROSS_BORDER_ALLOWED = False
        with patch(_DOWNLOAD) as dl:
            _run(tenant, _payload(attachments=[AUDIO]))
        assert [c["text"] for c in mock_send] == [REFUSAL_TEXTS["voice_provider_unavailable"]]
        dl.assert_not_called()


class TestIdempotencyAndHandoff:
    def test_repeated_webhook_does_not_transcribe_twice(self, tenant, mock_send, fake_redis):
        provider = _provider()
        with patch(_DOWNLOAD, return_value=ogg_of(2)) as dl:
            _run(tenant, _payload(attachments=[AUDIO], mid="m-same"))
            _run(tenant, _payload(attachments=[AUDIO], mid="m-same"))
        assert len(provider.calls) == 1
        assert dl.call_count == 1
        assert len(mock_send) == 1

    def test_under_operator_nothing_is_downloaded_and_the_bot_is_silent(
        self, tenant, mock_send, fake_redis
    ):
        _run(tenant, _payload(attachments=[], text="привет", mid="m-h1"))
        assert len(mock_send) == 1
        mock_send.clear()
        conv = Conversation.all_tenants.get(tenant=tenant)
        Conversation.all_tenants.filter(pk=conv.pk).update(state=Conversation.State.HUMAN_HANDOFF)

        provider = _provider()
        with patch(_DOWNLOAD) as dl:
            _run(tenant, _payload(attachments=[AUDIO], mid="m-h2"))
        assert mock_send == []
        dl.assert_not_called()
        assert provider.calls == []


class TestEchoAndLogs:
    def test_echo_always_prefixes_the_reply(self, tenant, mock_send, fake_redis, settings):
        settings.VOICE_ECHO_MODE = "always"
        _provider("привет")
        with patch(_DOWNLOAD, return_value=ogg_of(2)):
            _run(tenant, _payload(attachments=[AUDIO]))
        assert len(mock_send) == 1
        assert mock_send[0]["text"].startswith("Я услышала: «привет»\n\n")
        assert _messages(tenant, "assistant")[0].content == mock_send[0]["text"]

    def test_logs_carry_neither_transcript_nor_url(self, tenant, mock_send, fake_redis, caplog):
        _provider("СЕКРЕТНОЕ СЛОВО ксилофон")
        with (
            patch(_DOWNLOAD, return_value=ogg_of(2)),
            caplog.at_level(logging.DEBUG, logger="apps"),
        ):
            _run(tenant, _payload(attachments=[AUDIO]))
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "channels.max.voice.resolved provider=fake" in text
        assert "ксилофон" not in text
        assert "SECRET-SIG" not in text
