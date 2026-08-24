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


# ---------------------------------------------------------------------------
# DRF-973 — the stem is a WORD, not a substring.
#
# State on 2026-08-24 before the patch (VERIFIED by running the pre-patch
# ``classify`` over these three tuples): 30 of the 43 everyday phrases
# classified as SOFT, all 31 complaints were recognised, and 12 of the 13
# red flags — «онемела рука» matched nothing at all.
#
# ADD to these tuples — never trim them. NOT_PAIN_PHRASES is the ticket's
# direction; PAIN_PHRASES and RED_FLAG_PHRASES are the guard that says
# the fix bought its false-positive reduction with nothing.
# ---------------------------------------------------------------------------

NOT_PAIN_PHRASES: tuple[str, ...] = (
    # ── «больш*»: the ticket's headline case and its neighbours ──
    "спасибо большое",
    "большое спасибо",
    "спасибо большое, всё супер",
    "больше спасибо не надо",
    "больше не пишите",
    "больше вопросов нет",
    "хочу большой маникюр",
    "сколько стоит большая чистка лица",
    "у вас большой выбор мастеров",
    "запишите на большие ресницы",
    # ── «я больше не приду» is a CANCELLATION (DRF-1060); the pain stem
    #    in «больше» is the only reason it never reached booking ──
    "я больше не приду",
    "больше не приду",
    # ── other «бол» words that are not pain ──
    "болтать не буду",
    "давайте не будем болтать",
    "футбол",
    "я болею за спартак",
    # ── stems other than «бол», checked because the ticket asks ──
    "хочу стрелки",  # «стрел» — eyeliner, a service we sell
    "сделайте стрелки",
    "стрелки на глазах",
    "перманентные стрелки",
    "хрустальный маникюр",  # «хруст»
    "хочу зажим для волос",  # «зажим»
    "есть зажимы для волос?",
    "у меня напряжённая неделя",  # «напряж»
    "хочу напряжённый график",
    "у меня была напряжённая неделя, хочу расслабляющий массаж",
    # ── the question about a procedure that has not happened yet ──
    "а это больно?",
    "больно ли делать татуаж?",
    "больно будет?",
    "не колет ли лазер?",
    # ── plain goodbyes ──
    "спасибо, всё отлично",
    "спасибо, до свидания",
    "всё отлично, спасибо",
)

PAIN_PHRASES: tuple[str, ...] = (
    "болит спина",
    "после массажа боль",
    "боль в шее",
    "больно",
    "мне очень больно",
    "у меня болит поясница",
    "болят ноги",
    "боли в спине",
    "спина болит третий день",
    "болезненные ощущения",
    # prefixed forms — the word-start rule must not lose these
    "прострел в пояснице",
    "простреливает поясницу",
    "заболела спина",
    "разболелась голова",
    # the hypothetical frame does NOT veto a real complaint in the same turn
    "больно ли делать массаж, если болит спина",
    "стреляет в шею",
    "ноет плечо",
    "тянет поясницу",
    "шея хрустит",
    "хруст в шее",
    "ломит суставы",
    "ломота во всём теле",
    "колет в боку",
    "дёргает зуб",
    "пульсирует висок",
    "защемило нерв",
    "зажим в шее",
    "напряжение в спине",
    "спазм мышц",
    "судорога в ноге",
    "тяжесть в ногах",
    "усталость в спине",
)

RED_FLAG_PHRASES: tuple[str, ...] = (
    # DRF-973 — the two that matched NOTHING before the patch.
    "онемела рука",
    "немеет рука",
    "онемели пальцы",
    "рука онемела и не проходит",
    # the ones that already worked
    "онемение в ноге",
    "потеряла чувствительность",
    "отнимается нога",
    "отдаёт в руку",
    "температура 38.5",
    "тошнит",
    "рвота",
    "давит в груди",
    "одышка",
    "не могу встать",
    "не могу ходить",
    "теряю сознание",
)


class TestDrf973NotPain:
    @pytest.mark.parametrize("text", NOT_PAIN_PHRASES)
    def test_everyday_phrases_are_not_pain(self, text: str) -> None:
        assert classify(text) == PainSignal.NONE


class TestDrf973PainSurvives:
    """The half that makes the fix honest.

    A patch that removes a false positive by losing a true one is a
    defect shipped: this is a SAFETY gate, and the expensive direction
    is the miss.
    """

    @pytest.mark.parametrize("text", PAIN_PHRASES)
    def test_complaints_still_reach_the_screening(self, text: str) -> None:
        assert classify(text) != PainSignal.NONE

    @pytest.mark.parametrize("text", RED_FLAG_PHRASES)
    def test_red_flags_still_redirect_to_a_doctor(self, text: str) -> None:
        assert classify(text) == PainSignal.RED_FLAG


class TestDrf973MaskingIsPhraseScoped:
    """A masked phrase blanks itself and nothing else."""

    def test_masked_word_does_not_silence_the_rest(self) -> None:
        assert classify("болтали про то, что болит спина") == PainSignal.SOFT

    def test_masking_cannot_weld_two_halves_into_a_stem(self) -> None:
        # «стрелк» is masked between «хочу» and «и»; if the mask were a
        # deletion rather than a blank, the neighbours could join.
        assert classify("хочу стрелки и педикюр") == PainSignal.NONE

    def test_big_and_painful_in_one_turn_is_still_pain(self) -> None:
        assert classify("спасибо большое, но спина болит") == PainSignal.SOFT
