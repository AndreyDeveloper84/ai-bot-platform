"""Шаг «тип питания» в анкете — закрытый список владельца (DRF-2310, §77 п. 5–7).

До этого листа анкета тип питания НЕ спрашивала вовсе: в теле его не было, а
в каталоге столбец стоял со свободной строкой и умолчанием `"none"` — то есть
про каждого человека там лежало «что-то», чего он не говорил.

Состав списка назвал владелец 23.09.2026: «без ограничений · вегетарианство ·
веганство · кето · халяль · кошер · другое словами». Значения на проводе —
словарь каталога `users.UserPersonalContext.DietType`: один словарь на два
репозитория, второй разошёлся бы с ним молча (решение 24.09).

* e1 — место шага: последним, после цели и темпа; цель больше не замыкает;
* e2 — каждый ответ уходит в каталог своим значением, без пометки пропуска;
* e3 — «другое» спрашивает СЛОВА и уходит вместе с ними;
* e4 — «пропустить» → значения НЕТ, только `_skipped_fields == ["diet"]`;
  имя вопроса короткое, как у остальных пропусков;
* e5 — состояние, сохранённое до появления шага: при завершении анкета
  спрашивает питание, а не отправляет тело без него;
* e6 — стража: набор значений шага равен словарю каталога, и «пропустить»
  в этот набор не входит.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock, patch

import pytest

from apps.consent.personal_calculation import ConsentAttestation
from apps.skills.base import SkillContext
from apps.skills.nutrition_anketa.fsm import (
    CATALOG_DIET_TYPES,
    DIET_CHOICES,
    DIET_OTHER,
    DIET_SKIP,
    DIET_SKIP_WIRE_NAME,
    AnketaFSM,
    choice_keyboard_options,
)
from apps.skills.nutrition_anketa.skill import NutritionAnketaSkill
from apps.skills.nutrition_anketa.tests.test_skill import (
    _ATTESTATION_LOOKUP,
    _profile,
    _StatefulConversation,
)

_ATTESTATION = ConsentAttestation(
    type="personal_calculation", document_version="personal-calculation-v1"
)

_BODY = {
    "gender": "female",
    "age": 28,
    "height": 168,
    "weight": 62,
    "activity": "light",
}


def _state(current_step: str, answers: dict[str, Any]) -> dict:
    return {
        "nutrition_anketa": {
            "current_step": current_step,
            "answers": dict(answers),
            "is_complete": False,
        }
    }


class _Run:
    def __init__(self, state: dict | None = None) -> None:
        self.conversation = _StatefulConversation(state)
        self.captured: list[dict] = []
        client = Mock()

        async def _upsert(**kwargs):
            self.captured.append(kwargs)
            return _profile()

        client.upsert_profile = _upsert
        self._client = client
        self.skill = NutritionAnketaSkill()

    def turn(self, text: str):
        ctx = SkillContext(
            conversation=self.conversation,  # type: ignore[arg-type]
            bot_user=Mock(channel="max", channel_user_id="12345"),
            message_text=text,
        )
        with (
            patch(
                "apps.skills.nutrition_anketa.skill.get_nutrition_client",
                return_value=self._client,
            ),
            patch(_ATTESTATION_LOOKUP, return_value=_ATTESTATION),
        ):
            return self.skill.handle(ctx)

    @property
    def bucket(self) -> dict:
        return self.conversation.skill_state.get("nutrition_anketa") or {}


class TestE1TheStepIsLast:
    def test_the_goal_no_longer_closes_the_anketa(self) -> None:
        run = _Run(_state("goal", _BODY))

        result = run.turn("cb:anketa:choice:goal:maintain")

        assert result.action_type == "anketa_step_diet"
        assert run.bucket["current_step"] == "diet"
        assert run.captured == [], "в каталог ничего не ушло — вопрос ещё не задан"

    def test_after_the_pace_the_diet_follows(self) -> None:
        """Цель с темпом: сначала темп, и только потом питание."""
        run = _Run(_state("goal", _BODY))

        asked_pace = run.turn("cb:anketa:choice:goal:lose")
        assert asked_pace.action_type == "anketa_step_pace"
        asked_diet = run.turn("cb:anketa:choice:pace:gentle")

        assert asked_diet.action_type == "anketa_step_diet"


class TestE2EachAnswerTravelsAsItsOwnValue:
    @pytest.mark.parametrize("slug", [s for s in DIET_CHOICES if s not in (DIET_SKIP, DIET_OTHER)])
    def test_payload_carries_the_named_value(self, slug: str) -> None:
        run = _Run(_state("diet", {**_BODY, "goal": "maintain"}))

        done = run.turn(f"cb:anketa:choice:diet:{slug}")

        assert done.action_type == "anketa_complete"
        data = run.captured[0]["data"]
        assert data["diet_preference"] == slug
        assert "_skipped_fields" not in data
        assert "diet_note" not in data


class TestE3OtherAsksForWords:
    def test_the_words_are_asked_and_travel_with_the_answer(self) -> None:
        run = _Run(_state("diet", {**_BODY, "goal": "maintain"}))

        asked = run.turn(f"cb:anketa:choice:diet:{DIET_OTHER}")
        assert asked.action_type == "anketa_step_diet_note"
        assert run.captured == [], "«другое» без слов в каталог не уходит"

        done = run.turn("без лактозы")

        assert done.action_type == "anketa_complete"
        data = run.captured[0]["data"]
        assert data["diet_preference"] == DIET_OTHER
        assert data["diet_note"] == "без лактозы"


class TestE4SkipSendsNoValue:
    def test_only_the_skip_marker_with_the_short_name(self) -> None:
        run = _Run(_state("diet", {**_BODY, "goal": "maintain"}))

        done = run.turn(f"cb:anketa:choice:diet:{DIET_SKIP}")

        assert done.action_type == "anketa_complete"
        data = run.captured[0]["data"]
        assert data["_skipped_fields"] == [DIET_SKIP_WIRE_NAME]
        assert "diet_preference" not in data
        # Короткое имя — как у остальных пропусков (`weight` при столбце
        # `weight_kg`). Имя столбца здесь молча потеряло бы пропуск.
        assert DIET_SKIP_WIRE_NAME == "diet"


class TestE5AStateFromBeforeTheStep:
    def test_completing_without_diet_asks_it_instead_of_sending(self) -> None:
        """Состояние сериализовано до деплоя: стоит на цели, ответа про
        питание нет. Тело без него не уходит — иначе человек остался бы с
        неспрошенным вопросом и прежним значением в столбце."""
        run = _Run(_state("goal", _BODY))

        result = run.turn("cb:anketa:choice:goal:maintain")

        assert result.action_type == "anketa_step_diet"
        assert run.captured == []


class TestE6TheVocabularyIsTheCatalogsOwn:
    def test_the_choices_match_the_catalog_and_the_skip_is_not_one_of_them(self) -> None:
        assert set(DIET_CHOICES) == set(CATALOG_DIET_TYPES) | {DIET_SKIP}
        assert DIET_SKIP not in CATALOG_DIET_TYPES
        # «Пропустить» — не ответ «без ограничений»: первое — отсутствие
        # ответа, второе — ответ (решение владельца §77 п. 6).
        assert DIET_SKIP != "omnivore"

    def test_the_step_presents_a_choice_keyboard(self) -> None:
        options = choice_keyboard_options("diet")

        assert [slug for _label, slug in options] == list(DIET_CHOICES)
        assert list(AnketaFSM.STEPS)[-2:] == ["diet", "diet_note"]
