"""Pain classifier tests (DRF-824 / Sprint 9 / P7)."""

from __future__ import annotations

import pytest

from apps.skills.health_screening.classifier import PainSignal, classify


class TestSoftPain:
    @pytest.mark.parametrize(
        "text",
        [
            "Болит спина",
            "болит",
            "спина болит уже неделю",
            "Шея ноет",
            "колено хрустит",
            "плечи тянет",
            "пульсирует в висках",
            "защемило в шее",
            "напряжение в спине",
            "Поясница ломит к вечеру",
            "стреляет в ягодицу",
            "Не могу повернуть шею",
        ],
    )
    def test_pain_words_signal_soft(self, text: str) -> None:
        """Mainstream pain mentions classify as soft."""
        assert classify(text) == PainSignal.SOFT


class TestRedFlags:
    @pytest.mark.parametrize(
        "text",
        [
            "Потерял чувствительность в ноге",
            "Онемение в руке",
            "Болит шея, отдаёт в руку",
            "Болит спина, отдает в ногу",
            "У меня температура 38",
            "Температура высокая третий день",
            "Тошнит после еды",
            "Рвота с утра",
            "Не могу встать с кровати",
            "Не могу ходить",
            "Теряю сознание под нагрузкой",
            "Давит в груди при подъёме",
            "Одышка появилась",
        ],
    )
    def test_red_flag_classified(self, text: str) -> None:
        assert classify(text) == PainSignal.RED_FLAG


class TestRedFlagShadowsSoft:
    """Red-flag pattern wins when it co-occurs with a soft pain word."""

    def test_pain_plus_numbness_is_red(self) -> None:
        assert classify("болит шея, онемение в руке") == PainSignal.RED_FLAG

    def test_pain_plus_radiation_is_red(self) -> None:
        assert classify("Тянет в пояснице, отдаёт в ногу") == PainSignal.RED_FLAG


class TestNoSignal:
    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   ",
            "Здравствуйте",
            "Хочу записаться на массаж",
            "Где вы находитесь?",
            "Какие услуги у вас есть?",
            "Завтра в 14:00 удобно",
        ],
    )
    def test_non_pain_messages(self, text: str) -> None:
        assert classify(text) == PainSignal.NONE

    def test_long_text_does_not_classify(self) -> None:
        """200-char cap: full-text questions about pain are an LLM job,
        not the classifier's."""
        long_text = "болит " * 100  # well over 200 chars
        assert classify(long_text) == PainSignal.NONE


class TestTypeTolerance:
    @pytest.mark.parametrize("bad", [None, 123, b"bolit", ["bolit"]])
    def test_non_str_returns_none(self, bad: object) -> None:
        assert classify(bad) == PainSignal.NONE  # type: ignore[arg-type]
