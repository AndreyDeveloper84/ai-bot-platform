"""Тексты про нижнюю границу по BMR (``bmr_floor``) в карточке анкеты.

Решение владельца «все по рекомендациям» (CD §72), тексты — дословно:

* на месте «Учла важное в анамнезе — ориентиры подобрала с поправкой на это.»
  при ``bmr_floor`` — «Ориентир не ниже безопасного минимума.»: это не
  анамнез, а граница расчёта;
* на месте «цель — поддерживать» в строке «Считала … от твоих данных» —
  «Твоя цель — снизить вес. Сейчас ориентир на поддержание: ниже безопасного
  минимума не опускаю.»: цель — названная человеком, а «поддержание» —
  расчётная, после ступени (каталог DRF-2241 хранит их порознь).

Ступень ``bmr_floor`` — единственное переопределение, которое Ayla выдаёт
вместе с расчётом (DRF-2222, ``nutrition_proactive/render.py``). Прочие
(беременность, кормление, РПП и никогда не выдававшийся ``bmi_floor``) —
прежний текст: census DRF-1840 держит его для ``bmi_floor``.
"""

from __future__ import annotations

from dataclasses import replace


from apps.skills.nutrition_anketa.skill import _format_summary, _method_and_inputs_line
from apps.skills.nutrition_anketa.tests.test_skill import _profile

FLOOR_REMARK = "Ориентир не ниже безопасного минимума."
FLOOR_GOAL = (
    "Твоя цель — снизить вес. Сейчас ориентир на поддержание: ниже безопасного минимума не опускаю."
)
OLD_REMARK = "Учла важное в анамнезе — ориентиры подобрала с поправкой на это."

SNAPSHOT = {
    "gender": "female",
    "age": 30,
    "height_cm": 160,
    "weight_kg": 40,
    "activity_coefficient": 1.2,
    "goal": "maintain",  # расчётная — после ступени bmr_floor
    "pace": "gentle",
}


def _floor_profile(name: str = "bmr_floor"):
    return replace(
        _profile(goal_overridden_by=name),
        goal="lose",  # названная человеком (DRF-2241)
        targets_method_versions={"calories": "mifflin_st_jeor_v2"},
        targets_input_snapshot=dict(SNAPSHOT),
    )


class TestFloorRemark:
    def test_the_floor_is_named_as_a_floor_not_as_history(self) -> None:
        text = _format_summary(_floor_profile())
        assert FLOOR_REMARK in text
        assert OLD_REMARK not in text

    def test_a_health_override_keeps_its_remark(self) -> None:
        """Контроль: беременность — по-прежнему «учла важное»."""
        text = _format_summary(_profile(goal_overridden_by="pregnancy"))
        assert OLD_REMARK in text
        assert FLOOR_REMARK not in text


class TestFloorGoalLine:
    def test_the_named_goal_and_the_floor_are_both_said(self) -> None:
        line = _method_and_inputs_line(_floor_profile())
        assert FLOOR_GOAL in line
        assert "цель — поддерживать" not in line  # расчётная цель не выдаётся за названную
        assert "вес — 40 кг" in line  # положительно: прочие входы на месте

    def test_without_the_floor_the_goal_fact_is_unchanged(self) -> None:
        """Контроль: без ступени «цель — …» печатается из снимка, как раньше."""
        profile = replace(
            _profile(),
            targets_method_versions={"calories": "mifflin_st_jeor_v2"},
            targets_input_snapshot={**SNAPSHOT, "goal": "maintain"},
        )
        line = _method_and_inputs_line(profile)
        assert "цель — поддерживать" in line
        assert FLOOR_GOAL not in line
