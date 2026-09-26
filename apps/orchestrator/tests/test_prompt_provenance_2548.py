"""Подсказка называет происхождение честно: сказанное, выведенное, системное (DRF-2548).

Замер DRF-2543 (`docs/measurements/DRF-2543-prompt-sources.md`) нашёл в
подсказке консьержа две строки, чьё происхождение модель читала неверно:

* **S4 — `UserPersonalContext.summary`** стоял в группе «что ты уже знаешь»,
  рядом со сказанным клиентом, хотя это вывод по определению (`POLICY_DEBT`,
  `apps/identity/personal_fields.py`). Теперь — в группе выведенного.
* **S3 — сигнал Ayla в блоке питания** — единственная свободная строка, пришедшая
  не от человека и не из нашего кода, — стоял без рамки рядом с целью человека
  дословно. Теперь — под рамкой «подсказка системы, не слова клиента».

Узлы идут через те функции, которые зовёт продукт: `handler.py` →
`render_current_personal_context(ayla_user_id)` и `build_nutrition_context_block`.
Строки в базе пишет продуктовый писатель; только `summary` задаётся прямо —
писателя у поля нет (DRF-2526), и сторож #2087 обязан это держать.

Охват: из девяти источников таблицы DRF-2543 эти узлы держат два, в которых
смешение было найдено, — S3 и S4. Провенанс S2 (`memory_block`) держат узлы
P0-3 в `apps/orchestrator/tests/`, S7 — свой блок `said_memory`; S1, S5, S6,
S8, S9 фактов о человеке с происхождением не несут.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.utils import timezone

from apps.orchestrator import nutrition_context

STATED_LEAD = "Что ты уже знаешь об этом клиенте"
DERIVED_LEAD = "Это мы вывели сами, клиент этого НЕ говорил"

SUMMARY = "любит тишину и вечерние слоты"
STATED_PHRASE = "веганского питания"
DERIVED_PHRASE = "район «Арбат»"


def _groups(paragraph: str) -> tuple[str, str]:
    """(сказанное, выведенное) — по рамке выведенного, которая идёт второй."""
    stated, lead, derived = paragraph.partition(DERIVED_LEAD)
    assert lead, f"группы выведенного в абзаце нет: {paragraph!r}"
    return stated, derived


@pytest.mark.django_db
def test_personal_context_puts_every_source_in_exactly_one_group():
    from apps.identity.models import MemoryEntry, UserPersonalContext
    from apps.identity.services.memory_writer import write_entry
    from apps.persona.memory_surface import render_current_personal_context

    user_id = uuid.uuid4()
    upc = UserPersonalContext.objects.create(user_id=user_id, summary=SUMMARY)

    def _write(source: str, content: dict) -> None:
        write_entry(
            user_id=user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            source=source,
            kind="preference",
            content=content,
            request_id=uuid.uuid4(),
            purpose="test:prompt-provenance",
            last_inferred_at=None if source == MemoryEntry.SOURCE_EXPLICIT else timezone.now(),
        )

    _write(MemoryEntry.SOURCE_EXPLICIT, {"key": "diet", "value": "vegan"})
    _write(MemoryEntry.SOURCE_INFERRED, {"key": "preferred_districts", "value": "Арбат"})

    paragraph = render_current_personal_context(user_id)
    assert paragraph is not None
    assert STATED_LEAD in paragraph
    stated, derived = _groups(paragraph)

    # Сказанное — в группе сказанного.
    assert STATED_PHRASE in stated
    # Выведенное — в группе выведенного, и summary среди него.
    assert DERIVED_PHRASE in derived
    assert SUMMARY in derived
    # Ни один источник не стоит в двух группах.
    for text in (STATED_PHRASE, DERIVED_PHRASE, SUMMARY):
        assert paragraph.count(text) == 1, (text, paragraph)
    assert STATED_PHRASE not in derived
    assert SUMMARY not in stated
    assert DERIVED_PHRASE not in stated


# ─── S3: сигнал Ayla в блоке питания ───────────────────────────────────────

HINT = "белка стабильно мало всю неделю"
GOAL_TEXT = "хочу больше энергии днём"


@pytest.fixture
def nutrition_block(settings, monkeypatch) -> str:
    """Блок питания той функцией, что зовёт `handler.py`, при всех дверях Ayla
    закрытых подменой: цель, неделя и сегодняшний день."""
    from apps.orchestrator import food_history

    settings.CONCIERGE_NUTRITION_CONTEXT_ENABLED = True
    monkeypatch.setattr(nutrition_context, "_consent_open", lambda bot_user: True)
    monkeypatch.setattr(
        nutrition_context,
        "_fetch_goal",
        lambda bot_user: SimpleNamespace(key="more_energy", text=GOAL_TEXT),
    )
    monkeypatch.setattr(
        nutrition_context,
        "_fetch_deficits",
        lambda bot_user: SimpleNamespace(
            days_observed=5,
            protein_avg_pct_goal=62.0,
            protein_low_streak_days=3,
            hint=HINT,
        ),
    )
    monkeypatch.setattr(food_history, "read_today", Mock(return_value=food_history.UNKNOWN))

    block = nutrition_context.build_nutrition_context_block(object())
    assert block, "блок питания не собрался — узлы ниже прошли бы на пустом"
    return block


def test_the_ayla_signal_stands_under_the_system_frame(nutrition_block):
    hint_lines = [line for line in nutrition_block.splitlines() if HINT in line]
    assert len(hint_lines) == 1, nutrition_block
    assert nutrition_context.HINT_FRAME in hint_lines[0], hint_lines[0]
    assert hint_lines[0].index(nutrition_context.HINT_FRAME) < hint_lines[0].index(HINT)


def test_the_client_goal_and_the_system_signal_never_share_a_line(nutrition_block):
    """Цель — слова человека; сигнал — система. Одна строка на оба была бы
    ровно тем смешением, которое рамка разводит."""
    goal_lines = [line for line in nutrition_block.splitlines() if GOAL_TEXT in line]
    assert len(goal_lines) == 1, nutrition_block
    assert HINT not in goal_lines[0]
    assert nutrition_context.HINT_FRAME not in goal_lines[0]
    assert nutrition_block.count(nutrition_context.HINT_FRAME) == 1
