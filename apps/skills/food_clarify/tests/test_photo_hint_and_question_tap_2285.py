"""DRF-2285 / DRF-2287 — живой проход владельца 22.09 (DRF-2281, находки 1 и 3).

1. «Сфотографируем еду?» → консьерж: «Я не умею делать фото». Ответ целиком
   модели: ни её промпт, ни описания трёх инструментов еды не говорили, что
   фото распознаётся, — хотя фото без подписи и есть рабочий вход в сканер
   (``is_structured_nutrition_turn``). Теперь блок «Инструменты питания»
   промпта говорит об этом — по воротам фото (``FOOD_PHOTO_SCAN_ENABLED``):
   при выключенном распознавании звать прислать фото нельзя (ревью #1979).
3. «А есть вообще торт в справочнике?» → карточка «Это про еду?» и затем
   «Не нашла «а есть вообще торт в справочнике»». Модель приняла вопрос за
   запись; тап «📔 В дневник» оценил запомненную фразу-вопрос целиком. Теперь
   описание инструмента отделяет вопрос от записи, а тап по вопросу не
   оценивает его, а спрашивает, что было (``ASK_WHAT_TEXT``, текст прежний).

* p1 — промпт: при включённом фото — «пришли фото без подписи», при
  выключенном — «не обещай разобрать фото»; описание инструмента про фото
  молчит, про вопрос — говорит;
* q1 — тап по фразе-вопросу («?», «?)», «?!», «？»): оценки нет, вопрос
  «что было», ждём еду;
* q2 — следующий ответ едой оценивается как обычно;
* q3 — фраза без вопроса оценивается, как прежде (контроль).
"""

from __future__ import annotations

import pytest

from apps.orchestrator.concierge import (
    FOOD_PHOTO_OFF_PROMPT_LINE,
    FOOD_PHOTO_ON_PROMPT_LINE,
    _nutrition_tools_prompt_block,
)
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


class TestP1PhotoLineFollowsThePhotoGate:
    def test_photo_on_invites_a_photo(self, settings) -> None:
        settings.NUTRITION_ENABLED = True
        settings.FOOD_PHOTO_SCAN_ENABLED = True
        block = _nutrition_tools_prompt_block()
        assert FOOD_PHOTO_ON_PROMPT_LINE in block
        assert "распознаёт" in FOOD_PHOTO_ON_PROMPT_LINE
        assert FOOD_PHOTO_OFF_PROMPT_LINE not in block

    def test_photo_off_does_not_promise_recognition(self, settings) -> None:
        settings.NUTRITION_ENABLED = True
        settings.FOOD_PHOTO_SCAN_ENABLED = False
        block = _nutrition_tools_prompt_block()
        assert FOOD_PHOTO_OFF_PROMPT_LINE in block
        assert FOOD_PHOTO_ON_PROMPT_LINE not in block

    def test_the_tool_description_speaks_of_questions_not_photos(self) -> None:
        text = CLARIFY_FOOD_ENTRY_TOOL_SPEC["description"]
        assert "вопрос" in text.lower()
        assert "фото" not in text.lower()


class TestQ1QuestionTap:
    @pytest.mark.parametrize(
        "question", [QUESTION, "а торт есть?)", "торт есть?!", "торт есть？", "торт есть?  "]
    )
    def test_a_question_is_not_estimated(self, conversation, consent, question) -> None:  # noqa: F811
        text_entry.remember_source(_ctx(conversation, question), question)
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
