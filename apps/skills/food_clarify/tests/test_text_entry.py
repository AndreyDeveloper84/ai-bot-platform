"""DRF-1837 — текстовый ввод еды: оценка → «Я распознала так» → подтверждение → запись.

Решение владельца §109 (10.09.2026): оценка показывается со словами
«примерно»/«оценка», в дневник попадает только после подтверждения;
§136 (11.09): происхождение ``text_estimated_confirmed`` /
``text_user_corrected``. Замер 10.09: записать еду было нечем ни одним из
трёх входов — этот путь и есть «дневник работает».

Каталог здесь подменён записывающим двойником: предмет — что бот зовёт,
когда и с чем, а не арифметика справочника (её держит каталог,
``nutrition/tests/test_internal_food_estimate.py``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from apps.integrations.ayla import DishEstimate, FoodLogResponse, FoodNotRecognizedError
from apps.skills.base import SkillContext
from apps.skills.food_clarify import text_entry
from apps.skills.food_clarify.skill import CARD_TEXT, FoodClarifySkill


class _Catalogue:
    """Записывающий двойник каталога: оценка по справочнику 100 г = 50 ккал."""

    def __init__(self, *, not_found: bool = False) -> None:
        self.estimates: list[dict[str, Any]] = []
        self.logs: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self.deletes: list[dict[str, Any]] = []
        self.restores: list[dict[str, Any]] = []
        self.not_found = not_found

    async def estimate_dish(self, *, external_user_id, dish_name, portion_g=None):
        self.estimates.append({"dish_name": dish_name, "portion_g": portion_g})
        if self.not_found:
            raise FoodNotRecognizedError("dish_not_found")
        grams = 100.0 if portion_g is None else float(portion_g)
        factor = grams / 100.0
        return DishEstimate(
            matched_dish=dish_name,
            portion_g=grams,
            portion_estimated=portion_g is None,
            kcal=50.0 * factor,
            protein_g=2.0 * factor,
            fat_g=3.0 * factor,
            carbs_g=4.0 * factor,
            raw={},
        )

    async def log_meal(self, **kwargs):
        self.logs.append(kwargs)
        return FoodLogResponse(
            log_id=self.log_id,
            dish_name=kwargs["dish_name"],
            meal_type=kwargs["meal_type"],
            calories=50.0 * kwargs["portion_multiplier"],
            raw={},
        )

    # DRF-1838 — правка / удаление / возврат сохранённой записи.
    refuse: Exception | None = None
    log_id = "log-1"

    async def update_meal(self, **kwargs):
        self.updates.append(kwargs)
        if self.refuse is not None:
            raise self.refuse
        return FoodLogResponse(
            log_id=kwargs["log_id"],
            dish_name="борщ",
            meal_type="other",
            calories=50.0 * kwargs["portion_multiplier"],
            raw={"entry_origin": "text_user_corrected"},
        )

    async def delete_meal(self, **kwargs):
        from apps.integrations.ayla import MealDeletion

        self.deletes.append(kwargs)
        if self.refuse is not None:
            raise self.refuse
        # DRF-2108 — окно с провода: 15 минут от «сейчас», как у каталога.
        from datetime import datetime, timedelta, timezone

        expires = datetime.now(timezone.utc) + timedelta(minutes=15)
        return MealDeletion(log_id=kwargs["log_id"], restore_window_expires_at=expires.isoformat())

    async def restore_meal(self, **kwargs):
        self.restores.append(kwargs)
        if self.refuse is not None:
            raise self.refuse
        return FoodLogResponse(
            log_id=kwargs["log_id"],
            dish_name="борщ",
            meal_type="other",
            calories=150.0,
            raw={"entry_origin": "text_estimated_confirmed"},
        )


@pytest.fixture(autouse=True)
def _nutrition_on(settings):
    settings.NUTRITION_ENABLED = True


@pytest.fixture
def consent():
    """PERSONAL_DATA и согласие дневника из реестра — по каноническим адресам
    предикатов (DRF-2093: ворота текста зовут единый ``diary_write_refusal``)."""
    with (
        patch(
            "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=True
        ) as p,
        patch("apps.consent.nutrition.diary_is_granted", return_value=True),
    ):
        yield p


@pytest.fixture
def conversation():
    return SimpleNamespace(id="conv-1837", skill_state={})


def _ctx(conversation, text: str) -> SkillContext:
    bot_user = Mock()
    bot_user.channel = "max"
    bot_user.channel_user_id = "1837"
    return SkillContext(conversation=conversation, bot_user=bot_user, message_text=text)


def _turn(conversation, text: str, catalogue: _Catalogue):
    skill = FoodClarifySkill()
    ctx = _ctx(conversation, text)
    assert skill.matches(ctx), text
    with patch("apps.skills.food_clarify.text_entry.get_nutrition_client", return_value=catalogue):
        return skill.handle(ctx)


class TestParse:
    @pytest.mark.parametrize(
        ("text", "dish", "grams"),
        [
            ("борщ 300г", "борщ", 300.0),
            ("съела гречка 200 г", "гречка", 200.0),
            ("на обед омлет", "омлет", None),
            ("Сырники, 150 грамм", "сырники", 150.0),
        ],
    )
    def test_named_grams_only_when_named(self, text, dish, grams) -> None:
        parsed = text_entry.parse_food_text(text)
        assert parsed == text_entry.ParsedFood(dish=dish, grams=grams)

    @pytest.mark.parametrize("text", ["", "cb:food:diary", "борщ 5000 г", "борщ 5 г"])
    def test_unreadable_or_out_of_range_is_none(self, text) -> None:
        assert text_entry.parse_food_text(text) is None


class TestHappyPath:
    def test_phrase_then_diary_tap_shows_estimate_and_writes_nothing(
        self, conversation, consent
    ) -> None:
        catalogue = _Catalogue()
        card = _turn(conversation, "борщ 300г", catalogue)
        assert card.reply_text == CARD_TEXT  # «это про еду?» — прежняя защита от опечаток

        result = _turn(conversation, "cb:food:diary", catalogue)

        assert catalogue.estimates == [{"dish_name": "борщ", "portion_g": 300.0}]
        assert result.action_type == "food_text_estimate_card"
        assert result.reply_text.startswith("Я распознала так: борщ.")
        assert "Порция — 300 г, по твоим словам." in result.reply_text
        assert (
            "Примерно 150 ккал · Б 6 · Ж 9 · У 12 — оценка по справочнику блюд."
            in result.reply_text
        )
        assert [b["callback"] for b in result.action_data["buttons"]] == [
            "cb:food:text_log",
            "cb:food:text_grams",
            "cb:food:text_reject",
        ]
        # §109 шаг 6: до подтверждения — ни одной записи.
        assert catalogue.logs == []

    def test_confirm_logs_once_with_origin_and_unnamed_meal_type(
        self, conversation, consent
    ) -> None:
        catalogue = _Catalogue()
        _turn(conversation, "борщ 300г", catalogue)
        _turn(conversation, "cb:food:diary", catalogue)
        token = conversation.skill_state["food_text"]["token"]

        result = _turn(conversation, "cb:food:text_log", catalogue)

        assert len(catalogue.logs) == 1
        logged = catalogue.logs[0]
        assert logged["dish_name"] == "борщ"
        assert logged["portion_multiplier"] == 3.0
        assert logged["meal_type"] == "other"
        assert logged["entry_origin"] == "text_estimated_confirmed"
        assert logged["idempotency_key"] == f"food-text:bot:max:1837:{token}"
        assert result.reply_text == "Записала в дневник: борщ — 150 ккал."
        assert "food_text" not in conversation.skill_state

    def test_no_grams_is_shown_as_an_estimate(self, conversation, consent) -> None:
        catalogue = _Catalogue()
        _turn(conversation, "омлет", catalogue)
        result = _turn(conversation, "cb:food:diary", catalogue)
        assert (
            "Порция — примерно 100 г, это оценка: граммов в сообщении не было." in result.reply_text
        )
        _turn(conversation, "cb:food:text_log", catalogue)
        assert catalogue.logs[0]["portion_multiplier"] == 1.0


class TestCorrection:
    def test_corrected_grams_re_estimate_and_log_as_user_corrected(
        self, conversation, consent
    ) -> None:
        catalogue = _Catalogue()
        _turn(conversation, "борщ 300г", catalogue)
        _turn(conversation, "cb:food:diary", catalogue)

        prompt = _turn(conversation, "cb:food:text_grams", catalogue)
        assert prompt.reply_text == text_entry.GRAMS_PROMPT

        card = _turn(conversation, "250", catalogue)  # число — ответ на вопрос бота
        assert catalogue.estimates[-1] == {"dish_name": "борщ", "portion_g": 250.0}
        assert "Порция — 250 г, по твоим словам." in card.reply_text

        _turn(conversation, "cb:food:text_log", catalogue)
        assert catalogue.logs[0]["portion_multiplier"] == 2.5
        assert catalogue.logs[0]["entry_origin"] == "text_user_corrected"

    def test_unreadable_grams_keep_asking_and_write_nothing(self, conversation, consent) -> None:
        catalogue = _Catalogue()
        _turn(conversation, "борщ 300г", catalogue)
        _turn(conversation, "cb:food:diary", catalogue)
        _turn(conversation, "cb:food:text_grams", catalogue)
        result = _turn(conversation, "5", catalogue)
        assert result.reply_text == text_entry.GRAMS_UNREADABLE
        assert conversation.skill_state["food_text"]["awaiting_grams"] is True
        assert catalogue.logs == []


class TestRefusals:
    def test_reject_writes_nothing(self, conversation, consent) -> None:
        catalogue = _Catalogue()
        _turn(conversation, "борщ 300г", catalogue)
        _turn(conversation, "cb:food:diary", catalogue)
        result = _turn(conversation, "cb:food:text_reject", catalogue)
        assert result.reply_text == text_entry.REJECTED_TEXT
        assert catalogue.logs == []

    def test_a_stale_card_is_not_logged(self, conversation, consent) -> None:
        catalogue = _Catalogue()
        _turn(conversation, "борщ 300г", catalogue)
        _turn(conversation, "cb:food:diary", catalogue)
        old = datetime.now(timezone.utc) - timedelta(seconds=text_entry.PENDING_TTL_SECONDS + 5)
        conversation.skill_state["food_text"]["at"] = old.isoformat()
        result = _turn(conversation, "cb:food:text_log", catalogue)
        assert result.reply_text == text_entry.STALE_TEXT
        assert catalogue.logs == []

    def test_dish_not_in_reference_is_named_and_not_logged(self, conversation, consent) -> None:
        catalogue = _Catalogue(not_found=True)
        _turn(conversation, "зыбзик 100г", catalogue)
        result = _turn(conversation, "cb:food:diary", catalogue)
        assert result.reply_text == text_entry.NOT_FOUND_TEXT.format(dish="зыбзик")
        assert catalogue.logs == []

    def test_no_personal_data_consent_no_estimate_no_log(self, conversation) -> None:
        catalogue = _Catalogue()
        with patch(
            "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=False
        ):
            _turn(conversation, "борщ 300г", catalogue)
            result = _turn(conversation, "cb:food:diary", catalogue)
        assert result.reply_text == text_entry.CONSENT_TEXT
        assert catalogue.estimates == []
        assert catalogue.logs == []

    def test_nutrition_off_no_estimate(self, conversation, consent, settings) -> None:
        settings.NUTRITION_ENABLED = False
        catalogue = _Catalogue()
        _turn(conversation, "борщ 300г", catalogue)
        result = _turn(conversation, "cb:food:diary", catalogue)
        assert result.reply_text == text_entry.NUTRITION_OFF_TEXT
        assert catalogue.estimates == []


class TestAskWhenThePhraseIsMissing:
    def test_tap_without_phrase_asks_then_typed_food_is_estimated_directly(
        self, conversation, consent
    ) -> None:
        catalogue = _Catalogue()
        ask = _turn(conversation, "cb:food:diary", catalogue)
        assert ask.reply_text == text_entry.ASK_WHAT_TEXT

        card = _turn(conversation, "омлет 150 г", catalogue)
        assert card.action_type == "food_text_estimate_card"
        assert catalogue.estimates == [{"dish_name": "омлет", "portion_g": 150.0}]


class TestRouting:
    def test_pending_grams_make_a_plain_number_structured(self, conversation, consent) -> None:
        from apps.orchestrator.nutrition_global import is_structured_nutrition_turn

        catalogue = _Catalogue()
        _turn(conversation, "борщ 300г", catalogue)
        _turn(conversation, "cb:food:diary", catalogue)
        # POSITIVE first: a tap on the card is structured by prefix — the
        # predicate is alive; only a bare number is not ours yet.
        assert is_structured_nutrition_turn(
            text="cb:food:text_log", has_attachments=False, conversation=conversation
        )
        assert not is_structured_nutrition_turn(
            text="250", has_attachments=False, conversation=conversation
        )
        _turn(conversation, "cb:food:text_grams", catalogue)
        assert is_structured_nutrition_turn(
            text="250", has_attachments=False, conversation=conversation
        )

    def test_every_text_tap_has_a_history_label(self) -> None:
        from apps.orchestrator.nutrition_global import resolve_food_tap

        for payload in sorted(text_entry.TEXT_CALLBACKS):
            tap = resolve_food_tap(payload)
            assert tap is not None, payload
            assert tap.history_text, payload

    def test_keyboard_callbacks_are_the_ones_the_skill_owns(self) -> None:
        from apps.orchestrator.ui.keyboards import food_text_estimate_keyboard

        assert {b["callback"] for b in food_text_estimate_keyboard()} == text_entry.TEXT_CALLBACKS


LOG_ID = "0b6f3c2e-9d1a-4c55-8e2f-1838aaaa0001"


def _ayla_error(name: str, code: str) -> Exception:
    import apps.integrations.ayla as ayla

    return getattr(ayla, name)(code)


class TestSavedEntryChips:
    """DRF-1838 (F4) — §109 шаг 7: сохранённую запись можно исправить или удалить.

    Каталог (``PATCH/DELETE internal/food-log/<id>/`` и ``…/restore/``)
    подменён двойником: предмет — что бот показывает под записью, что зовёт по
    тапу и что говорит на каждый отказ. Правила пересчёта и окна держит
    каталог (``nutrition/tests/test_internal_food_log_edit.py``).
    """

    def _logged(self, conversation, catalogue):
        _turn(conversation, "борщ 300г", catalogue)
        _turn(conversation, "cb:food:diary", catalogue)
        return _turn(conversation, "cb:food:text_log", catalogue)

    def test_the_saved_entry_carries_fix_and_delete_chips_with_its_id(
        self, conversation, consent
    ) -> None:
        catalogue = _Catalogue()

        result = self._logged(conversation, catalogue)

        assert result.reply_text == "Записала в дневник: борщ — 150 ккал."
        assert [b["callback"] for b in result.action_data["buttons"]] == [
            "cb:food:entry_fix:log-1",
            "cb:food:entry_del:log-1",
        ]

    def test_delete_then_restore_within_the_window(self, conversation, consent) -> None:
        catalogue = _Catalogue()

        deleted = _turn(conversation, f"cb:food:entry_del:{LOG_ID}", catalogue)

        assert catalogue.deletes == [{"external_user_id": "bot:max:1837", "log_id": LOG_ID}]
        assert deleted.reply_text == "Убрала запись из дневника. Вернуть можно ещё 15 минут."
        assert [b["callback"] for b in deleted.action_data["buttons"]] == [
            f"cb:food:entry_undo:{LOG_ID}"
        ]

        restored = _turn(conversation, f"cb:food:entry_undo:{LOG_ID}", catalogue)

        assert catalogue.restores == [{"external_user_id": "bot:max:1837", "log_id": LOG_ID}]
        assert restored.reply_text == "Вернула в дневник: борщ — 150 ккал."
        assert [b["callback"] for b in restored.action_data["buttons"]] == [
            f"cb:food:entry_fix:{LOG_ID}",
            f"cb:food:entry_del:{LOG_ID}",
        ]

    def test_restore_after_the_window_says_it_is_final(self, conversation, consent) -> None:
        catalogue = _Catalogue()
        catalogue.refuse = _ayla_error("MealRestoreExpiredError", "restore_window_expired")

        result = _turn(conversation, f"cb:food:entry_undo:{LOG_ID}", catalogue)

        # POSITIVE first: the catalogue was asked — the refusal is its answer.
        assert catalogue.restores == [{"external_user_id": "bot:max:1837", "log_id": LOG_ID}]
        assert result.reply_text == (
            "Уже не вернуть: окно возврата закрылось, запись удалена окончательно."
        )
        assert "buttons" not in (result.action_data or {})

    @pytest.mark.parametrize(
        ("refusal", "text"),
        [
            ("MealNotFoundError", "Этой записи уже нет в дневнике."),
            ("MealEditConflictError", "Эту запись ведёт учёт воды — её убирает отмена стакана."),
            (
                "NutritionUnavailableError",
                "Дневник сейчас не отвечает — ничего не изменила. Попробуй через минуту.",
            ),
        ],
    )
    def test_each_refusal_of_a_delete_is_named(self, conversation, consent, refusal, text) -> None:
        catalogue = _Catalogue()
        catalogue.refuse = _ayla_error(refusal, "refused")

        result = _turn(conversation, f"cb:food:entry_del:{LOG_ID}", catalogue)

        assert len(catalogue.deletes) == 1
        assert result.reply_text == text

    def test_fix_grams_asks_then_patches_the_portion(self, conversation, consent) -> None:
        catalogue = _Catalogue()

        prompt = _turn(conversation, f"cb:food:entry_fix:{LOG_ID}", catalogue)
        assert prompt.reply_text == (
            "Сколько граммов было на самом деле? Напиши число — пересчитаю запись."
        )
        assert catalogue.updates == []

        result = _turn(conversation, "250", catalogue)

        assert catalogue.updates == [
            {"external_user_id": "bot:max:1837", "log_id": LOG_ID, "portion_multiplier": 2.5}
        ]
        assert result.reply_text == "Исправила: борщ — теперь 125 ккал."
        assert [b["callback"] for b in result.action_data["buttons"]] == [
            f"cb:food:entry_fix:{LOG_ID}",
            f"cb:food:entry_del:{LOG_ID}",
        ]
        assert "food_text" not in conversation.skill_state

    def test_an_out_of_range_answer_patches_nothing(self, conversation, consent) -> None:
        catalogue = _Catalogue()
        _turn(conversation, f"cb:food:entry_fix:{LOG_ID}", catalogue)

        result = _turn(conversation, "5000", catalogue)

        # POSITIVE first: the answer was ours — it got the grams hint, not the concierge.
        assert result.reply_text == text_entry.GRAMS_UNREADABLE
        assert catalogue.updates == []

    def test_without_consent_nothing_is_edited_or_restored(self, conversation) -> None:
        catalogue = _Catalogue()
        with patch(
            "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=False
        ):
            fix = _turn(conversation, f"cb:food:entry_fix:{LOG_ID}", catalogue)
            undo = _turn(conversation, f"cb:food:entry_undo:{LOG_ID}", catalogue)

        assert fix.reply_text == text_entry.CONSENT_TEXT
        assert undo.reply_text == text_entry.CONSENT_TEXT
        assert catalogue.updates == []
        assert catalogue.restores == []


class TestEntryTapHistoryH2:
    """OD-WATER-TAP-HISTORY (H2, владелец не ответил): как тап правки своей записи
    ложится в историю диалога. Одна точка выбора —
    ``nutrition_global.EDIT_TAP_HISTORY``: «silence» (вариант б, рекомендован,
    по умолчанию) или «phrase» (вариант а, подпись кнопки). Сырой payload —
    никогда: тап обязан быть распознан, иначе обработчик запишет ``cb:…``.
    """

    ENTRY_PAYLOADS = (
        f"cb:food:entry_fix:{LOG_ID}",
        f"cb:food:entry_del:{LOG_ID}",
        f"cb:food:entry_undo:{LOG_ID}",
    )

    def test_by_default_the_tap_is_recognised_and_silent(self) -> None:
        from apps.orchestrator import nutrition_global

        assert nutrition_global.EDIT_TAP_HISTORY == "silence"
        for payload in self.ENTRY_PAYLOADS:
            tap = nutrition_global.resolve_food_tap(payload)
            # POSITIVE first: recognised — it will not fall through to the raw-payload writer.
            assert tap is not None, payload
            assert tap.history_text is None, payload

    def test_phrase_mode_writes_the_button_label_never_the_payload(self, monkeypatch) -> None:
        from apps.orchestrator import nutrition_global
        from apps.orchestrator.ui.keyboards import (
            food_text_deleted_keyboard,
            food_text_logged_keyboard,
        )

        monkeypatch.setattr(nutrition_global, "EDIT_TAP_HISTORY", "phrase")
        labels = {
            b["callback"]: b["label"]
            for b in (*food_text_logged_keyboard(LOG_ID), *food_text_deleted_keyboard(LOG_ID))
        }
        assert set(labels) == set(self.ENTRY_PAYLOADS)
        for payload, label in labels.items():
            tap = nutrition_global.resolve_food_tap(payload)
            assert tap is not None, payload
            assert tap.history_text == label
            assert not tap.history_text.startswith("cb:")

    def test_an_unknown_mode_falls_back_to_silence(self, monkeypatch) -> None:
        from apps.orchestrator import nutrition_global

        monkeypatch.setattr(nutrition_global, "EDIT_TAP_HISTORY", "payload")
        tap = nutrition_global.resolve_food_tap(self.ENTRY_PAYLOADS[0])
        assert tap is not None
        assert tap.history_text is None


class TestEntryDecisionsAndEdges:
    """DRF-1838, ревью: решения, которые код принимает молча, и края.

    Два первых теста сторожат решения, у которых не было ни одного теста:
    удаление своей записи не требует согласия; выключенный дневник отказывает
    на все три тапа. Остальные — края, найденные ревью.
    """

    def test_delete_does_not_need_consent(self, conversation) -> None:
        catalogue = _Catalogue()
        with patch(
            "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=False
        ):
            result = _turn(conversation, f"cb:food:entry_del:{LOG_ID}", catalogue)

        assert len(catalogue.deletes) == 1
        assert result.reply_text == "Убрала запись из дневника. Вернуть можно ещё 15 минут."

    def test_nutrition_off_refuses_every_entry_tap(self, conversation, consent, settings) -> None:
        settings.NUTRITION_ENABLED = False
        catalogue = _Catalogue()

        replies = [
            _turn(conversation, f"cb:food:{action}:{LOG_ID}", catalogue).reply_text
            for action in ("entry_fix", "entry_del", "entry_undo")
        ]

        assert replies == [text_entry.NUTRITION_OFF_TEXT] * 3
        assert catalogue.updates == []
        assert catalogue.deletes == []
        assert catalogue.restores == []

    def test_an_uncertain_outcome_does_not_claim_nothing_changed(
        self, conversation, consent
    ) -> None:
        catalogue = _Catalogue()
        catalogue.refuse = _ayla_error("NutritionUncertainOutcomeError", "network: ReadTimeout")

        result = _turn(conversation, f"cb:food:entry_del:{LOG_ID}", catalogue)

        assert len(catalogue.deletes) == 1
        assert result.reply_text == (
            "Не знаю, дошло ли: дневник не ответил вовремя. "
            "Загляни в дневник, прежде чем повторять."
        )

    @pytest.mark.parametrize("pending", ["_food_correction_pending", "_anketa_fsm_active"])
    def test_fix_is_refused_while_another_question_would_take_the_number(
        self, conversation, consent, pending
    ) -> None:
        catalogue = _Catalogue()
        with patch(f"apps.orchestrator.nutrition_global.{pending}", return_value=True):
            result = _turn(conversation, f"cb:food:entry_fix:{LOG_ID}", catalogue)

        assert result.reply_text == (
            "Сначала закончим вопрос, который уже открыт, — потом исправлю граммы."
        )
        assert "food_text" not in conversation.skill_state

    def test_a_consent_refusal_clears_the_pending_fix(self, conversation, consent) -> None:
        catalogue = _Catalogue()
        _turn(conversation, f"cb:food:entry_fix:{LOG_ID}", catalogue)
        assert conversation.skill_state["food_text"]["awaiting_fix_grams"] is True

        with patch(
            "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=False
        ):
            result = _turn(conversation, "250", catalogue)

        assert result.reply_text == text_entry.CONSENT_TEXT
        assert "food_text" not in conversation.skill_state
        assert catalogue.updates == []

    def test_an_id_too_long_for_a_button_gets_no_chips(self, conversation, consent) -> None:
        catalogue = _Catalogue()
        catalogue.log_id = "x" * 46  # cb:food:entry_undo: (19) + 46 = 65 байт > 64

        _turn(conversation, "борщ 300г", catalogue)
        _turn(conversation, "cb:food:diary", catalogue)
        result = _turn(conversation, "cb:food:text_log", catalogue)

        # POSITIVE first: the entry was written and said so.
        assert result.reply_text == "Записала в дневник: борщ — 150 ккал."
        assert result.action_data["log_id"] == "x" * 46
        assert "buttons" not in result.action_data

    def test_a_long_id_tap_still_obeys_the_history_choice(self) -> None:
        from apps.orchestrator import nutrition_global

        tap = nutrition_global.resolve_food_tap("cb:food:entry_fix:" + "y" * 60)

        assert tap is not None
        assert tap.history_text is None
