"""FoodCorrectionSkill tests (DRF-822 / Sprint 9 / P5)."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from apps.skills.base import SkillContext
from apps.skills.food_correction.skill import _PROMPTS as _PROMPTS_FOR_TEST
from apps.skills.food_correction.skill import FoodCorrectionSkill


def _context(text: str) -> SkillContext:
    conversation = Mock(id="conv-1")
    # The ✏️ button lives on a scanner card — the conversation carries it.
    conversation.skill_state = {"food_scan": {"scan_id": None, "dish": "борщ"}}
    return SkillContext(conversation=conversation, bot_user=Mock(), message_text=text)


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
    remembered: bool = False,
    known_value: object = None,
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
            "remembered": remembered,
            "value": known_value,
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
        from apps.orchestrator.memory import food as food_memory

        with patch(
            "apps.orchestrator.memory.food.remember_correction",
            return_value=food_memory.Outcome.NO_CONSENT,
        ):
            result = FoodCorrectionSkill().handle(_pending_context("плов", field="name"))
        assert result.action_data is not None
        assert result.action_data["stored"] is False
        assert "апомнила" not in result.reply_text


class TestGramsAndMacrosAreAcceptedNotStored:
    """Решение владельца 2026-09-04 (Q-NUTRITION-01, вариант А): вес и БЖУ НЕ
    пишутся в память до DRF-825 — дневник принадлежит Ayla, и две расходящиеся
    записи об одном приёме пищи нарушают ADR-0009 (правила 1 и 5). Ответ на
    такую правку — честный ack: значение принято, без «запомнила» и без
    «больше не спрошу». Хранится только имя блюда (см. TestKeepOrChange)."""

    def test_a_grams_answer_is_acknowledged_and_never_kept(self) -> None:
        from apps.orchestrator.memory import food as food_memory

        skill = FoodCorrectionSkill()
        written: list = []
        with (
            patch(
                "apps.orchestrator.memory.food.remember_correction",
                return_value=food_memory.Outcome.NOT_REMEMBERED,
            ) as remember,
            patch(
                "apps.conversations.services.write_skill_state",
                side_effect=lambda conv, key, value: written.append((key, value)),
            ),
        ):
            result = skill.handle(_pending_context("500"))

        assert (
            result.reply_text
            == "Поняла: 500 г. Вес пока не запоминаю — в следующий раз уточню снова."
        )
        assert result.action_data == {"field": "grams", "value": 500, "stored": False}
        assert "апомнила" not in result.reply_text
        assert "не спрошу" not in result.reply_text
        remember.assert_called_once()  # решение о владении живёт в memory, одно на всех
        assert written and written[-1][1] is None  # вопрос закрыт

    def test_a_macros_answer_is_acknowledged_and_never_kept(self) -> None:
        from apps.orchestrator.memory import food as food_memory

        with patch(
            "apps.orchestrator.memory.food.remember_correction",
            return_value=food_memory.Outcome.NOT_REMEMBERED,
        ) as remember:
            result = FoodCorrectionSkill().handle(_pending_context("12/8/32", field="macros"))

        assert (
            result.reply_text
            == "Поняла: БЖУ 12/8/32. Пока не запоминаю — в следующий раз уточню снова."
        )
        assert result.action_data == {"field": "macros", "value": "12/8/32", "stored": False}
        assert "апомнила" not in result.reply_text
        assert "переспрашивать" not in result.reply_text
        remember.assert_called_once()

    def test_a_name_answer_still_reaches_memory(self) -> None:
        """Положительная стража на тех же данных: имя по-прежнему пишется —
        «не пишем вес/БЖУ» не сломало запись имени."""
        from apps.orchestrator.memory import food as food_memory

        with patch(
            "apps.orchestrator.memory.food.remember_correction",
            return_value=food_memory.Outcome.WRITTEN,
        ) as remember:
            result = FoodCorrectionSkill().handle(_pending_context("плов", field="name"))

        remember.assert_called_once()
        assert result.action_data is not None
        assert result.action_data["stored"] is True
        assert "апомнила" in result.reply_text


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

        assert food_memory.parse_correction_value(food_memory.FIELD_GRAMS, text) is None


class TestKeepOrChange:
    """Ревью DRF-1454, ось correctness, MUST_FIX_PRE_PILOT: промпт «в прошлый
    раз было X» заканчивается «Оставляем?», но matches() требовал форму НОВОГО
    значения — «да»/«нет» уходили консьержу, а «Оставляем» на промпте про имя
    само сохранялось как название блюда. После Q-NUTRITION-01 (2026-09-04)
    «в прошлый раз» существует только для имени — вес и БЖУ не хранятся до
    DRF-825, и переспрашивать нечего."""

    @pytest.mark.parametrize("text", ["да", "Оставляем", "ок"])
    def test_a_confirmation_is_claimed_on_a_remembered_prompt(self, text: str) -> None:
        assert FoodCorrectionSkill().matches(
            _pending_context(text, field="name", remembered=True, known_value="плов")
        )

    def test_a_decline_is_claimed_on_a_remembered_prompt(self) -> None:
        assert FoodCorrectionSkill().matches(
            _pending_context("нет", field="name", remembered=True, known_value="плов")
        )

    @pytest.mark.parametrize("text", ["да", "нет", "Оставляем"])
    def test_no_value_remembered_means_nothing_to_keep(self, text: str) -> None:
        """На чистом вопросе «да»/«нет» — не ответ: ход идёт дальше по лестнице."""
        skill = FoodCorrectionSkill()
        # Положительная стража на тех же данных: с запомненным значением тот же
        # текст скилл забирает — то есть «не забрал» ниже отвечает за pending
        # без значения, а не за сломанный matches() вообще.
        assert skill.matches(
            _pending_context(text, field="name", remembered=True, known_value="плов")
        )
        assert not skill.matches(_pending_context(text, field="name"))

    def test_a_confirmation_keeps_the_stored_value_and_settles(self) -> None:
        skill = FoodCorrectionSkill()
        written: list = []
        with patch(
            "apps.conversations.services.write_skill_state",
            side_effect=lambda conv, key, value: written.append((key, value)),
        ):
            result = skill.handle(
                _pending_context("да", field="name", remembered=True, known_value="плов")
            )

        assert "плов" in result.reply_text
        assert "апомнила" not in result.reply_text  # ничего нового не писали
        assert result.meta["reply_kind"] == "food_correction_name_kept"
        assert written and written[-1][1] is None  # вопрос закрыт

    def test_a_confirmation_on_a_name_prompt_does_not_become_a_dish(self) -> None:
        """«Оставляем» на промпте про имя — худший случай находки."""
        result = FoodCorrectionSkill().handle(
            _pending_context(
                "Оставляем", field="name", remembered=True, known_value="Куриная грудка"
            )
        )
        assert "Куриная грудка" in result.reply_text
        assert result.meta["reply_kind"] == "food_correction_name_kept"

    def test_a_decline_reasks_with_the_plain_prompt_and_still_listens(self) -> None:
        skill = FoodCorrectionSkill()
        written: list = []
        with patch(
            "apps.conversations.services.write_skill_state",
            side_effect=lambda conv, key, value: written.append((key, value)),
        ):
            result = skill.handle(
                _pending_context("нет", field="name", remembered=True, known_value="плов")
            )

        assert result.reply_text == _PROMPTS_FOR_TEST["name"]
        # Pending обновлён, а не стёрт, и remembered снят — второе «нет»
        # не должно крутиться по кругу.
        assert written and written[-1][1] is not None
        assert written[-1][1]["remembered"] is False


class TestAnActiveAnketaKeepsItsAnswers:
    """Ревью DRF-1454, ось correctness, MUST_FIX_PRE_PILOT: ожидающая правка
    веса перехватывала числовые ответы анкеты. Шаги анкеты числовые (возраст
    14–90, рост 100–220, вес 30–200) и все попадают в диапазон парсера порции,
    а food_correction опрашивается раньше nutrition_anketa. Вход из находки:
    карточка «Борщ» → ✏️ → «вес» (молчим) → «Пройти анкету» → вопрос о росте
    → «170» → записывалось «порция „борщ“ — 170 г», ответ анкеты терялся."""

    def test_an_active_anketa_fsm_blocks_the_answer_path(self) -> None:
        skill = FoodCorrectionSkill()
        # Presence first: without an anketa the very same turn IS ours.
        assert skill.matches(_pending_context("170"))

        context = _pending_context("170")
        context.conversation.skill_state["nutrition_anketa"] = {"current_step": "height"}

        assert not skill.matches(context)

    def test_the_callback_path_is_unaffected_by_an_active_anketa(self) -> None:
        """cb:food:correct:* должен выигрывать у анкеты и дальше (порядок
        реестра несущий) — блокируется только свободный текст."""
        context = _pending_context("cb:food:correct:grams:scan-1")
        context.conversation.skill_state["nutrition_anketa"] = {"current_step": "height"}
        assert FoodCorrectionSkill().matches(context)


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


class TestATransientWriteFailureKeepsTheQuestionOpen:
    """Ревью DRF-1454, ось persistence, MUST_FIX_PRE_PILOT: pending «жду ответ»
    стирался ДО долговечной записи — отдельной, уже закоммиченной транзакцией.
    remember_correction → NO_IDENTITY (сетевой таймаут к Ayla — в пилоте
    штатная флуктуация) или ERROR означал: правка не записана, pending стёрт,
    следующее сообщение ушло в консьерж — ровно тот дефект, ради которого
    делалась задача. Соседняя ветка «не распарсилось» сделана правильно:
    pending обновляется, а не чистится. Здесь — так же. Актуально только для
    имени: вес и БЖУ до записи не доходят вовсе (Q-NUTRITION-01)."""

    @pytest.mark.parametrize("outcome_name", ["NO_IDENTITY", "ERROR"])
    def test_a_transient_failure_refreshes_pending_instead_of_clearing(
        self, outcome_name: str
    ) -> None:
        from apps.orchestrator.memory import food as food_memory

        outcome = getattr(food_memory.Outcome, outcome_name)
        written: list = []
        with (
            patch("apps.orchestrator.memory.food.remember_correction", return_value=outcome),
            patch(
                "apps.conversations.services.write_skill_state",
                side_effect=lambda conv, key, value: written.append((key, value)),
            ),
        ):
            result = FoodCorrectionSkill().handle(_pending_context("плов", field="name"))

        # Вопрос остаётся открытым — следующий ход повторит запись.
        assert written and written[-1][1] is not None
        assert "апомнила" not in result.reply_text
        assert "Поняла, учла" not in result.reply_text  # ничего не сохранено — не врём

    def test_a_terminal_outcome_settles_the_question(self) -> None:
        """NO_CONSENT/CAP_REACHED не лечатся повтором — вопрос закрывается."""
        from apps.orchestrator.memory import food as food_memory

        written: list = []
        with (
            patch(
                "apps.orchestrator.memory.food.remember_correction",
                return_value=food_memory.Outcome.NO_CONSENT,
            ),
            patch(
                "apps.conversations.services.write_skill_state",
                side_effect=lambda conv, key, value: written.append((key, value)),
            ),
        ):
            result = FoodCorrectionSkill().handle(_pending_context("плов", field="name"))

        assert written and written[-1][1] is None
        assert result.reply_text == "Поняла, учла."


class TestAStaleCardIsAnsweredHonestly:
    """Ревью DRF-1454, мелкая находка: ответ терялся молча, если карточка
    устарела (сканировали второе фото) или отрисована до релиза — pending
    писался всегда, dish пустой → «Поняла, учла», ничего не сохранено."""

    def test_the_prompt_is_not_asked_when_there_is_no_card(self) -> None:
        conversation = Mock(id="conv-no-card")
        conversation.skill_state = {}  # карточки сканера нет / она устарела
        context = SkillContext(
            conversation=conversation,
            bot_user=Mock(),
            message_text="cb:food:correct:grams:scan-9",
        )
        written: list = []
        with patch(
            "apps.conversations.services.write_skill_state",
            side_effect=lambda conv, key, value: written.append((key, value)),
        ):
            result = FoodCorrectionSkill().handle(context)

        assert "карточк" in result.reply_text.lower()
        assert "Поняла, учла" not in result.reply_text
        assert not written  # вопрос, ответ которому некуда писать, не задаётся

    def test_an_answer_on_a_cardless_pending_is_not_a_silent_loss(self) -> None:
        """Pending, записанный до исправления (dish пустой), получает честный
        ответ вместо «Поняла, учла»."""
        result = FoodCorrectionSkill().handle(_pending_context("500", dish=""))

        assert "Поняла, учла" not in result.reply_text
        assert "карточк" in result.reply_text.lower()


class TestRollbackSwitchOnTheSkill:
    def test_flag_off_restores_the_pre_memory_skill(self, settings) -> None:
        skill = FoodCorrectionSkill()
        assert skill.matches(_pending_context("500"))  # presence: ON claims it

        settings.FOOD_SCANNER_MEMORY_ENABLED = False

        assert not skill.matches(_pending_context("500"))
        # The callback half keeps working — that is the pre-DRF-1454 behaviour.
        assert skill.matches(_context("cb:food:correct:grams:scan-1"))

    def test_flag_off_writes_no_pending_record(self, settings) -> None:
        """Мелкая находка ревью: тумблер отката не выключал запись pending —
        в skill_state копилась мёртвая запись при выключенной фиче."""
        settings.FOOD_SCANNER_MEMORY_ENABLED = False
        written: list = []
        with patch(
            "apps.conversations.services.write_skill_state",
            side_effect=lambda conv, key, value: written.append((key, value)),
        ):
            result = FoodCorrectionSkill().handle(_context("cb:food:correct:grams:scan-1"))

        assert result.reply_text == _PROMPTS_FOR_TEST["grams"]  # чистый вопрос, как прежде
        assert not written
