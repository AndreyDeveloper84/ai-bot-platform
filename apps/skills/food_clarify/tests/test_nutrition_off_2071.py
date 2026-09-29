"""DRF-2071 — ворота выключателя в ``FoodClarifySkill.handle`` стирают открытый вопрос.

Перепись по классу нашла ветки этого навыка, продолжавшие разговор о еде при
``NUTRITION_ENABLED=false``. Ворота встали в ``handle``; заглушка — половина
дела. Вторая половина — ``text_entry.forget``: вопрос, заданный ещё при ON
(«что было?», «сколько граммов?»), держит за навыком каждое следующее число
или название блюда до конца ``PENDING_TTL_SECONDS``. Без стирания человек при
OFF получал бы одну и ту же заглушку на «250», «омлет», «гречка» — вход в
контур через открытый вопрос прошлого сообщения (тот же класс, что закрыл
``food_correction`` коммитом ac2540e4).

Состояние здесь конструируется, а не проигрывается через каталог: ``_write``
пишет только в настоящий ``Conversation``, поэтому стирание доказывается
вызовом ``_write(conversation, None)``, а не пустым ``skill_state`` двойника.
Каждое отрицание — рядом с положительным контролем при ON.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from apps.skills.base import SkillContext
from apps.skills.food_clarify import text_entry
from apps.skills.food_clarify.skill import FoodClarifySkill
from apps.skills.food_clarify.tests.test_text_entry import _Catalogue

WRITE = "apps.skills.food_clarify.text_entry._write"


@pytest.fixture
def nutrition_off(settings):
    # Перекрывает autouse ``_nutrition_contour_on`` из conftest пакета.
    settings.NUTRITION_ENABLED = False


def _ctx(text: str, *, bucket: dict | None) -> SkillContext:
    """Разговор с открытым вопросом текстового ввода (или без него)."""
    state = {}
    if bucket is not None:
        state[text_entry.STATE_KEY] = {**bucket, "at": datetime.now(UTC).isoformat()}
    conversation = SimpleNamespace(id="conv-2071", skill_state=state)
    bot_user = Mock()
    bot_user.channel = "max"
    bot_user.channel_user_id = "2071"
    return SkillContext(conversation=conversation, bot_user=bot_user, message_text=text)  # type: ignore[arg-type]


#: (открытый вопрос при ON, ответ человека при OFF)
OPEN_QUESTIONS = [
    pytest.param({"expect_food": True}, "омлет", id="что-было→название"),
    pytest.param({"awaiting_grams": True, "dish": "борщ"}, "250", id="сколько-граммов→число"),
    pytest.param(
        {"awaiting_fix_grams": True, "entry_id": "e1"}, "180 г", id="исправить-граммы→число"
    ),
]


class TestAnAnswerToAnEarlierQuestionIsRefusedAndForgotten:
    @pytest.mark.parametrize(("bucket", "answer"), OPEN_QUESTIONS)
    def test_stub_and_the_question_is_erased(self, nutrition_off, bucket, answer):
        ctx = _ctx(answer, bucket=bucket)
        skill = FoodClarifySkill()
        # Ход всё ещё этого навыка — иначе «250» уехало бы модели.
        assert skill.matches(ctx), answer

        with patch(WRITE) as write:
            result = skill.handle(ctx)

        assert result.reply_text == text_entry.NUTRITION_OFF_TEXT
        assert result.meta["reply_kind"] == "food_text_nutrition_off"
        write.assert_called_once_with(ctx.conversation, None)

    def test_the_card_tap_without_its_phrase_is_refused_and_forgotten(self, nutrition_off):
        """«📔 В дневник» под карточкой, отрисованной при ON."""
        ctx = _ctx("cb:food:diary", bucket={"expect_food": True})
        with patch(WRITE) as write:
            result = FoodClarifySkill().handle(ctx)

        assert result.reply_text == text_entry.NUTRITION_OFF_TEXT
        write.assert_called_once_with(ctx.conversation, None)

    def test_free_food_text_is_refused_without_a_card(self, nutrition_off):
        """Свободный текст о еде при OFF — заглушка, не карточка «Это про еду?»."""
        ctx = _ctx("борщ 300г", bucket=None)
        catalogue = _Catalogue()
        with patch(
            "apps.skills.food_clarify.text_entry.get_nutrition_client", return_value=catalogue
        ):
            result = FoodClarifySkill().handle(ctx)

        assert result.reply_text == text_entry.NUTRITION_OFF_TEXT
        assert catalogue.estimates == []
        assert catalogue.logs == []


class TestPositiveControlTheQuestionStaysOpenWhenOn:
    @pytest.mark.parametrize(("bucket", "answer"), OPEN_QUESTIONS[:2])
    def test_the_answer_is_taken_as_an_answer(self, bucket, answer):
        ctx = _ctx(answer, bucket=bucket)
        catalogue = _Catalogue()
        with (
            patch(
                "apps.skills.food_clarify.text_entry.get_nutrition_client", return_value=catalogue
            ),
            patch(
                "apps.orchestrator.personal_surface.personal_records_consent_open",
                return_value=True,
            ),
            patch("apps.consent.nutrition.diary_is_granted", return_value=True),
        ):
            result = FoodClarifySkill().handle(ctx)

        assert result.reply_text != text_entry.NUTRITION_OFF_TEXT
        # Ответ пошёл в работу: оценка по справочнику запрошена.
        assert catalogue.estimates, result.reply_text
