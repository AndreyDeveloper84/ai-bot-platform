"""FoodCorrectionSkill tests (DRF-822 / Sprint 9 / P5)."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from apps.skills.base import SkillContext
from apps.skills.food_correction.skill import _PROMPTS as _PROMPTS_FOR_TEST
from apps.skills.food_correction.skill import FoodCorrectionSkill


def _context(text: str) -> SkillContext:
    return SkillContext(
        conversation=Mock(id="conv-1"),
        bot_user=Mock(),
        message_text=text,
    )


# ─── matches ──────────────────────────────────────────────────────────────


class TestMatches:
    @pytest.mark.parametrize(
        "callback",
        [
            "cb:food:correct:grams:scan-1",
            "cb:food:correct:name:scan-1",
            "cb:food:correct:macros:scan-1",
        ],
    )
    def test_three_correction_callbacks_match(self, callback: str) -> None:
        assert FoodCorrectionSkill().matches(_context(callback))

    def test_unknown_field_does_not_match(self) -> None:
        """Misroute guard — only the 3 known fields are claimed."""
        assert not FoodCorrectionSkill().matches(_context("cb:food:correct:portion:scan-1"))

    def test_food_scanner_callbacks_not_claimed(self) -> None:
        """P5 must NOT claim P1 (food_scanner) callbacks even though
        both start with cb:food:*."""
        for cb in [
            "cb:food:to_diary:scan-1",
            "cb:food:clarify:scan-1",
            "cb:food:reject:scan-1",
        ]:
            assert not FoodCorrectionSkill().matches(_context(cb))

    def test_food_clarify_callbacks_not_claimed(self) -> None:
        """P5 must NOT claim P4 (food_clarify) callbacks."""
        assert not FoodCorrectionSkill().matches(_context("cb:food:diary"))
        assert not FoodCorrectionSkill().matches(_context("cb:food:typo"))

    def test_plain_text_does_not_match(self) -> None:
        assert not FoodCorrectionSkill().matches(_context("250"))


# ─── handle ──────────────────────────────────────────────────────────────


class TestHandle:
    def test_grams_emits_grams_prompt(self) -> None:
        result = FoodCorrectionSkill().handle(_context("cb:food:correct:grams:scan-1"))
        assert "грамм" in result.reply_text.lower()
        assert result.action_type == "food_correction_prompt"
        assert result.action_data == {
            "field": "grams",
            "scan_id": "scan-1",
            # DRF-1454 — the memory-aware prompt flag; False with nothing stored.
            "remembered": False,
        }

    def test_name_emits_name_prompt(self) -> None:
        result = FoodCorrectionSkill().handle(_context("cb:food:correct:name:scan-1"))
        assert "напиши" in result.reply_text.lower() or "что было" in result.reply_text.lower()
        assert result.action_data == {
            "field": "name",
            "scan_id": "scan-1",
            # DRF-1454 — the memory-aware prompt flag; False with nothing stored.
            "remembered": False,
        }

    def test_macros_emits_macros_prompt(self) -> None:
        result = FoodCorrectionSkill().handle(_context("cb:food:correct:macros:scan-1"))
        assert "макрос" in result.reply_text.lower()
        assert result.action_data == {
            "field": "macros",
            "scan_id": "scan-1",
            # DRF-1454 — the memory-aware prompt flag; False with nothing stored.
            "remembered": False,
        }

    def test_scan_id_propagated_to_action_data(self) -> None:
        """The follow-up text input from the user must be tied back to
        this scan_id when the apply path lands in Phase 1."""
        result = FoodCorrectionSkill().handle(_context("cb:food:correct:grams:abc-xyz-789"))
        assert result.action_data is not None
        assert result.action_data["scan_id"] == "abc-xyz-789"

    def test_each_prompt_differs(self) -> None:
        """All three replies must be distinguishable — no copy-paste accident."""
        replies = {
            FoodCorrectionSkill().handle(_context(f"cb:food:correct:{field}:s-1")).reply_text
            for field in ("grams", "name", "macros")
        }
        assert len(replies) == 3


# ─── registration ────────────────────────────────────────────────────────


class TestRegistration:
    def test_food_correction_registered(self) -> None:
        from apps.skills.registry import registered

        names = [s.name for s in registered()]
        assert "food_correction" in names
        # Both P1 and P5 present; their callbacks are disjoint
        # (to_diary/clarify/reject vs correct:*).
        assert "food_scanner" in names


# ─── DRF-1454: the answer turn ───────────────────────────────────────────


def _pending_context(
    text: str,
    *,
    field: str = "grams",
    dish: str = "борщ",
    age_seconds: int = 0,
) -> SkillContext:
    """A context whose conversation is waiting for a correction value."""

    from datetime import datetime, timedelta, timezone as _tz

    conversation = Mock(id="conv-1")
    conversation.skill_state = {
        "food_scan": {"scan_id": "scan-1", "dish": dish},
        "food_correction": {
            "field": field,
            "scan_id": "scan-1",
            "dish": dish,
            "at": (datetime.now(_tz.utc) - timedelta(seconds=age_seconds)).isoformat(),
        },
    }
    return SkillContext(conversation=conversation, bot_user=Mock(), message_text=text)


class TestAnswerTurnIsClaimedNarrowly:
    """The skill registers above anketa/food_clarify/faq — a loose match here
    would shadow them, so every guard below is load-bearing."""

    @pytest.mark.parametrize("text", ["500", "500 г", "около 250 грамм"])
    def test_an_answer_shaped_reply_is_claimed(self, text: str) -> None:
        assert FoodCorrectionSkill().matches(_pending_context(text))

    @pytest.mark.parametrize(
        "text",
        [
            "а когда вы работаете в воскресенье?",
            "хочу записаться на маникюр",
            "",
        ],
    )
    def test_an_unrelated_turn_falls_through(self, text: str) -> None:
        skill = FoodCorrectionSkill()
        # Presence first: the very same pending context DOES claim a real answer,
        # so the negative below is about the text, not about a dead fixture.
        assert skill.matches(_pending_context("500"))

        assert not skill.matches(_pending_context(text))

    def test_a_stale_prompt_stops_claiming_text(self) -> None:
        """An unanswered prompt must not swallow a turn tomorrow."""
        skill = FoodCorrectionSkill()
        assert skill.matches(_pending_context("500", age_seconds=60))  # fresh: claimed

        assert not skill.matches(_pending_context("500", age_seconds=3600))

    def test_callbacks_still_win_over_the_answer_path(self) -> None:
        result = FoodCorrectionSkill().handle(_pending_context("cb:food:correct:name:scan-1"))
        assert result.action_type == "food_correction_prompt"

    def test_macros_shape_is_required_for_a_macros_prompt(self) -> None:
        skill = FoodCorrectionSkill()
        assert skill.matches(_pending_context("12/8/32", field="macros"))
        assert not skill.matches(_pending_context("12/8/32", field="name"))

    def test_a_refusal_is_claimed_even_without_the_answer_shape(self) -> None:
        assert FoodCorrectionSkill().matches(
            _pending_context("у меня непереносимость лактозы", field="grams")
        )

    def test_has_pending_correction_is_the_dispatcher_predicate(self) -> None:
        from apps.skills.food_correction.skill import has_pending_correction

        fresh = _pending_context("500").conversation
        stale = _pending_context("500", age_seconds=3600).conversation
        assert has_pending_correction(fresh) is True
        assert has_pending_correction(stale) is False
        assert has_pending_correction(Mock(id="no-state")) is False


class TestAnswerHandling:
    def test_unreadable_value_re_asks_instead_of_guessing(self) -> None:
        """A guessed correction is worse than none — it makes the NEXT card
        wrong in the person's name."""
        result = FoodCorrectionSkill().handle(_pending_context("99999"))
        assert result.reply_text == _PROMPTS_FOR_TEST["grams"]

    def test_refusal_is_acknowledged_without_claiming_to_remember(self) -> None:
        result = FoodCorrectionSkill().handle(
            _pending_context("у меня аллергия на орехи", field="name")
        )
        assert result.meta["reply_kind"] == "food_correction_refusal_not_stored"
        assert "не буду" in result.reply_text.lower()

    def test_a_failed_write_never_promises_memory(self) -> None:
        """No consent / no link / DB down: a soft ack, never «запомнила»."""
        result = FoodCorrectionSkill().handle(_pending_context("500"))
        assert result.action_data["stored"] is False
        assert "апомнила" not in result.reply_text


