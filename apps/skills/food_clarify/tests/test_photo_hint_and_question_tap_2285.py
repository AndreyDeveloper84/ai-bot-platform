"""DRF-2285 / DRF-2287 — живой проход владельца 22.09 (DRF-2281, находки 1 и 3).

1. «Сфотографируем еду?» → консьерж: «Я не умею делать фото». Ответ целиком
   модели: ни её промпт, ни описания трёх инструментов еды не говорили, что
   фото распознаётся, — хотя фото без подписи и есть рабочий вход в сканер
   (``is_structured_nutrition_turn``), а приветствие S5 зовёт прислать фото.
   Теперь описание ``clarify_food_entry`` говорит модели об этом прямо.
3. «А есть вообще торт в справочнике?» → карточка «Это про еду?» и затем
   «Не нашла «а есть вообще торт в справочнике»». Модель приняла вопрос за
   запись; тап «📔 В дневник» оценил запомненную фразу-вопрос целиком. Теперь
   описание инструмента отделяет вопрос от записи, а тап по вопросу не
   оценивает его, а спрашивает, что было (``ASK_WHAT_TEXT``, текст прежний).

* p1 — описание ``clarify_food_entry`` говорит про фото в чат и про вопрос;
* q1 — тап по фразе-вопросу: оценки нет, вопрос «что было», ждём еду;
* q2 — следующий ответ едой оценивается как обычно;
* q3 — фраза без вопроса оценивается, как прежде (контроль).
"""

from __future__ import annotations

import pytest

from apps.orchestrator.nutrition_global import CLARIFY_FOOD_ENTRY_TOOL_SPEC
from apps.skills.food_clarify import text_entry
from apps.skills.food_clarify.tests.test_text_entry import (  # noqa: F401 — фикстуры по имени
    _Catalogue,
    _ctx,
    _turn,
    consent,
    conversation,
)

QUESTION = "А есть вообще торт в справочнике?"


class TestP1ToolDescription:
    def test_the_model_is_told_photos_are_recognised(self) -> None:
        text = CLARIFY_FOOD_ENTRY_TOOL_SPEC["description"]
        assert "фото" in text.lower()
        assert "распознаёт" in text

    def test_the_model_is_told_a_question_is_not_an_entry(self) -> None:
        text = CLARIFY_FOOD_ENTRY_TOOL_SPEC["description"]
        assert "вопрос" in text.lower()


class TestQ1QuestionTap:
    def test_a_question_is_not_estimated(self, conversation, consent) -> None:  # noqa: F811
        text_entry.remember_source(_ctx(conversation, QUESTION), QUESTION)
        catalogue = _Catalogue()

        result = _turn(conversation, "cb:food:diary", catalogue)

        assert result.reply_text == text_entry.ASK_WHAT_TEXT
        assert conversation.skill_state["food_text"]["expect_food"] is True
        assert catalogue.estimates == []

    def test_then_food_is_estimated(self, conversation, consent) -> None:  # noqa: F811
        text_entry.remember_source(_ctx(conversation, QUESTION), QUESTION)
        catalogue = _Catalogue()
        _turn(conversation, "cb:food:diary", catalogue)

        result = _turn(conversation, "медовик 150 г", catalogue)

        assert result.action_type == "food_text_estimate_card"
        assert catalogue.estimates == [{"dish_name": "медовик", "portion_g": 150.0}]


class TestQ3ControlAPhraseIsStillEstimated:
    @pytest.mark.parametrize("phrase", ["борщ 300г", "торт наполеон"])
    def test_a_food_phrase_is_estimated(self, conversation, consent, phrase) -> None:  # noqa: F811
        text_entry.remember_source(_ctx(conversation, phrase), phrase)
        catalogue = _Catalogue()

        _turn(conversation, "cb:food:diary", catalogue)

        assert len(catalogue.estimates) == 1
