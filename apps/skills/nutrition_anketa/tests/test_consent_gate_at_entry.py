"""Гейт согласия стоит НА ВХОДЕ в анкету, а не на завершении (§92).

До этой правки утверждение проверялось в `_on_complete` — после того как
человек назвал пол, возраст, рост, вес и цель, а FSM записала их в
`Conversation.skill_state`. Пять параметров из шести, перечисленных §92,
оказывались собраны и сохранены до появления основания их собирать.

§92 требует гейт на входе прямым текстом: «до второго согласия вопрос о
весе не задаётся». И довод оттуда же: гейт на шаге веса «исполнил бы
букву правила и оставил четыре параметра из шести собранными без
основания».

Прежняя проверка в `_on_complete` не снята — вторая линия: согласие может
быть отозвано между входом и завершением.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

from apps.skills.base import SkillContext
from apps.skills.nutrition_anketa import skill as anketa
from apps.skills.nutrition_anketa.skill import NutritionAnketaSkill

_IS_GRANTED = "apps.consent.personal_calculation.is_granted"
_GRANT = "apps.consent.personal_calculation.grant"


class _Conversation:
    def __init__(self) -> None:
        self.id = "conv-gate"
        self.skill_state: dict = {}

    def save(self, update_fields=None) -> None:
        return


def _ctx(text: str) -> tuple[SkillContext, _Conversation]:
    conversation = _Conversation()
    ctx = SkillContext(
        conversation=conversation,  # type: ignore[arg-type]
        bot_user=Mock(channel="max", channel_user_id="gate-1"),
        message_text=text,
    )
    return ctx, conversation


class TestTheEntranceIsGated:
    def test_without_consent_the_first_question_is_not_asked(self):
        """Главное утверждение среза: без согласия FSM НЕ стартует.

        Проверяется не текстом ответа, а тем, что в `skill_state` не
        появилось анкеты. Текст можно сочинить любой; отсутствие FSM —
        это отсутствие сбора параметров.
        """
        ctx, conversation = _ctx("cb:anketa:start")

        with patch(_IS_GRANTED, return_value=False):
            result = NutritionAnketaSkill().handle(ctx)

        assert result.reply_text == anketa.CONSENT_ASK
        assert "nutrition_anketa" not in conversation.skill_state
        assert result.action_type == "anketa_consent_ask"

    def test_the_ask_carries_the_version_and_two_actions(self):
        """Версия — в данных, а не в тексте: доказывать, на что человек
        соглашался, — наша работа, а не его память."""
        from apps.consent.personal_calculation import (
            PERSONAL_CALCULATION_DOCUMENT_VERSION,
        )

        ctx, _ = _ctx("/anketa")

        with patch(_IS_GRANTED, return_value=False):
            result = NutritionAnketaSkill().handle(ctx)

        assert result.action_data["document_version"] == PERSONAL_CALCULATION_DOCUMENT_VERSION
        callbacks = {b["callback"] for b in result.action_data["buttons"]}
        assert callbacks == {anketa.CONSENT_GRANT_CALLBACK, anketa.CONSENT_DECLINE_CALLBACK}

    def test_with_consent_the_first_question_is_asked(self):
        """Положительная стража: гейт закрывает НЕ всё.

        Без неё тест выше зеленел бы и на анкете, которая не стартует
        никогда, — то есть на закрытом расчёте вместо гейта.
        """
        ctx, conversation = _ctx("cb:anketa:start")

        with patch(_IS_GRANTED, return_value=True):
            result = NutritionAnketaSkill().handle(ctx)

        assert result.reply_text != anketa.CONSENT_ASK
        assert "nutrition_anketa" in conversation.skill_state
        assert result.action_type.startswith("anketa_step_")


class TestTheTwoActions:
    def test_grant_records_and_starts_the_questionnaire(self):
        """Согласие записано → анкета начинается тем же ходом."""
        ctx, conversation = _ctx(anketa.CONSENT_GRANT_CALLBACK)

        with patch(_GRANT, return_value=True) as grant, patch(_IS_GRANTED, return_value=True):
            result = NutritionAnketaSkill().handle(ctx)

        from apps.consent.personal_calculation import (
            PERSONAL_CALCULATION_DOCUMENT_VERSION,
        )

        grant.assert_called_once()
        assert grant.call_args.kwargs["document_version"] == PERSONAL_CALCULATION_DOCUMENT_VERSION
        assert "nutrition_anketa" in conversation.skill_state
        assert result.action_type.startswith("anketa_step_")

    def test_grant_that_cannot_be_read_back_does_not_start(self):
        """`grant` вернул False — записали, но перечитать не смогли.

        Анкета не начинается: собрать параметры и потом обнаружить, что
        основания нет, хуже, чем попросить повторить.
        """
        ctx, conversation = _ctx(anketa.CONSENT_GRANT_CALLBACK)

        with patch(_GRANT, return_value=False):
            result = NutritionAnketaSkill().handle(ctx)

        assert result.reply_text == anketa.CONSENT_RECORDED_BUT_UNREADABLE
        assert "nutrition_anketa" not in conversation.skill_state

    def test_decline_keeps_the_diary_and_starts_nothing(self):
        """§92: «отказ не закрывает дневник»."""
        ctx, conversation = _ctx(anketa.CONSENT_DECLINE_CALLBACK)

        with patch(_GRANT) as grant:
            result = NutritionAnketaSkill().handle(ctx)

        grant.assert_not_called()
        assert result.reply_text == anketa.CONSENT_DECLINED
        assert "nutrition_anketa" not in conversation.skill_state
        assert "дневник" in result.reply_text.lower()

    def test_both_callbacks_are_claimed_by_this_skill(self):
        """Иначе «не сейчас» уйдёт в общий конвейер и человек получит
        молчание или чужой ответ на своё «нет»."""
        skill = NutritionAnketaSkill()
        for cb in (anketa.CONSENT_GRANT_CALLBACK, anketa.CONSENT_DECLINE_CALLBACK):
            ctx, _ = _ctx(cb)
            assert skill.matches(ctx), cb


class TestTheTextsPromiseNothingThatDoesNotExist:
    def test_no_promise_of_a_settings_screen(self):
        """Экрана отзыва в боте нет — обещать дверь, которой нет, нельзя."""
        for text in (anketa.CONSENT_ASK, anketa.CONSENT_DECLINED):
            assert "настройк" not in text.lower()

    def test_no_claim_of_anonymity(self):
        """Параметры тела хранятся под учётной записью — называть это
        обезличиванием было бы неправдой."""
        for text in (anketa.CONSENT_ASK, anketa.CONSENT_DECLINED):
            low = text.lower()
            assert "аноним" not in low
            assert "обезлич" not in low

    def test_the_six_parameters_are_named(self):
        """Состав согласия — из §92 дословно: шесть, и ни одним больше."""
        low = anketa.CONSENT_ASK.lower()
        for word in ("вес", "рост", "возраст", "пол", "активност", "цел"):
            assert word in low, word