class TestTheNameAnswerIsNotAnyText:
    """Review DRF-1454: «any short text without digits» let «что я ел сегодня»
    be stored as the name of a dish AND took the turn away from the chip that
    owns it. A dish name is short, at most three words, and does not open with
    the vocabulary of asking for something."""

    @pytest.mark.parametrize(
        "text",
        ["Борщ", "куриная грудка", "плов узбекский", "Кофе без сахара"],
    )
    def test_a_dish_name_is_still_claimed(self, text: str) -> None:
        assert FoodCorrectionSkill().matches(_pending_context(text, field="name"))

    @pytest.mark.parametrize(
        "text",
        [
            "что я ел сегодня",  # CHIP_DIARY callback
            "стакан воды",  # CHIP_WATER callback
            "/anketa",  # CHIP_ANKETA callback
            "мой дневник",
            "Хочу записаться на стрижку",
            "Что вы умеете?",
            "Как до вас доехать",
            "Пройти анкету",
            "покажи что ты обо мне помнишь",
            "Спасибо большое!",
            "ок",
            "отмени запись",
        ],
    )
    def test_a_request_is_not_a_dish_name(self, text: str) -> None:
        skill = FoodCorrectionSkill()
        # Presence first: the same pending context DOES claim a real dish name.
        assert skill.matches(_pending_context("Борщ", field="name"))

        assert not skill.matches(_pending_context(text, field="name"))

    def test_a_two_line_message_is_not_an_answer_about_weight(self) -> None:
        skill = FoodCorrectionSkill()
        assert skill.matches(_pending_context("300"))

        assert not skill.matches(_pending_context("Ок\n300"))


