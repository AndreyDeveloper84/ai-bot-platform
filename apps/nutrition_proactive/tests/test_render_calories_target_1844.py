"""Ориентир калорий в отчёте дня — «из», «осталось», слово «ориентир» (DRF-1844, F1).

Каталог (#509) с этого листа шлёт ``calories_goal`` в сводке при
подтверждённом ориентире. Здесь — что с ним делает DM-отчёт:

* d1 — «Калории: N из M ккал.» при цели из сводки и настроенном профиле
  (положительная пара: без настроенного профиля — «Калории: N ккал.»);
* d2 — «До ориентира по калориям осталось N ккал.» — при цели и когда
  съедено меньше 70 % (иначе первой срабатывает «уложился»); арифметика,
  не оценка (§85 §8);
* d3 — при ``total ≥ goal`` реплика «осталось» не появляется; «уложился в
  ориентир» — новое слово (§82: «ориентир», не «норма»);
* c1 — перепись строк модуля: «норм» остаётся ТОЛЬКО в репликах о воде
  (решение владельца 11.09 §5.2: норма воды — мера шага) и в комментариях;
  в пользовательских строках про калории и белок — «ориентир».
"""

from __future__ import annotations

import ast
from pathlib import Path

from apps.nutrition_proactive import render
from apps.nutrition_proactive.tests.test_render import profile, summary, water


class TestTheCaloriesTargetInTheReport:
    def test_d1_the_goal_from_the_summary_is_the_second_number(self) -> None:
        text = render.render_daily_report(
            summary(calories_total=1500.0, calories_goal=2000), water(), profile()
        )
        assert "Калории: 1500 из 2000 ккал." in text

        # Положительная пара: сводка несёт число, но профиль не настроен —
        # второго числа нет (§103: происхождение решает профиль).
        bare = render.render_daily_report(
            summary(calories_total=1500.0, calories_goal=2000),
            water(),
            profile(targets_source="ayla_proposed"),
        )
        assert "Калории: 1500 ккал." in bare and "из 2000" not in bare

    def test_d2_remaining_kcal_is_named_when_far_from_the_target(self) -> None:
        remark = render.goal_remark(
            summary(calories_total=800.0, calories_goal=2000, protein_g=80.0),
            water(),
            profile(goal="maintain"),
        )
        assert remark == "До ориентира по калориям осталось 1200 ккал."

    def test_d3_within_the_band_says_the_target_word_and_no_remaining(self) -> None:
        remark = render.goal_remark(
            summary(calories_total=1500.0, calories_goal=1900, protein_g=80.0),
            water(),
            profile(goal="maintain"),
        )
        assert remark == "День уложился в ориентир из твоего профиля."
        over = render.goal_remark(
            summary(calories_total=2150.0, calories_goal=2000, protein_g=80.0),
            water(),
            profile(goal="maintain"),
        )
        assert "осталось" not in over and "норм" not in over

    def test_c1_the_word_norma_survives_only_for_water(self) -> None:
        """Перепись пользовательских строк ``render.py`` по AST: «норм» — только про воду."""
        source = Path(render.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        offenders: list[str] = []
        water_lines = 0
        # Куски f-строк считаются ЦЕЛИКОМ (иначе «До дневной нормы ещё » без
        # « мл.» читался бы как строка не про воду); докстринги (многострочные
        # константы) — не пользовательские строки, пропускаются.
        inside_fstrings: set[int] = set()
        texts: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                parts = [v for v in node.values if isinstance(v, ast.Constant)]
                inside_fstrings.update(id(v) for v in parts)
                texts.append((node.lineno, "".join(str(v.value) for v in parts)))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in inside_fstrings
            ):
                if "\n" not in node.value:
                    texts.append((node.lineno, node.value))
        for lineno, text in texts:
            low = text.lower()
            if "норм" not in low:
                continue
            if "вод" in low or "стакан" in low or "мл" in text:
                water_lines += 1
                continue
            offenders.append(f"{lineno}: {text!r}")
        assert water_lines >= 1  # положительная стража: водные строки на месте
        assert not offenders, "«норма» в строках про калории/белок: " + "; ".join(offenders)
