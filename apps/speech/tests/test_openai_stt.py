"""Провайдер OpenAI: SDK подменяется целиком, сеть не используется, провайдер никогда не бросает."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest
from django.test import override_settings

from apps.speech.providers.openai_stt import DEFAULT_MODEL, OpenAISpeechProvider
from apps.speech.types import RefusalCode, SpeechRefusal, Transcript

SECRET_TEXT = "СЕКРЕТНАЯ РАСШИФРОВКА не должна попасть в лог"
TEST_KEY = "sk-test"  # pragma: allowlist secret
SETTINGS_KEY = "from-settings"  # pragma: allowlist secret


def _client_with(create):
    client = MagicMock()
    client.audio.transcriptions.create = create
    return client


def _status_error(status: int) -> openai.APIStatusError:
    req = httpx.Request("POST", "https://api.openai.com/v1/audio/transcriptions")
    resp = httpx.Response(status, request=req)
    return openai.APIStatusError("boom", response=resp, body=None)


@pytest.fixture
def provider() -> OpenAISpeechProvider:
    return OpenAISpeechProvider(api_key=TEST_KEY, proxy="", model="gpt-transcribe")


class TestClientConstruction:
    def test_no_proxy(self):
        p = OpenAISpeechProvider(api_key="k", proxy="")  # pragma: allowlist secret
        with (
            patch("openai.OpenAI") as cls,
            patch("openai.DefaultHttpxClient") as http_cls,
        ):
            p._get_client(15.0)
        kwargs = cls.call_args.kwargs
        assert kwargs["api_key"] == "k"
        assert kwargs["timeout"] == 15.0
        assert kwargs["max_retries"] == 0
        assert "http_client" not in kwargs
        http_cls.assert_not_called()

    def test_proxy_goes_through_sdk_httpx_client(self):
        p = OpenAISpeechProvider(
            api_key="k", proxy="socks5://u:p@proxy.example:1080"
        )  # pragma: allowlist secret
        with (
            patch("openai.OpenAI") as cls,
            patch("openai.DefaultHttpxClient") as http_cls,
        ):
            p._get_client(9.0)
        http_cls.assert_called_once_with(proxy="socks5://u:p@proxy.example:1080", timeout=9.0)
        assert cls.call_args.kwargs["http_client"] is http_cls.return_value

    def test_client_cached_until_timeout_changes(self):
        p = OpenAISpeechProvider(api_key="k", proxy="")  # pragma: allowlist secret
        first, second = MagicMock(name="client-15"), MagicMock(name="client-7")
        with patch("openai.OpenAI", side_effect=[first, second]) as cls:
            a = p._get_client(15.0)
            b = p._get_client(15.0)
            c = p._get_client(7.0)
        assert a is first
        assert b is first
        assert c is second
        assert cls.call_count == 2

    @override_settings(OPENAI_API_KEY=SETTINGS_KEY, OPENAI_PROXY="http://p:1", VOICE_STT_MODEL="")
    def test_defaults_from_settings(self):
        p = OpenAISpeechProvider()
        assert p._api_key == SETTINGS_KEY
        assert p._proxy == "http://p:1"
        assert p.model == DEFAULT_MODEL


class TestTranscribe:
    def test_happy_path(self, provider):
        create = MagicMock(return_value=SimpleNamespace(text="  Съела борщ, триста грамм.  "))
        with patch.object(provider, "_get_client", return_value=_client_with(create)):
            out = provider.transcribe(b"OggS...", mime="audio/ogg", timeout_s=15.0)
        assert isinstance(out, Transcript)
        assert out.text == "Съела борщ, триста грамм."  # нормализация — в service, не здесь
        assert out.provider == "openai"
        assert out.model == "gpt-transcribe"
        kwargs = create.call_args.kwargs
        assert kwargs["model"] == "gpt-transcribe"
        assert kwargs["language"] == "ru"
        assert kwargs["temperature"] == 0
        assert kwargs["response_format"] == "json"
        name, buf, mime = kwargs["file"]
        assert name == "voice.ogg"
        assert buf.read() == b"OggS..."
        assert mime == "audio/ogg"

    def test_empty_text_is_voice_empty(self, provider):
        create = MagicMock(return_value=SimpleNamespace(text="   "))
        with patch.object(provider, "_get_client", return_value=_client_with(create)):
            out = provider.transcribe(b"OggS", mime="audio/ogg", timeout_s=15.0)
        assert out == SpeechRefusal(RefusalCode.EMPTY, "empty_text")

    def test_no_api_key_refuses_without_network(self):
        p = OpenAISpeechProvider(api_key="", proxy="")
        with patch.object(p, "_get_client") as get_client:
            out = p.transcribe(b"OggS", mime="audio/ogg", timeout_s=15.0)
        assert out == SpeechRefusal(RefusalCode.PROVIDER_UNAVAILABLE, "no_api_key")
        get_client.assert_not_called()

    def test_zero_budget_refuses_without_network(self, provider):
        with patch.object(provider, "_get_client") as get_client:
            out = provider.transcribe(b"OggS", mime="audio/ogg", timeout_s=0)
        assert out == SpeechRefusal(RefusalCode.PROVIDER_UNAVAILABLE, "no_time_budget")
        get_client.assert_not_called()

    @pytest.mark.parametrize(
        ("exc", "reason"),
        [
            (
                openai.APITimeoutError(request=httpx.Request("POST", "https://x")),
                "transport",
            ),
            (
                openai.APIConnectionError(request=httpx.Request("POST", "https://x")),
                "transport",
            ),
            (
                _status_error(429).__class__("r", response=_status_error(429).response, body=None),
                "http_429",
            ),
            (_status_error(500), "http_500"),
            (_status_error(401), "http_401"),
            (RuntimeError("anything"), "unexpected"),
        ],
    )
    def test_every_failure_is_a_refusal_not_an_exception(self, provider, exc, reason):
        create = MagicMock(side_effect=exc)
        with patch.object(provider, "_get_client", return_value=_client_with(create)):
            out = provider.transcribe(b"OggS", mime="audio/ogg", timeout_s=15.0)
        assert isinstance(out, SpeechRefusal)
        assert out.code == RefusalCode.PROVIDER_UNAVAILABLE
        assert out.reason == reason

    def test_rate_limit_and_auth_reasons(self, provider):
        req = httpx.Request("POST", "https://x")
        for status, cls, reason in (
            (429, openai.RateLimitError, "rate_limited"),
            (401, openai.AuthenticationError, "auth"),
        ):
            exc = cls("m", response=httpx.Response(status, request=req), body=None)
            create = MagicMock(side_effect=exc)
            with patch.object(provider, "_get_client", return_value=_client_with(create)):
                out = provider.transcribe(b"OggS", mime="audio/ogg", timeout_s=15.0)
            assert out == SpeechRefusal(RefusalCode.PROVIDER_UNAVAILABLE, reason)


class TestLogRedaction:
    def test_failure_log_has_class_not_text_or_key(self, provider, caplog):
        create = MagicMock(side_effect=RuntimeError(SECRET_TEXT))
        with (
            patch.object(provider, "_get_client", return_value=_client_with(create)),
            caplog.at_level(logging.WARNING, logger="apps.speech.providers.openai_stt"),
        ):
            provider.transcribe(b"OggS", mime="audio/ogg", timeout_s=15.0)
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "speech.openai.failed reason=unexpected exc=RuntimeError" in text
        assert SECRET_TEXT not in text
        assert TEST_KEY not in text

    def test_success_writes_nothing_with_text(self, provider, caplog):
        create = MagicMock(return_value=SimpleNamespace(text=SECRET_TEXT))
        with (
            patch.object(provider, "_get_client", return_value=_client_with(create)),
            caplog.at_level(logging.DEBUG, logger="apps.speech.providers.openai_stt"),
        ):
            out = provider.transcribe(b"OggS", mime="audio/ogg", timeout_s=15.0)
        assert isinstance(out, Transcript)
        assert out.text == SECRET_TEXT
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "speech.openai.ok latency_ms=" in text
        assert SECRET_TEXT not in text
