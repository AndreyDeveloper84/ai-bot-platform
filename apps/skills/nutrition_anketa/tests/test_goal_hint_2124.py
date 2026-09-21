"""Подсказка на шаге «Какая у тебя цель?» из шаблона цели (DRF-2124, План-B).

Каталог (#515) кладёт в decision-context ``known.goal.nutrition_goal_hint`` —
список ⊆ {lose, maintain, gain} или ``null``. Анкета питания — её читатель:
на шаге цели при активной ``ClientGoal`` с подсказкой ПОДСВЕЧИВАЕТ вариант
строкой «под цель «…» обычно выбирают …». Граница В-4 — связать, не слить:
``goal_key`` человека и ``goal`` анкеты остаются разными понятиями, и
§7.1/§5.1 требуют ВХОДА ЧЕЛОВЕКА — шаг не предвыбирается и не пропускается.

* h1 — активная цель с подсказкой → в промпте шага цели строка с меткой
  цели из зеркала и метками вариантов из таблицы анкеты; в
  ``action_data["goal_hint"]`` — ключ цели и слаги;
* h2 — подсказки нет (``null`` / цели нет / нет ``known``) → промпт шага
  ровно тот же, что до этого листа, ключа ``goal_hint`` нет;
* h3 — ложный вход: подсказка показана, ответ не выбран → в состоянии
  FSM нет ответа ``goal``, POST в каталог не ушёл, клавиатура — все три
  варианта в прежнем порядке с прежними метками (ничего не «предвыбрано»);
* h4 — человек выбирает НЕ подсказанное → payload несёт его выбор; 1.375
  и цель из подсказки не подставлены;
* h5 — Ayla недоступна / не настроена / прислала мусор → шаг без
  подсказки, без падения, кнопки на месте;
* h6 — значения вне таблицы анкеты (``slim``) в подсказку не попадают;
  список из одних чужих — как ``null``;
* h7 — метка цели — из зеркала (``goal_label``), свободный ``goal_text``
  человека в подсказку не печатается;
* h8 — decision-context читается только при показе шага цели: на шагах
  gender…activity ни одного запроса.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock, patch

import pytest

from apps.consent.personal_calculation import ConsentAttestation
from apps.integrations.ayla.goals_client import GoalsConfigError, GoalsUnavailable
from apps.skills.base import SkillContext
from apps.skills.nutrition_anketa.fsm import GOAL_CHOICES, choice_keyboard_options
from apps.skills.nutrition_anketa.skill import NutritionAnketaSkill
from apps.skills.nutrition_anketa.tests.test_skill import (
    _ATTESTATION_LOOKUP,
    _profile,
    _StatefulConversation,
)

_ATTESTATION = ConsentAttestation(
    type="personal_calculation", document_version="personal-calculation-v1"
)

_FETCH = "apps.skills.nutrition_anketa.skill.fetch_decision_context"
_LABEL = "apps.skills.nutrition_anketa.skill.goal_label"

_BODY = {"gender": "female", "age": 28, "height": 168, "weight": 62}

#: Зеркало меток (goal_label): ключ без метки отдаётся как есть — и тогда
#: подсказки нет (h7).
_MIRROR = {"body_shape": "Подтянуть фигуру", "recharge": "Перезагрузиться"}


def _state(current_step: str, answers: dict[str, Any]) -> dict:
    return {
        "nutrition_anketa": {
            "current_step": current_step,
            "answers": dict(answers),
            "is_complete": False,
        }
    }


def _doc(hint: list[str] | None, *, goal_key: str = "body_shape", goal_text: str = "") -> dict:
    return {
        "version": 2,
        "known": {
            "goal": {
                "id": "g-1",
                "state": "active",
                "goal_key": goal_key,
                "goal_text": goal_text,
                "nutrition_goal_hint": hint,
            }
        },
        "missing": [],
        "suggestions": [],
        "intents": [],
        "next": None,
    }


class _Run:
    """Один навык, одна беседа, перехваченные каталог и decision-context."""

    def __init__(self, state: dict | None = None, *, doc: Any = None, fetch: Any = None) -> None:
        self.conversation = _StatefulConversation(state)
        self.captured: list[dict] = []
        client = Mock()

        async def _upsert(**kwargs):
            self.captured.append(kwargs)
            return _profile()

        client.upsert_profile = _upsert
        self._client = client
        self.skill = NutritionAnketaSkill()
        self.fetch = fetch if fetch is not None else Mock(return_value=doc)

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
            patch(_FETCH, self.fetch),
            patch(_LABEL, side_effect=lambda key: _MIRROR.get(key, key)),
        ):
            return self.skill.handle(ctx)

    @property
    def bucket(self) -> dict:
        return self.conversation.skill_state.get("nutrition_anketa") or {}


def _ask_goal(run: _Run):
    """Ответить на активность — анкета задаёт цель."""
    return run.turn("cb:anketa:choice:activity:moderate")


_PLAIN_GOAL_PROMPT = "Какая у тебя цель?"


class TestH1HintIsShownForAnActiveGoal:
    def test_the_prompt_names_the_goal_and_the_hinted_options(self) -> None:
        run = _Run(_state("activity", _BODY), doc=_doc(["lose", "maintain"]))
        result = _ask_goal(run)
        assert result.action_type == "anketa_step_goal"
        assert result.reply_text.startswith(_PLAIN_GOAL_PROMPT)
        assert "Подтянуть фигуру" in result.reply_text
        assert "обычно выбирают" in result.reply_text
        assert "«Похудеть»" in result.reply_text and "«Поддержать»" in result.reply_text
        assert "«Набрать»" not in result.reply_text

    @pytest.mark.parametrize(
        ("gender", "tail"),
        [("female", "выбирай сама"), ("male", "выбирай сам")],
        ids=["female", "male"],
    )
    def test_the_closing_words_follow_the_gender_answered_at_step_one(
        self, gender: str, tail: str
    ) -> None:
        """Поправка главного окна 20.09: «сама/сам» с косой чертой не звучит —
        пол известен с первого шага, строка склоняется по нему."""
        run = _Run(_state("activity", {**_BODY, "gender": gender}), doc=_doc(["lose"]))
        result = _ask_goal(run)
        assert result.reply_text.rstrip(".").endswith(tail)
        assert "/" not in result.reply_text

    def test_action_data_carries_the_hint_as_data(self) -> None:
        run = _Run(_state("activity", _BODY), doc=_doc(["maintain"], goal_key="recharge"))
        result = _ask_goal(run)
        assert result.action_data["goal_hint"] == {"goal_key": "recharge", "options": ["maintain"]}

    def test_edit_back_to_goal_shows_the_hint_too(self) -> None:
        run = _Run(_state("goal", {**_BODY, "activity": "light"}), doc=_doc(["lose"]))
        result = run.turn("cb:anketa:edit:goal")
        assert result.action_type == "anketa_step_goal"
        assert "обычно выбирают" in result.reply_text


class TestH2NoHintMeansThePlainStep:
    @pytest.mark.parametrize(
        "doc",
        [
            _doc(None),
            {"version": 2, "known": {"goal": None}},
            {"version": 2, "known": {}},
            {"version": 2},
            {
                "version": 2,
                "known": {"goal": {**_doc(["lose"])["known"]["goal"], "is_active": False}},
            },
        ],
        ids=["hint-null", "no-goal", "known-empty", "no-known", "archived-goal"],
    )
    def test_the_prompt_is_exactly_the_plain_one(self, doc: dict) -> None:
        run = _Run(_state("activity", _BODY), doc=doc)
        result = _ask_goal(run)
        assert result.reply_text == _PLAIN_GOAL_PROMPT
        assert "goal_hint" not in result.action_data


class TestH3TheHintDoesNotAnswerForThePerson:
    def test_hint_shown_answer_not_chosen_no_goal_in_state_and_no_post(self) -> None:
        run = _Run(_state("activity", _BODY), doc=_doc(["lose", "maintain"]))
        result = _ask_goal(run)
        assert "обычно выбирают" in result.reply_text
        # Состояние стоит на цели, ответа нет — подсказка не пишет в answers.
        assert run.bucket["current_step"] == "goal"
        assert "goal" not in run.bucket["answers"]
        assert run.captured == []

    def test_the_keyboard_is_the_whole_table_in_its_order(self) -> None:
        run = _Run(_state("activity", _BODY), doc=_doc(["lose"]))
        result = _ask_goal(run)
        buttons = result.action_data["buttons"]
        assert [b["label"] for b in buttons] == [
            label for label, _ in choice_keyboard_options("goal")
        ]
        assert [b["callback"] for b in buttons] == [
            f"cb:anketa:choice:goal:{slug}" for slug in GOAL_CHOICES
        ]
        # Ни у одной кнопки метка не изменена — «подсветка» живёт в тексте.
        assert all(b["label"] in GOAL_CHOICES.values() for b in buttons)


class TestH4ThePersonsChoiceWins:
    def test_a_non_hinted_answer_goes_to_the_catalogue_as_is(self) -> None:
        run = _Run(_state("activity", _BODY), doc=_doc(["lose", "maintain"]))
        _ask_goal(run)
        run.turn("cb:anketa:choice:goal:gain")
        # Вопрос 59: «набрать» спрашивает темп — ответ закрывает анкету.
        run.turn("cb:anketa:choice:pace:gentle")
        assert len(run.captured) == 1
        data = run.captured[0]["data"]
        assert data["goal"] == "gain"
        assert data["activity_coefficient"] == 1.55  # moderate, не 1.375
        assert "_skipped_fields" not in data


class TestH5AylaFailuresLeaveThePlainStep:
    @pytest.mark.parametrize(
        "fetch",
        [
            Mock(side_effect=GoalsUnavailable("timeout")),
            Mock(side_effect=GoalsConfigError("not configured")),
            Mock(side_effect=RuntimeError("boom")),
            Mock(return_value="not a document"),
            Mock(
                return_value={
                    "known": {"goal": {"goal_key": "body_shape", "nutrition_goal_hint": "lose"}}
                }
            ),
        ],
        ids=["unavailable", "not-configured", "any-exception", "not-a-dict", "hint-not-a-list"],
    )
    def test_the_step_renders_without_a_hint(self, fetch: Mock) -> None:
        run = _Run(_state("activity", _BODY), fetch=fetch)
        result = _ask_goal(run)
        assert result.action_type == "anketa_step_goal"
        assert result.reply_text == _PLAIN_GOAL_PROMPT
        assert len(result.action_data["buttons"]) == len(GOAL_CHOICES)


class TestH6OnlyTableValuesAreHinted:
    def test_unknown_values_are_dropped(self) -> None:
        run = _Run(_state("activity", _BODY), doc=_doc(["slim", "lose"]))
        result = _ask_goal(run)
        assert result.action_data["goal_hint"]["options"] == ["lose"]
        assert "slim" not in result.reply_text

    def test_only_unknown_values_is_no_hint(self) -> None:
        run = _Run(_state("activity", _BODY), doc=_doc(["slim"]))
        result = _ask_goal(run)
        assert result.reply_text == _PLAIN_GOAL_PROMPT
        assert "goal_hint" not in result.action_data


class TestH7TheGoalNameIsTheCuratedLabel:
    def test_a_key_the_mirror_does_not_know_means_no_hint(self) -> None:
        """Ревью #1895: ``goal_label`` без метки отдаёт сырой ключ — в текст
        человеку слаг не печатается; подсказки просто нет."""
        run = _Run(_state("activity", _BODY), doc=_doc(["lose"], goal_key="mystery_goal"))
        result = _ask_goal(run)
        assert result.reply_text == _PLAIN_GOAL_PROMPT
        assert "goal_hint" not in result.action_data
        assert "mystery_goal" not in result.reply_text

    def test_goal_text_of_the_person_is_not_printed(self) -> None:
        run = _Run(
            _state("activity", _BODY),
            doc=_doc(["lose"], goal_text="хочу влезть в платье к свадьбе сестры"),
        )
        result = _ask_goal(run)
        assert "Подтянуть фигуру" in result.reply_text
        assert "платье" not in result.reply_text


class TestH8DecisionContextIsReadOnlyOnTheGoalStep:
    def test_no_request_before_the_goal_step(self) -> None:
        run = _Run(doc=_doc(["lose"]))
        run.turn("/anketa")
        run.turn("cb:anketa:choice:gender:female")
        run.turn("28")
        run.turn("cb:anketa:choice:screening:none")
        run.turn("168")
        run.turn("62")
        assert run.fetch.call_count == 0
        result = run.turn("cb:anketa:choice:activity:light")
        assert result.action_type == "anketa_step_goal"
        assert run.fetch.call_count == 1
        assert run.fetch.call_args.kwargs["external_user_id"] == "bot:max:12345"
