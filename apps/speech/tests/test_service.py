"""Сервис целиком: порядок проверок, отказы, нормализация, потолок, реестр. Без сети."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from django.core.cache import cache
from django.test import override_settings

from apps.speech import budget, registry
from apps.speech.providers.fake import FakeSpeechProvider
from apps.speech.providers.openai_stt import OpenAISpeechProvider
from apps.speech.service import transcribe_voice
from apps.speech.tests.test_ogg import opus_head, page
from apps.speech.types import RefusalCode, SpeechRefusal, Transcript

SECRET_TEXT = "СЕКРЕТНАЯ РАСШИФРОВКА триста грамм"


def ogg_of(seconds: float) -> bytes:
    return page(0, opus_head(0), flags=2) + page(int(seconds * 48_000), b"x", seq=1)


@pytest.fixture(autouse=True)
def _fresh_state():
    cache.clear()
    registry.set_provider_for_tests(None)
    yield
    registry.set_provider_for_tests(None)
    cache.clear()


@pytest.fixture
def fake() -> FakeSpeechProvider:
    provider = FakeSpeechProvider(text="Съела борщ, триста грамм.")
    registry.set_provider_for_tests(provider)
    return provider


class TestHappyPath:
    def test_numbers_normalized_and_duration_filled(self, fake):
        out = transcribe_voice(ogg_of(3.5), timeout_s=12.0)
        assert isinstance(out, Transcript)
        assert out.text == "Съела борщ, 300 грамм."
        assert out.duration_s == pytest.approx(3.5)
        assert out.provider == "fake"
        assert fake.calls == [(len(ogg_of(3.5)), "audio/ogg", 12.0)]

    @override_settings(VOICE_STT_TIMEOUT_S=7.5)
    def test_timeout_defaults_from_settings(self, fake):
        transcribe_voice(ogg_of(1))
        assert fake.calls[0][2] == 7.5

    def test_unreadable_container_still_goes_to_provider(self, fake):
        # Длительность неизвестна — лимит не проверить, провайдер решает
        out = transcribe_voice(b"OggS" + b"\x00" * 10)
        assert isinstance(out, Transcript)
        assert out.duration_s is None


class TestRefusalsBeforeMoney:
    @override_settings(VOICE_MAX_DURATION_S=60)
    def test_too_long_refused_without_calling_provider(self, fake):
        out = transcribe_voice(ogg_of(61))
        assert out == SpeechRefusal(RefusalCode.TOO_LONG, "61.0s>60s")
        assert fake.calls == []

    @override_settings(VOICE_MAX_DURATION_S=60)
    def test_exactly_at_limit_is_allowed(self, fake):
        out = transcribe_voice(ogg_of(60))
        assert isinstance(out, Transcript)

    @override_settings(VOICE_STT_MONTHLY_MINUTES_CAP=1)
    def test_budget_exhausted_refuses_without_calling_provider(self, fake):
        assert isinstance(transcribe_voice(ogg_of(30)), Transcript)
        assert isinstance(transcribe_voice(ogg_of(30)), Transcript)
        out = transcribe_voice(ogg_of(1))
        assert out == SpeechRefusal(RefusalCode.PROVIDER_UNAVAILABLE, "budget")
        assert len(fake.calls) == 2

    @override_settings(VOICE_STT_PROVIDER="nonexistent")
    def test_unknown_provider_is_refusal_not_crash(self, caplog):
        with caplog.at_level(logging.ERROR, logger="apps.speech.registry"):
            out = transcribe_voice(ogg_of(1))
        assert out == SpeechRefusal(RefusalCode.PROVIDER_UNAVAILABLE, "no_provider")
        assert "speech.registry.unknown_provider name=nonexistent" in caplog.text


class TestProviderOutcomes:
    def test_provider_refusal_passes_through(self):
        registry.set_provider_for_tests(
            FakeSpeechProvider(refusal=SpeechRefusal(RefusalCode.PROVIDER_UNAVAILABLE, "transport"))
        )
        assert transcribe_voice(ogg_of(1)) == SpeechRefusal(
            RefusalCode.PROVIDER_UNAVAILABLE, "transport"
        )

    def test_whitespace_only_text_is_empty(self):
        registry.set_provider_for_tests(FakeSpeechProvider(text="   \n"))
        assert transcribe_voice(ogg_of(1)) == SpeechRefusal(
            RefusalCode.EMPTY, "empty_after_normalize"
        )


class TestLogRedaction:
    def test_service_log_has_outcome_but_never_text(self, caplog):
        registry.set_provider_for_tests(FakeSpeechProvider(text=SECRET_TEXT))
        with caplog.at_level(logging.DEBUG, logger="apps.speech"):
            out = transcribe_voice(ogg_of(2))
        assert isinstance(out, Transcript)
        assert out.text == "СЕКРЕТНАЯ РАСШИФРОВКА 300 грамм"
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "speech.transcribe outcome=ok audio_s=2.00" in text
        assert "СЕКРЕТНАЯ" not in text
        assert "300 грамм" not in text


class TestBudget:
    @override_settings(VOICE_STT_MONTHLY_MINUTES_CAP=0)
    def test_cap_zero_means_unlimited(self):
        assert budget.reserve(10_000)
        assert cache.get(budget.month_key()) is None

    @override_settings(VOICE_STT_MONTHLY_MINUTES_CAP=2)
    def test_counts_per_month_and_refuses_over_cap(self):
        jan = datetime(2026, 1, 15, tzinfo=UTC)
        feb = datetime(2026, 2, 1, tzinfo=UTC)
        assert budget.reserve(100, now=jan)
        assert budget.reserve(19, now=jan)
        assert not budget.reserve(2, now=jan)  # 121 > 120
        assert budget.reserve(60, now=feb)  # новый месяц — новый счётчик
        assert cache.get(budget.month_key(jan)) == 121
        assert cache.get(budget.month_key(feb)) == 60

    @override_settings(VOICE_STT_MONTHLY_MINUTES_CAP=1)
    def test_cache_failure_allows_and_warns(self, monkeypatch, caplog):
        def boom(*_a, **_k):
            raise ConnectionError("redis down")

        monkeypatch.setattr("apps.speech.budget.cache.incr", boom)
        with caplog.at_level(logging.WARNING, logger="apps.speech.budget"):
            assert budget.reserve(30)
        assert "speech.budget.cache_unavailable exc=ConnectionError" in caplog.text


class TestRegistry:
    @override_settings(VOICE_STT_PROVIDER="fake")
    def test_fake_by_settings(self):
        assert isinstance(registry.get_provider(), FakeSpeechProvider)

    @override_settings(VOICE_STT_PROVIDER="openai")
    def test_openai_by_settings_and_cached(self):
        a = registry.get_provider()
        b = registry.get_provider()
        assert isinstance(a, OpenAISpeechProvider)
        assert a is b

    def test_default_is_openai(self):
        with override_settings(VOICE_STT_PROVIDER=""):
            assert registry.configured_provider_name() == "openai"

    def test_switching_settings_rebuilds(self):
        with override_settings(VOICE_STT_PROVIDER="fake"):
            first = registry.get_provider()
        with override_settings(VOICE_STT_PROVIDER="openai"):
            second = registry.get_provider()
        assert isinstance(first, FakeSpeechProvider)
        assert isinstance(second, OpenAISpeechProvider)
