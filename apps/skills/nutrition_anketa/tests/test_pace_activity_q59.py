"""Темп и активность спрашиваются, а не подставляются (CD §72, вопрос 59).

Решение владельца: темп и активность не подставлять; спрашивать в анкете;
без ответа — «не хватает данных» с именем поля. До правки бот:

* темп не спрашивал вовсе — каталог подставлял ``moderate``;
* на «Не знаю» про активность слал ``1.375`` с пометкой пропуска — число за
  человека, пусть и помеченное.

Темп нужен расчёту только при цели «похудеть» / «набрать»: при
«поддержать» поправка нулевая, и спрашивать его значит спрашивать то, что
расчёт не использует.

* p1 — «похудеть» → следующий вопрос — темп; ответ уходит в каталог;
* p2 — «поддержать» → анкета завершается, темпа в теле нет;
* p3 — «Не знаю» про активность → числа в теле нет, пропуск назван
  (каталог отвечает «не хватает данных: активность»);
* p4 — «обнови вес» при цели «похудеть» несёт темп из снимка; снимок без
  темпа → в анкету, темп не подставляется.

Узлы ревью:

* r1 — кнопка ДРУГОГО шага (старая «Средняя активность» в истории) на
  вопросе о темпе — не ответ: slug «moderate» у них общий, и без проверки
  темп записался бы невыбранным;
* r2 — отказ каталога «не хватает данных» назван словами и в верном числе;
  если у человека есть хоть один ориентир (ручная вода), карточка его не
  прячет.
"""

from __future__ import annotations

from dataclasses import replace

from apps.skills.nutrition_anketa.fsm import ACTIVITY_SKIP
from apps.skills.nutrition_anketa.skill import _format_summary
from apps.skills.nutrition_anketa.tests.test_skill import _profile
from apps.skills.nutrition_anketa.tests.test_activity_step_2102 import _BODY, _Run, _state
from apps.skills.nutrition_anketa.tests.test_update_weight_2139 import (
    _SNAPSHOT,
    _calculated,
)
from apps.skills.nutrition_anketa.tests.test_update_weight_2139 import _Run as _WeightRun

_ANSWERED = {**_BODY, "activity": "light"}


class TestP1LoseAsksPace:
    def test_lose_asks_pace_then_sends_it(self) -> None:
        run = _Run(_state("goal", _ANSWERED))
        asked = run.turn("cb:anketa:choice:goal:lose")
        assert asked.action_type == "anketa_step_pace"
        assert run.captured == []

        done = run.turn("cb:anketa:choice:pace:gentle")
        assert done.action_type == "anketa_complete"
        assert run.captured[0]["data"]["pace"] == "gentle"

    def test_gain_asks_pace_too(self) -> None:
        run = _Run(_state("goal", _ANSWERED))
        assert run.turn("cb:anketa:choice:goal:gain").action_type == "anketa_step_pace"


class TestP2MaintainDoesNotAskPace:
    def test_maintain_completes_without_pace(self) -> None:
        run = _Run(_state("goal", _ANSWERED))
        done = run.turn("cb:anketa:choice:goal:maintain")
        assert done.action_type == "anketa_complete"
        data = run.captured[0]["data"]
        # Присутствие: тело ушло — отсутствие ниже про темп.
        assert data["goal"] == "maintain"
        assert "pace" not in data


class TestP3UnknownActivitySendsNoNumber:
    def test_unknown_sends_no_coefficient_and_names_the_skip(self) -> None:
        """До вопроса 59 тест a3 пинил «Не знаю → 1.375» — умолчание, которое
        владелец отменил (§72): числа за человека нет, каталог скажет «не
        хватает данных»."""
        run = _Run(_state("activity", _BODY))
        run.turn(f"cb:anketa:choice:activity:{ACTIVITY_SKIP}")
        done = run.turn("cb:anketa:choice:goal:maintain")
        assert done.action_type == "anketa_complete"
        data = run.captured[0]["data"]
        assert data["_skipped_fields"] == ["activity"]
        assert "activity_coefficient" not in data


class TestP4UpdateWeightCarriesPace:
    def test_lose_snapshot_pace_rides_with_the_new_weight(self) -> None:
        snapshot = {**_SNAPSHOT, "goal": "lose", "pace": "gentle"}
        run = _WeightRun(profile=_calculated(snapshot=snapshot))
        run.turn("мой вес 65")
        assert len(run.posted) == 1
        assert run.posted[0]["data"]["pace"] == "gentle"

    def test_lose_snapshot_without_pace_goes_to_the_anketa(self) -> None:
        snapshot = {**_SNAPSHOT, "goal": "lose"}
        run = _WeightRun(profile=_calculated(snapshot=snapshot))
        result = run.turn("мой вес 65")
        assert run.posted == []
        assert result.meta["reply_kind"] == "anketa_update_weight_need_anketa"

    def test_maintain_snapshot_needs_no_pace(self) -> None:
        run = _WeightRun(profile=_calculated(snapshot=dict(_SNAPSHOT)))
        run.turn("мой вес 65")
        assert len(run.posted) == 1
        assert "pace" not in run.posted[0]["data"]


class TestR1ATapOfAnotherStepIsNotAnAnswer:
    def test_old_activity_button_on_the_pace_question_is_not_a_pace(self) -> None:
        run = _Run(_state("goal", _ANSWERED))
        assert run.turn("cb:anketa:choice:goal:lose").action_type == "anketa_step_pace"

        again = run.turn("cb:anketa:choice:activity:moderate")
        # Вопрос о темпе задан снова, в каталог ничего не ушло.
        assert again.action_type == "anketa_step_pace"
        assert run.captured == []
        assert "pace" not in run.bucket["answers"]

        done = run.turn("cb:anketa:choice:pace:gentle")
        assert done.action_type == "anketa_complete"
        assert run.captured[0]["data"]["pace"] == "gentle"


def _refused(fields: list[str], **over):
    values = {
        "targets_source": "none",
        "daily_kcal": 0,
        "protein_g": 0,
        "fat_g": 0,
        "carbs_g": 0,
        "water_ml": 0,
        "raw": {"overrides_applied": [{"reason": "insufficient_inputs", "fields": fields}]},
        **over,
    }
    return replace(_profile(), **values)


class TestR2TheRefusalNamesWhatIsMissing:
    def test_one_missing_input_is_named(self) -> None:
        text = _format_summary(_refused(["activity_coefficient"]))
        assert "не хватает данных — активность" in text
        assert "этот вопрос" in text

    def test_several_missing_inputs_are_named_in_the_plural(self) -> None:
        text = _format_summary(_refused(["pace", "activity_coefficient"]))
        assert "темп, активность" in text
        assert "эти вопросы" in text

    def test_a_manual_water_target_is_not_hidden(self) -> None:
        text = _format_summary(
            _refused(["activity_coefficient"], water_ml=2000, targets_source="user_entered")
        )
        # Присутствие: ручная вода на карточке — а общий отказ её не прячет.
        assert "2000" in text
        assert "не хватает данных" not in text
