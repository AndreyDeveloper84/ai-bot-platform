"""Шаг «активность» в анкете питания — четыре коэффициента §85 (DRF-2102).

До этого листа анкета отправляла ``activity_coefficient=1.4`` всем — число,
которого нет в утверждённом наборе (решение владельца 09.09, таблица
«Коэффициенты активности»: 1.2 / 1.375 / 1.55 / 1.725), и каталог с #508
подводил его к ближайшему. Теперь человек отвечает сам:

* a1 — место шага: между весом и целью; цель по-прежнему замыкает;
  клавиатура — четыре варианта дословно из решения владельца плюс
  «Не знаю» последней;
* a2 — каждый из четырёх ответов уходит в каталог ровно своим числом,
  без ``_skipped_fields``;
* a3 — «Не знаю» → 1.375 **и** ``_skipped_fields == ["activity"]``; пара:
  выбранная «Лёгкая активность» — то же 1.375, но без пометки. Пропуск
  отличим от выбора с тем же числом;
* a4 — состояние, сохранённое до появления шага (стоит на цели, ответа
  про активность нет): при завершении анкета спрашивает активность, а не
  подставляет число за человека; цель переспрашивается. Это стража
  механизма — есть ли такие люди на стенде, здесь не утверждается;
* a5 — метка ответа ложится в историю чата той же таблицей, что и
  клавиатура (общий путь ``resolve_anketa_tap``);
* a6 — строка «Считала … от твоих данных» называет активность словом
  из таблицы, число — в скобках; незнакомое число (1.4 у старых
  профилей) печатается как есть;
* a7 — стража: в ``skill.py`` нет константы ``1.4``; набор коэффициентов
  шага равен набору §85.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from apps.consent.personal_calculation import ConsentAttestation
from apps.skills.base import SkillContext
from apps.skills.nutrition_anketa import skill as skill_module
from apps.skills.nutrition_anketa.fsm import (
    ACTIVITY_CHOICES,
    ACTIVITY_COEFFICIENTS,
    ACTIVITY_SKIP,
    CHOICE_STEPS,
    AnketaFSM,
    choice_keyboard_options,
)
from apps.skills.nutrition_anketa.skill import NutritionAnketaSkill
from apps.skills.nutrition_anketa.tests.test_skill import (
    _ATTESTATION_LOOKUP,
    _profile,
    _StatefulConversation,
)

#: Таблица решения владельца (AYLA_NUTRITION_TARGETS_ARCHITECTURE_DECISION.md,
#: «Коэффициенты активности») — дословно, в её порядке.
_OWNER_TABLE: list[tuple[str, float]] = [
    ("Почти нет активности", 1.2),
    ("Лёгкая активность", 1.375),
    ("Средняя активность", 1.55),
    ("Высокая активность", 1.725),
]

_ATTESTATION = ConsentAttestation(
    type="personal_calculation", document_version="personal-calculation-v1"
)


def _state(current_step: str, answers: dict[str, Any]) -> dict:
    return {
        "nutrition_anketa": {
            "current_step": current_step,
            "answers": dict(answers),
            "is_complete": False,
        }
    }


_BODY = {"gender": "female", "age": 28, "height": 168, "weight": 62}


class _Run:
    """Один скилл, одна беседа, перехваченный клиент каталога."""

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


class TestA1TheStepSitsBetweenWeightAndGoal:
    def test_order_and_keyboard(self) -> None:
        assert list(AnketaFSM.STEPS) == [
            "gender",
            "age",
            "screening",
            "height",
            "weight",
            "activity",
            "goal",
        ]
        assert AnketaFSM.STEPS["weight"].next == "activity"
        assert AnketaFSM.STEPS["activity"].next == "goal"
        assert "activity" in CHOICE_STEPS

        options = choice_keyboard_options("activity")
        labels = [label for label, _slug in options]
        # Четыре варианта — дословно и в порядке таблицы владельца; «Не знаю» — пятая, последняя.
        assert labels[:4] == [label for label, _ in _OWNER_TABLE]
        assert labels[4] == ACTIVITY_CHOICES[ACTIVITY_SKIP] and len(labels) == 5
        # Slug пропуска — не один из четырёх коэффициентов.
        assert ACTIVITY_SKIP not in ACTIVITY_COEFFICIENTS

    def test_the_weight_answer_asks_activity_and_renders_five_buttons(self) -> None:
        run = _Run(_state("weight", {"gender": "female", "age": 28, "height": 168}))
        result = run.turn("62")
        assert result.action_type == "anketa_step_activity"
        buttons = (result.action_data or {})["buttons"]
        assert len(buttons) == 5
        assert run.bucket["current_step"] == "activity"


class TestA2EachAnswerIsItsOwnCoefficient:
    @pytest.mark.parametrize(("label", "coefficient"), _OWNER_TABLE)
    def test_payload_carries_the_chosen_number(self, label: str, coefficient: float) -> None:
        slug = next(s for lbl, s in choice_keyboard_options("activity") if lbl == label)
        run = _Run(_state("activity", _BODY))
        asked_goal = run.turn(f"cb:anketa:choice:activity:{slug}")
        assert asked_goal.action_type == "anketa_step_goal"
        done = run.turn("cb:anketa:choice:goal:maintain")
        assert done.action_type == "anketa_complete"

        assert len(run.captured) == 1
        data = run.captured[0]["data"]
        assert data["activity_coefficient"] == coefficient
        assert "_skipped_fields" not in data


class TestA3SkipIsMarkedAndDistinctFromTheSameNumber:
    def test_unknown_is_1375_with_the_skip_marker(self) -> None:
        run = _Run(_state("activity", _BODY))
        run.turn(f"cb:anketa:choice:activity:{ACTIVITY_SKIP}")
        done = run.turn("cb:anketa:choice:goal:maintain")
        assert done.action_type == "anketa_complete"
        data = run.captured[0]["data"]
        assert data["activity_coefficient"] == 1.375
        assert data["_skipped_fields"] == ["activity"]

    def test_light_is_1375_without_the_marker(self) -> None:
        light = next(
            s for lbl, s in choice_keyboard_options("activity") if lbl == "Лёгкая активность"
        )
        run = _Run(_state("activity", _BODY))
        run.turn(f"cb:anketa:choice:activity:{light}")
        run.turn("cb:anketa:choice:goal:maintain")
        data = run.captured[0]["data"]
        assert data["activity_coefficient"] == 1.375
        assert "_skipped_fields" not in data


class TestA4AStateFromBeforeTheStepIsAskedNotDefaulted:
    def test_completing_without_activity_asks_activity_then_goal_again(self) -> None:
        # Сериализовано до деплоя: стоит на цели, активности в ответах нет.
        run = _Run(_state("goal", _BODY))
        result = run.turn("cb:anketa:choice:goal:maintain")
        assert result.action_type == "anketa_step_activity"
        assert run.captured == [], "в каталог ничего не ушло — числа за человека нет"
        assert run.bucket["current_step"] == "activity"
        assert run.bucket["is_complete"] is False

        # Ответил про активность — цель переспрашивается, затем расчёт.
        again = run.turn("cb:anketa:choice:activity:moderate")
        assert again.action_type == "anketa_step_goal"
        done = run.turn("cb:anketa:choice:goal:lose")
        assert done.action_type == "anketa_complete"
        data = run.captured[0]["data"]
        assert data["activity_coefficient"] == 1.55 and data["goal"] == "lose"
        assert "_skipped_fields" not in data


class TestA5TheLabelLandsInHistory:
    def test_history_text_is_the_keyboard_label(self) -> None:
        from apps.orchestrator.nutrition_global import resolve_anketa_tap

        tap = resolve_anketa_tap("cb:anketa:choice:activity:light")
        assert tap is not None and tap.history_text == "Лёгкая активность"
        # Пара: снятое значение — навигация, в историю ничего.
        gone = resolve_anketa_tap("cb:anketa:choice:activity:extreme")
        assert gone is not None and gone.history_text is None


class TestA6TheInputsLineNamesTheLevel:
    @staticmethod
    def _line(activity: float) -> str:
        profile = SimpleNamespace(
            targets_method_versions={"calories": "mifflin_st_jeor_v1"},
            targets_input_snapshot={
                "gender": "female",
                "age": 30,
                "height_cm": 168,
                "weight_kg": 62.0,
                "activity_coefficient": activity,
                "goal": "maintain",
            },
        )
        return skill_module._method_and_inputs_line(profile)

    def test_a_table_value_is_named(self) -> None:
        assert "активность — лёгкая активность (1.375)" in self._line(1.375)
        assert "активность — высокая активность (1.725)" in self._line(1.725)

    def test_an_unknown_value_is_printed_as_is(self) -> None:
        line = self._line(1.4)
        assert "активность — 1.4" in line and "(1.4)" not in line


class TestA7TheOldConstantIsGone:
    def test_no_1_4_literal_in_the_skill(self) -> None:
        source = Path(skill_module.__file__).read_text(encoding="utf-8")
        literals = [
            node.lineno
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and node.value == 1.4
        ]
        assert literals == [], f"1.4 в skill.py: строки {literals}"

    def test_the_step_coefficients_are_the_approved_set(self) -> None:
        # Зеркало каталожного ``ACTIVITY_COEFFICIENTS`` (nutrition_profile_service);
        # импортировать его отсюда нельзя — набор держится решением владельца.
        assert set(ACTIVITY_COEFFICIENTS.values()) == {1.2, 1.375, 1.55, 1.725}
        assert set(ACTIVITY_CHOICES) == set(ACTIVITY_COEFFICIENTS) | {ACTIVITY_SKIP}
