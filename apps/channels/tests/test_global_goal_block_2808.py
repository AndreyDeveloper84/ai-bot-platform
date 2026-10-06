"""DRF-2808 — цель доходит до промпта консьержа на живом глобальном ходе.

``apps/orchestrator/tests/test_goal_context_2808.py`` держит построитель. Этот
файл — проводку: настоящий ``GlobalMaxHandler`` несёт строку цели в слот
памяти на ходе НЕ про еду (раньше цель жила только в блоке питания и туда не
доходила), и не кладёт её дважды, когда блок питания уже несёт её как рамку.
"""

from __future__ import annotations

import pytest

from apps.channels.handlers import GlobalMaxHandler
from apps.channels.max import handler as max_handler
from apps.channels.tests.test_global_nutrition_context import (  # noqa: F401 — fixtures
    _flag_on,
    _raw_entry,
    captured_turn,
    fake_redis,
    mock_send,
)

pytestmark = pytest.mark.django_db

LINE = "Цель клиента своими словами: хочу выглядеть отдохнувшей к отпуску"


@pytest.fixture
def quiet_memory(monkeypatch):
    monkeypatch.setattr(max_handler, "build_concierge_memory_block", lambda bot_user: "")
    monkeypatch.setattr(max_handler, "render_current_personal_context", lambda bot_user: "")


@pytest.fixture
def goal(monkeypatch):
    monkeypatch.setattr("apps.orchestrator.goal_context.build_goal_block", lambda bot_user: LINE)


def test_a_turn_not_about_food_carries_the_goal_to_the_prompt(
    mock_send, fake_redis, captured_turn, quiet_memory, goal
) -> None:
    GlobalMaxHandler()(_raw_entry("Привет"))
    GlobalMaxHandler()(_raw_entry("Хочу записаться на маникюр в субботу"))

    assert captured_turn["memory_block"] == LINE
    assert captured_turn["nutrition_block"] == ""
    from apps.orchestrator.concierge import build_concierge_system_prompt

    assert LINE in build_concierge_system_prompt(memory_block=captured_turn["memory_block"])


def test_no_goal_leaves_the_turn_as_it_was(
    mock_send, fake_redis, captured_turn, quiet_memory, monkeypatch
) -> None:
    monkeypatch.setattr("apps.orchestrator.goal_context.build_goal_block", lambda bot_user: "")
    GlobalMaxHandler()(_raw_entry("Привет"))
    GlobalMaxHandler()(_raw_entry("Хочу записаться на маникюр в субботу"))

    assert "message_text" in captured_turn
    assert captured_turn["memory_block"] == ""


def test_a_food_turn_does_not_carry_the_goal_twice(
    mock_send, fake_redis, captured_turn, quiet_memory, goal, monkeypatch
) -> None:
    framed = f"{LINE}\nБелок 62% от ориентира."
    monkeypatch.setattr(max_handler, "build_nutrition_context_block", lambda bot_user: framed)
    monkeypatch.setattr(max_handler, "interpretation_eligible", lambda text: True)
    GlobalMaxHandler()(_raw_entry("Привет"))

    GlobalMaxHandler()(_raw_entry("Что мне съесть после тренировки?"))

    assert captured_turn["nutrition_block"] == framed
    assert LINE not in captured_turn["memory_block"]
