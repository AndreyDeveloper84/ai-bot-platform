"""``voice_turn.resolve_voice_turn`` в отрыве от handler'а: флаги, отказы, бюджет, K19-Б. Без сети."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from apps.channels.max.audio import (
    AudioDownloadError,
    AudioFormatError,
    AudioTooLargeError,
)
from apps.channels.max.parser import CanonicalEvent
from apps.channels.max.voice import VOICE_ACTION_TYPE, VOICE_NOT_SUPPORTED_TEXT
from apps.channels.max.voice_turn import (
    CODE_DISABLED,
    CODE_DOWNLOAD_FAILED,
    CODE_TOO_LARGE,
    CODE_UNSUPPORTED_FORMAT,
    REFUSAL_TEXTS,
    VoiceRefused,
    VoiceResolved,
    resolve_voice_turn,
    strip_for_gate,
    with_voice_echo,
)
from apps.speech import registry
from apps.speech.providers.fake import FakeSpeechProvider
from apps.speech.tests.test_ogg import opus_head, page
from apps.speech.types import RefusalCode, SpeechRefusal

SECRET = "СЕКРЕТНАЯ РАСШИФРОВКА триста грамм"
_DOWNLOAD = "apps.channels.max.voice_turn.download_audio"

AUDIO = {
    "type": "audio",
    "payload": {"id": 1, "url": "https://a.oneme.ru/v.ogg?sig=S", "token": "t"},
}


def ogg_of(seconds: float) -> bytes:
    return page(0, opus_head(0), flags=2) + page(int(seconds * 48_000), b"x", seq=1)


def voice_event(attachments=None) -> CanonicalEvent:
    return CanonicalEvent(
        channel="max",
        channel_user_id="u1",
        channel_message_id="m1",
        chat_id="c1",
        text="",
        attachments=[AUDIO] if attachments is None else attachments,
    )


@pytest.fixture(autouse=True)
def _voice_on(settings):
    settings.VOICE_INPUT_ENABLED = True
    settings.VOICE_CROSS_BORDER_ALLOWED = True
    settings.VOICE_STT_PROVIDER = "fake"
    settings.VOICE_GATE_STRIP_PUNCT = True
    settings.VOICE_ECHO_MODE = "never"
    registry.set_provider_for_tests(None)
    yield
    registry.set_provider_for_tests(None)


@pytest.fixture
def fake() -> FakeSpeechProvider:
    provider = FakeSpeechProvider(text="Съела борщ, триста грамм.")
    registry.set_provider_for_tests(provider)
    return provider


class TestResolved:
    def test_text_substituted_audio_dropped_gate_text_stripped(self, fake):
        event = voice_event([AUDIO, {"type": "sticker", "payload": {}}])
        with patch(_DOWNLOAD, return_value=ogg_of(3)) as dl:
            out = resolve_voice_turn(event)
        assert isinstance(out, VoiceResolved)
        assert out.event.text == "Съела борщ, 300 грамм."
        assert out.event.attachments == [{"type": "sticker", "payload": {}}]
        assert out.event.channel_message_id == "m1"
        assert out.gate_text == "Съела борщ 300 грамм"
        assert out.transcript.duration_s == pytest.approx(3.0)
        dl.assert_called_once()
        assert dl.call_args.args[0] == AUDIO["payload"]["url"]
        assert fake.calls[0][2] == pytest.approx(15.0, abs=0.5)  # VOICE_STT_TIMEOUT_S по умолчанию

    def test_gate_text_equals_text_when_strip_disabled(self, fake, settings):
        settings.VOICE_GATE_STRIP_PUNCT = False
        with patch(_DOWNLOAD, return_value=ogg_of(1)):
            out = resolve_voice_turn(voice_event())
        assert isinstance(out, VoiceResolved)
        assert out.gate_text == out.event.text == "Съела борщ, 300 грамм."

    def test_log_carries_sizes_not_text(self, caplog):
        registry.set_provider_for_tests(FakeSpeechProvider(text=SECRET))
        with (
            patch(_DOWNLOAD, return_value=ogg_of(2)),
            caplog.at_level(logging.DEBUG, logger="apps.channels.max.voice_turn"),
        ):
            out = resolve_voice_turn(voice_event())
        assert isinstance(out, VoiceResolved)
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "channels.max.voice.resolved provider=fake audio_s=2.0" in text
        assert "СЕКРЕТНАЯ" not in text
        assert "sig=S" not in text


class TestFlags:
    def test_flag_off_is_the_drf1939_stub_and_nothing_is_downloaded(self, fake, settings):
        settings.VOICE_INPUT_ENABLED = False
        with patch(_DOWNLOAD) as dl:
            out = resolve_voice_turn(voice_event())
        assert out == VoiceRefused(
            CODE_DISABLED, VOICE_NOT_SUPPORTED_TEXT, VOICE_ACTION_TYPE, "flag_off"
        )
        dl.assert_not_called()
        assert fake.calls == []

    @pytest.mark.parametrize("value", ["true", "1", "yes", " True ", True])
    def test_flag_accepts_explicit_true_values(self, fake, settings, value):
        settings.VOICE_INPUT_ENABLED = value
        with patch(_DOWNLOAD, return_value=ogg_of(1)):
            assert isinstance(resolve_voice_turn(voice_event()), VoiceResolved)

    @pytest.mark.parametrize("value", ["on", "enabled", "yes please", "", 0, None])
    def test_flag_rejects_anything_else(self, fake, settings, value):
        settings.VOICE_INPUT_ENABLED = value
        with patch(_DOWNLOAD) as dl:
            out = resolve_voice_turn(voice_event())
        assert isinstance(out, VoiceRefused)
        assert out.code == CODE_DISABLED
        dl.assert_not_called()

    def test_cross_border_off_blocks_openai_before_download(self, settings):
        settings.VOICE_STT_PROVIDER = "openai"
        settings.VOICE_CROSS_BORDER_ALLOWED = False
        with patch(_DOWNLOAD) as dl:
            out = resolve_voice_turn(voice_event())
        assert out == VoiceRefused(
            "voice_provider_unavailable",
            REFUSAL_TEXTS["voice_provider_unavailable"],
            "voice_provider_unavailable",
            "cross_border",
        )
        dl.assert_not_called()

    def test_cross_border_off_does_not_block_fake_provider(self, fake, settings):
        settings.VOICE_CROSS_BORDER_ALLOWED = False
        with patch(_DOWNLOAD, return_value=ogg_of(1)):
            assert isinstance(resolve_voice_turn(voice_event()), VoiceResolved)


class TestRefusals:
    @pytest.mark.parametrize(
        ("exc", "code", "reason"),
        [
            (AudioTooLargeError("big"), CODE_TOO_LARGE, "too_large"),
            (AudioFormatError("mp3"), CODE_UNSUPPORTED_FORMAT, "not_ogg"),
            (AudioDownloadError("cdn 4xx: HTTP 404"), CODE_DOWNLOAD_FAILED, "cdn 4xx"),
            (
                AudioDownloadError("deadline: download exceeded budget"),
                CODE_DOWNLOAD_FAILED,
                "deadline",
            ),
            (RuntimeError("???"), CODE_DOWNLOAD_FAILED, "unexpected"),
        ],
    )
    def test_download_failures_become_refusals(self, fake, exc, code, reason):
        with patch(_DOWNLOAD, side_effect=exc):
            out = resolve_voice_turn(voice_event())
        assert out == VoiceRefused(code, REFUSAL_TEXTS[code], code, reason)
        assert fake.calls == []

    def test_no_audio_url_is_unsupported_format(self, fake):
        with patch(_DOWNLOAD) as dl:
            out = resolve_voice_turn(voice_event([{"type": "audio", "payload": {}}]))
        assert isinstance(out, VoiceRefused)
        assert out.code == CODE_UNSUPPORTED_FORMAT
        dl.assert_not_called()

    def test_too_long_refused_with_limit_in_text(self, fake, settings):
        settings.VOICE_MAX_DURATION_S = 60
        with patch(_DOWNLOAD, return_value=ogg_of(61)):
            out = resolve_voice_turn(voice_event())
        assert isinstance(out, VoiceRefused)
        assert out.code == "voice_too_long"
        assert out.action_type == "voice_too_long"
        assert "60 секунд" in out.text
        assert fake.calls == []

    @pytest.mark.parametrize(
        ("refusal", "code"),
        [
            (SpeechRefusal(RefusalCode.EMPTY, "empty_text"), "voice_empty"),
            (
                SpeechRefusal(RefusalCode.UNRECOGNIZED, "low_confidence"),
                "voice_unrecognized",
            ),
            (
                SpeechRefusal(RefusalCode.PROVIDER_UNAVAILABLE, "auth"),
                "voice_provider_unavailable",
            ),
        ],
    )
    def test_provider_refusals_pass_through_with_text(self, refusal, code):
        registry.set_provider_for_tests(FakeSpeechProvider(refusal=refusal))
        with patch(_DOWNLOAD, return_value=ogg_of(1)):
            out = resolve_voice_turn(voice_event())
        assert out == VoiceRefused(code, REFUSAL_TEXTS[code], code, refusal.reason)

    def test_every_code_has_a_text(self):
        for code in REFUSAL_TEXTS.values():
            assert code.strip()
        assert REFUSAL_TEXTS[CODE_DISABLED] == VOICE_NOT_SUPPORTED_TEXT


class TestBudgetAndRetry:
    def test_transport_failure_retried_once_when_time_remains(self):
        provider = FakeSpeechProvider(
            refusal=SpeechRefusal(RefusalCode.PROVIDER_UNAVAILABLE, "transport")
        )
        registry.set_provider_for_tests(provider)
        with patch(_DOWNLOAD, return_value=ogg_of(1)):
            out = resolve_voice_turn(voice_event(), budget_s=20.0)
        assert isinstance(out, VoiceRefused)
        assert out.reason == "transport"
        assert len(provider.calls) == 2

    def test_transport_failure_not_retried_when_budget_is_short(self):
        provider = FakeSpeechProvider(
            refusal=SpeechRefusal(RefusalCode.PROVIDER_UNAVAILABLE, "transport")
        )
        registry.set_provider_for_tests(provider)
        with patch(_DOWNLOAD, return_value=ogg_of(1)):
            out = resolve_voice_turn(voice_event(), budget_s=5.0)
        assert isinstance(out, VoiceRefused)
        assert len(provider.calls) == 1

    def test_non_transport_failure_never_retried(self):
        provider = FakeSpeechProvider(
            refusal=SpeechRefusal(RefusalCode.PROVIDER_UNAVAILABLE, "auth")
        )
        registry.set_provider_for_tests(provider)
        with patch(_DOWNLOAD, return_value=ogg_of(1)):
            resolve_voice_turn(voice_event(), budget_s=20.0)
        assert len(provider.calls) == 1

    def test_provider_gets_the_remaining_budget_not_more(self, fake):
        with patch(_DOWNLOAD, return_value=ogg_of(1)):
            resolve_voice_turn(voice_event(), budget_s=4.0)
        assert fake.calls[0][2] <= 4.0

    def test_download_deadline_capped_by_budget(self, fake):
        with patch(_DOWNLOAD, return_value=ogg_of(1)) as dl:
            resolve_voice_turn(voice_event(), budget_s=3.0)
        assert dl.call_args.kwargs["deadline_s"] == 3.0


class TestHelpers:
    @pytest.mark.parametrize(
        ("src", "expected"),
        [
            ("Умираю, хочу кофе.", "Умираю хочу кофе"),
            ("Умираю, как хочу этот маникюр!", "Умираю как хочу этот маникюр"),
            ("Съела борщ, 300 г.", "Съела борщ 300 г"),
            ("без знаков", "без знаков"),
            ("", ""),
        ],
    )
    def test_strip_for_gate(self, src, expected):
        assert strip_for_gate(src) == expected

    def test_echo_never_by_default(self, settings):
        settings.VOICE_ECHO_MODE = "never"
        assert with_voice_echo("Ответ", "привет") == "Ответ"

    def test_echo_always(self, settings):
        settings.VOICE_ECHO_MODE = "always"
        assert with_voice_echo("Ответ", "привет") == "Я услышала: «привет»\n\nОтвет"

    def test_echo_skips_empty_transcript(self, settings):
        settings.VOICE_ECHO_MODE = "always"
        assert with_voice_echo("Ответ", "") == "Ответ"
