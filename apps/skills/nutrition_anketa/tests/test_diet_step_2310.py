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
    def test_a_state_without_activity_asks_diet_once_not_twice(self) -> None:
        """Состояние сериализовано до появления шага активности: стоит на цели,
        активности нет. Страж скилла возвращает к активности ПОСЛЕ завершения,
        то есть уже за питанием, — и цепочка шагов провела бы человека через
        питание второй раз, если бы названный ответ не считался названным."""
        run = _Run(_state("goal", {k: v for k, v in _BODY.items() if k != "activity"}))

        asked_diet = run.turn("cb:anketa:choice:goal:maintain")
        assert asked_diet.action_type == "anketa_step_diet"

        asked_activity = run.turn("cb:anketa:choice:diet:vegan")
        assert asked_activity.action_type == "anketa_step_activity"
        assert run.captured == [], "тело без активности не уходит"

        asked_goal = run.turn("cb:anketa:choice:activity:light")
        assert asked_goal.action_type == "anketa_step_goal"

        done = run.turn("cb:anketa:choice:goal:maintain")

        # Питание НЕ спрашивается второй раз — ответ уже назван.
        assert done.action_type == "anketa_complete"
        assert run.captured[0]["data"]["diet_preference"] == "vegan"


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


class TestE7ARejectedAnswerNeverCompletesTheAnketa:
    def test_free_text_on_the_goal_step_is_re_asked_not_swallowed(self) -> None:
        """Отвергнутый ответ переспрашивается. Без отдельной проверки на
        отказ валидации ветка «питание уже названо» замыкала бы анкету
        ПРЕЖНИМ ответом: человек не слышал «выбери вариант», а видел
        карточку норм, посчитанную не по тому, что он сказал."""
        run = _Run(_state("goal", {**_BODY, "goal": "maintain", "diet": "vegan"}))

        result = run.turn("ну давай средний темп")

        assert result.action_type != "anketa_complete"
        assert run.captured == [], "в каталог ничего не ушло — ответ не принят"

    def test_a_rejected_answer_does_not_drop_the_pace(self) -> None:
        answers = {**_BODY, "goal": "lose", "pace": "gentle", "diet": "vegan"}
        run = _Run(_state("goal", answers))

        run.turn("что-то своё")

        assert run.bucket["answers"].get("pace") == "gentle"


class TestE8OtherWithoutWordsIsNotAnAnswer:
    def test_an_edit_before_the_words_asks_them_again(self) -> None:
        """«Другое» само по себе лишь обещает слова. Прерви человек шаг
        правкой прежнего ответа — и «другое» без слов уехало бы в каталог
        (тот такое тело отвергает) либо уронило бы сборку тела."""
        run = _Run(_state("diet", {**_BODY, "goal": "maintain"}))
        asked_words = run.turn(f"cb:anketa:choice:diet:{DIET_OTHER}")
        assert asked_words.action_type == "anketa_step_diet_note"

        run.turn("cb:anketa:edit:weight")
        run.turn("70")
        run.turn("cb:anketa:choice:activity:light")
        result = run.turn("cb:anketa:choice:goal:maintain")

        # Вопрос задаётся заново С САМОГО ТИПА, а не со слов: человек вправе
        # выбрать другое значение. Важное здесь — тело не ушло и сборка не
        # упала на отсутствующем ключе слов.
        assert result.action_type == "anketa_step_diet"
        assert run.captured == []

    def test_empty_words_are_re_asked(self) -> None:
        run = _Run(_state("diet_note", {**_BODY, "goal": "maintain", "diet": DIET_OTHER}))

        result = run.turn("   ")

        assert result.action_type != "anketa_complete"
        assert run.captured == []


class TestE9BothSkipsTravelTogether:
    def test_activity_and_diet_skips_are_both_named(self) -> None:
        """Пометки складываются в один список: одна затирала бы другую."""
        run = _Run(_state("activity", _BODY))
        run.turn("cb:anketa:choice:activity:unknown")
        run.turn("cb:anketa:choice:goal:maintain")

        done = run.turn(f"cb:anketa:choice:diet:{DIET_SKIP}")

        assert done.action_type == "anketa_complete"
        data = run.captured[0]["data"]
        assert data["_skipped_fields"] == ["activity", DIET_SKIP_WIRE_NAME]
        assert "activity_coefficient" not in data
        assert "diet_preference" not in data


class TestE10TheLabelDoesNotLandInHistory:
    def test_halal_and_kosher_name_a_belief_and_stay_out_of_the_chat_log(self) -> None:
        """«Халяль» и «Кошер» называют веру — спецкатегория 152-ФЗ наравне со
        здоровьем. Метка легла бы в историю как собственная реплика человека,
        а её на следующих ходах читает промпт консьержа. Тот же вывод, что у
        скрининга; сам ответ уезжает в каталог своим путём."""
        from apps.orchestrator.nutrition_global import resolve_anketa_tap

        tap = resolve_anketa_tap("cb:anketa:choice:diet:halal")

        assert tap is not None  # наличие: тап разобран, ветка решена
        assert tap.history_text is None

    def test_a_step_without_the_carve_out_still_lands(self) -> None:
        """Пара к предыдущему: без изъятия метка в историю идёт — значит узел
        проверяет изъятие, а не общую немоту разборщика."""
        from apps.orchestrator.nutrition_global import resolve_anketa_tap

        tap = resolve_anketa_tap("cb:anketa:choice:activity:light")

        assert tap is not None and tap.history_text == "Лёгкая активность"


class TestE11WordsAreNotAShortcut:
    def test_a_phrase_about_a_doctors_number_is_the_answer_to_the_step(self) -> None:
        """Ожидаемый ответ шага — фраза про еду и врачей. Вход ручного
        ориентира ловил её раньше шага и уводил человека из анкеты (тот же
        довод, что у веса: ревью #1912)."""
        run = _Run(_state("diet_note", {**_BODY, "goal": "maintain", "diet": DIET_OTHER}))

        done = run.turn("врач сказал без глютена")

        assert done.action_type == "anketa_complete"
        assert run.captured[0]["data"]["diet_note"] == "врач сказал без глютена"