class TestServiceWordsAreNotDishes:
    """Ревью DRF-1454, ось correctness, MUST_FIX_PRE_PILOT: ``_NOT_A_DISH_RE``
    не знал «найди|сотри|удали|отмена|помощь», и служебные просьбы на живом
    промпте «что было на фото?» распознавались как название блюда. «удали мои
    данные» и «сотри всё» — запросы по 152-ФЗ; записать их как блюдо — значит
    украсть ход у команды стирания."""

    @pytest.mark.parametrize(
        "text",
        [
            "найди мастера",
            "отмена",
            "помощь",
            "удали мои данные",
            "сотри всё",
            "запись к мастеру",
            "оставляем",  # ответ на «Оставляем?» — не блюдо (см. TestKeepOrChange)
        ],
    )
    def test_a_service_request_is_not_a_dish_name(self, text: str) -> None:
        skill = FoodCorrectionSkill()
        # Presence first: the same pending context DOES claim a real dish name.
        assert skill.matches(_pending_context("Борщ", field="name"))

        assert not skill.matches(_pending_context(text, field="name"))


class TestApproximateAndNegativeGrams:
    """Ревью DRF-1454, мелкие находки: «примерно 300» не распознавалось
    (префикс длиннее шести символов), а «-300» сохранялось как 300 г —
    ``\\d+`` не видит знак."""

    @pytest.mark.parametrize("text", ["примерно 300", "около 250 грамм", "где-то 400 г"])
    def test_a_longer_prefix_still_reads_as_a_weight_answer(self, text: str) -> None:
        assert FoodCorrectionSkill().matches(_pending_context(text))

    @pytest.mark.parametrize("text", ["-300", "−300"])
    def test_a_negative_number_is_not_a_portion(self, text: str) -> None:
        from apps.orchestrator.memory import food as food_memory

        assert (
            food_memory.parse_correction_value(food_memory.FIELD_GRAMS, text) is None
        )


class TestAnUnreadableAnswerKeepsTheQuestionOpen:
    """The re-ask used to clear the pending record first, so the bot asked a
    question it had stopped listening to and the person's next «300» fell
    through to the concierge — the exact loss this ticket exists to fix."""

    @pytest.mark.parametrize("text", ["0", "99999"])
    def test_out_of_range_re_asks_and_still_listens(self, text: str) -> None:
        skill = FoodCorrectionSkill()
        context = _pending_context(text)
        written: list = []
        with patch(
            "apps.conversations.services.write_skill_state",
            side_effect=lambda conv, key, value: written.append((key, value)),
        ):
            result = skill.handle(context)

        assert result.reply_text == _PROMPTS_FOR_TEST["grams"]
        # The record was refreshed, not dropped — a follow-up answer is heard.
        assert written and written[-1][1] is not None
        assert written[-1][1]["field"] == "grams"


class TestRollbackSwitchOnTheSkill:
    def test_flag_off_restores_the_pre_memory_skill(self, settings) -> None:
        skill = FoodCorrectionSkill()
        assert skill.matches(_pending_context("500"))  # presence: ON claims it

        settings.FOOD_SCANNER_MEMORY_ENABLED = False

        assert not skill.matches(_pending_context("500"))
        # The callback half keeps working — that is the pre-DRF-1454 behaviour.
        assert skill.matches(_context("cb:food:correct:grams:scan-1"))
