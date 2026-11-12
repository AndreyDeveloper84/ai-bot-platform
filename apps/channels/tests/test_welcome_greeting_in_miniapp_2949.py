"""DRF-2949 — приветствие Ayla в Mini App совпадает с первым сообщением бота.

Макет «Первый контакт» рисует на первом экране приложения приветствие Ayla.
Решение владельца 11.11.2026: слова макета не берём, остаются слова от 19.09.
Mini App поэтому показывает тот же текст, что бот говорит первым сообщением
(``GLOBAL_WELCOME_TEXT``), и держит его копию в
``apps/miniapp/src/screens/GoalSelectScreen.tsx``. Этот узел не даёт копии
разойтись с источником: у Ayla одно приветствие на обе поверхности.
"""

from __future__ import annotations

import json
from pathlib import Path

from apps.channels.max.global_onboarding import GLOBAL_WELCOME_TEXT

GOAL_SCREEN_TSX = (
    Path(__file__).resolve().parents[2] / "miniapp" / "src" / "screens" / "GoalSelectScreen.tsx"
)


def _ts_literal(text: str) -> str:
    """Python-строка в виде TS-литерала в двойных кавычках."""
    return json.dumps(text, ensure_ascii=False)


def test_the_bot_greeting_has_the_shape_the_screen_relies_on() -> None:
    # Наличие: первая строка — само приветствие с рукой, вторая — что Ayla умеет.
    title, body = GLOBAL_WELCOME_TEXT.split("\n", 1)
    assert title.endswith(" 👋")
    assert body and "\n" not in body


def test_the_miniapp_greets_with_the_bot_words_verbatim() -> None:
    source = GOAL_SCREEN_TSX.read_text(encoding="utf-8")
    title, body = GLOBAL_WELCOME_TEXT.split("\n", 1)

    # Рука на экране рисуется отдельным декоративным знаком, не текстом.
    assert f"export const HELLO_TITLE = {_ts_literal(title.removesuffix(' 👋'))};" in source
    # Привязка к самой константе: та же фраза в комментарии узел не устроит.
    assert f"export const HELLO_TEXT =\n  {_ts_literal(body)};" in source
