"""DRF-2124 — ``within_target_count`` у ``log_food`` в DTO Plan Lite.

Каталог (#515) кладёт к ``done_count`` ещё один ФАКТ: дни ведра, когда
сумма калорий ≤ подтверждённого ориентира; без ориентира — ``null``, не 0
(§103). Парсер несёт его как ``int | None`` и ничего не выводит.

* w1 — целое едет целым; ``null`` — ``None``; ключа нет (старый каталог,
  вода, бронь) — ``None``;
* w2 — мусор (строка, bool, отрицательное) — ``None``: поле про факт, а не
  про «что-то прислали»;
* w3 — В-5 перепись ключей: набор полей DTO назван точно, и результат-
  подобные ключи (``percent``, ``achieved``, ``within_target_percent``)
  по-прежнему умирают в парсере.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import httpx
import pytest

from apps.integrations.ayla.tests.test_wellness_context_client_plan_lite_2101 import (
    PLAN_LITE_WIRE,
    _client,
    _doc,
)
from apps.integrations.ayla.wellness_context_client import PlanLiteAction

_EXT = "bot:max:pl-2124"


def _wire(**food_extra: Any) -> dict:
    wire = json.loads(json.dumps(PLAN_LITE_WIRE))
    wire["actions"][0].update(food_extra)
    return wire


def _food(wire: dict) -> PlanLiteAction:
    ctx = _client(lambda _r: httpx.Response(200, json=_doc(wire))).get_wellness_context(
        external_user_id=_EXT
    )
    assert ctx.plan_lite is not None
    return next(a for a in ctx.plan_lite.actions if a.action_type == "log_food")


class TestW1TheFactTravels:
    def test_an_integer_is_carried(self) -> None:
        assert _food(_wire(within_target_count=2)).within_target_count == 2

    def test_zero_is_zero_not_none(self) -> None:
        assert _food(_wire(within_target_count=0)).within_target_count == 0

    def test_null_is_none(self) -> None:
        assert _food(_wire(within_target_count=None)).within_target_count is None

    def test_absent_key_is_none(self) -> None:
        assert _food(_wire()).within_target_count is None

    def test_other_actions_have_none(self) -> None:
        ctx = _client(
            lambda _r: httpx.Response(200, json=_doc(_wire(within_target_count=1)))
        ).get_wellness_context(external_user_id=_EXT)
        assert ctx.plan_lite is not None
        book = next(a for a in ctx.plan_lite.actions if a.action_type == "book_service")
        assert book.within_target_count is None


class TestW2GarbageIsNone:
    @pytest.mark.parametrize(
        "value", ["3", True, -1, 2.5, {"n": 3}], ids=["str", "bool", "neg", "float", "obj"]
    )
    def test_non_count_values_are_none(self, value: Any) -> None:
        assert _food(_wire(within_target_count=value)).within_target_count is None


class TestW3KeyRewrite:
    def test_the_dto_fields_are_exactly_the_commitment_and_the_facts(self) -> None:
        """В-5: поле добавлено ОДНО и названо; процентов и «достигнуто» у DTO
        полей нет — новое поле не открывает дверь остальным."""
        assert {f.name for f in dataclasses.fields(PlanLiteAction)} == {
            "action_type",
            "cadence",
            "target_count",
            "done_count",
            "bucket_start",
            "bucket_end",
            "within_target_count",
        }

    def test_result_looking_keys_still_die_in_the_parser(self) -> None:
        wire = _wire(within_target_count=2, within_target_percent=40, percent=33, achieved=True)
        wire["achieved"] = True
        food = _food(wire)
        assert food.within_target_count == 2
        rendered = str(food)
        assert "percent" not in rendered and "achieved" not in rendered
