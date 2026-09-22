"""«Обнови вес» спрашивает про прежние умолчания, а не переносит их (DRF-2279, CD §76 №32).

Решение владельца: «старые значения пометить как legacy_default и
запросить подтверждение, а не считать ответами человека». Каталог метит
темп и активность, подставленные до вопроса 59 (#543), и отдаёт их именами
в ``legacy_default_inputs``; помеченный вход для расчёта — «не назван».

«Обнови вес» (DRF-2139) шлёт входы снимка и названный темп профиля — то
есть переносил бы помеченные значения молча, а у каталога присланное
значение и есть подтверждение: бот подтвердил бы за человека. Поэтому при
пометках бот держит вес и сначала спрашивает — по вопросу на вход,
активность, потом темп, — и только потом шлёт вес с НАЗВАННЫМИ ответами.

Анкета заново спрашивает темп и активность всегда — там ответ названный
сам по себе, и отдельный вопрос не нужен.

* l1 — помечен темп: вопрос темпа, POST нет; «Средний» → один POST с
  ``pace: moderate`` и новым весом;
* l2 — помечена активность: вопрос активности с четырьмя ответами и «Не
  знаю»; ответ → POST с названным коэффициентом; «Не знаю» → POST с
  пропуском, без числа (как в анкете, вопрос 59);
* l3 — помечены оба: сначала активность, потом темп, один POST с обоими;
* l4 — пометок нет: путь как был — сразу POST, вопроса нет;
* l5 — клиент читает ``legacy_default_inputs``; ключа нет — пометок нет
  (бот выходит раньше каталога);
* l6 — текст вместо кнопки: тот же вопрос снова, POST нет;
* l7 — «Отмена» снимает вопрос, POST нет.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest

from apps.integrations.ayla.nutrition_client import NutritionClient
from apps.skills.nutrition_anketa.skill import UPDATE_WEIGHT_CANCEL_CALLBACK
from apps.skills.nutrition_anketa.tests.test_update_weight_2139 import (
    _SNAPSHOT,
    _calculated,
    _callbacks,
    _Run,
)

_LOSE = {**_SNAPSHOT, "goal": "lose", "pace": "moderate"}

CB_PACE = "cb:anketa:uw_pace:"
CB_ACTIVITY = "cb:anketa:uw_activity:"


def _marked(*names: str, snapshot: dict = _LOSE):
    return replace(_calculated(snapshot=snapshot), legacy_default_inputs=tuple(names))


def _sent(run: _Run) -> dict:
    assert len(run.posted) == 1, run.posted
    data = dict(run.posted[0]["data"])
    data.pop("consent", None)
    return data


class TestL1Pace:
    def test_asks_then_sends_the_named_pace(self) -> None:
        run = _Run(profile=_marked("pace"))
        asked = run.turn("мой вес 65")

        assert run.posted == []
        assert asked.meta["reply_kind"] == "anketa_update_weight_confirm_pace"
        assert _callbacks(asked)[:2] == [f"{CB_PACE}moderate", f"{CB_PACE}gentle"]

        card = run.turn(f"{CB_PACE}moderate")
        body = _sent(run)
        assert body["pace"] == "moderate"
        assert body["weight_kg"] == 65
        assert body["goal"] == "lose"
        assert card.meta["reply_kind"] == "anketa_update_weight_proposed"
        assert run.state is None


class TestL2Activity:
    def test_a_named_level_is_sent_as_its_coefficient(self) -> None:
        run = _Run(profile=_marked("activity_coefficient", snapshot=_SNAPSHOT))
        asked = run.turn("мой вес 65")

        assert run.posted == []
        assert asked.meta["reply_kind"] == "anketa_update_weight_confirm_activity"
        assert _callbacks(asked)[:5] == [
            f"{CB_ACTIVITY}sedentary",
            f"{CB_ACTIVITY}light",
            f"{CB_ACTIVITY}moderate",
            f"{CB_ACTIVITY}high",
            f"{CB_ACTIVITY}unknown",
        ]
        run.turn(f"{CB_ACTIVITY}moderate")
        body = _sent(run)
        assert body["activity_coefficient"] == 1.55
        assert body["weight_kg"] == 65

    def test_dont_know_is_a_skip_not_a_number(self) -> None:
        run = _Run(profile=_marked("activity_coefficient", snapshot=_SNAPSHOT))
        run.turn("мой вес 65")
        run.turn(f"{CB_ACTIVITY}unknown")

        body = _sent(run)
        assert body["_skipped_fields"] == ["activity"]
        assert "activity_coefficient" not in body
        assert body["weight_kg"] == 65


class TestL3Both:
    def test_activity_then_pace_then_one_post(self) -> None:
        run = _Run(profile=_marked("pace", "activity_coefficient"))
        first = run.turn("мой вес 65")
        assert first.meta["reply_kind"] == "anketa_update_weight_confirm_activity"

        second = run.turn(f"{CB_ACTIVITY}light")
        assert run.posted == []
        assert second.meta["reply_kind"] == "anketa_update_weight_confirm_pace"

        run.turn(f"{CB_PACE}gentle")
        body = _sent(run)
        assert (body["activity_coefficient"], body["pace"], body["weight_kg"]) == (
            1.375,
            "gentle",
            65,
        )


class TestL4NoMarks:
    def test_the_path_is_as_it_was(self) -> None:
        run = _Run(profile=_calculated(snapshot=_LOSE))
        card = run.turn("мой вес 65")

        assert card.meta["reply_kind"] == "anketa_update_weight_proposed"
        assert _sent(run)["pace"] == "moderate"


class TestL5Client:
    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            (
                {"legacy_default_inputs": ["pace", "activity_coefficient"]},
                ("pace", "activity_coefficient"),
            ),
            ({}, ()),
        ],
        ids=["present", "absent"],
    )
    def test_reads_the_marks(self, body: dict, expected: tuple, monkeypatch) -> None:
        payload = {
            "data": {
                "exists": True,
                "gender": "female",
                "age": 28,
                "height_cm": 168,
                "weight_kg": 70,
                "goal": "lose",
                "pace": "moderate",
                "activity_coefficient": 1.4,
                "norms": {},
                **body,
            }
        }
        transport = httpx.MockTransport(lambda _req: httpx.Response(200, json=payload))
        original = httpx.AsyncClient

        def _factory(*args, **kwargs):
            kwargs["transport"] = transport
            return original(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", _factory)
        client = NutritionClient(base_url="https://ayla.test", service_token="t")
        profile = asyncio.run(client.get_profile(external_user_id="bot:1"))
        assert profile is not None
        assert profile.legacy_default_inputs == expected


class TestL6TextInsteadOfAButton:
    def test_the_same_question_again(self) -> None:
        run = _Run(profile=_marked("pace"))
        run.turn("мой вес 65")
        again = run.turn("не знаю")

        assert run.posted == []
        assert again.meta["reply_kind"] == "anketa_update_weight_confirm_pace"


class TestL7Cancel:
    def test_cancel_clears_and_sends_nothing(self) -> None:
        run = _Run(profile=_marked("pace"))
        run.turn("мой вес 65")
        run.turn(UPDATE_WEIGHT_CANCEL_CALLBACK)

        assert run.posted == []
        assert run.state is None
