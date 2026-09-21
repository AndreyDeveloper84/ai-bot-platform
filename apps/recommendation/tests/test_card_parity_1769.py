"""Паритет словаря карточки: бот и экран говорят одними словами (DRF-1769).

Карточка C04 живёт на двух поверхностях — в DM (основная, `card.py`) и
на экране (`apps/miniapp/src/lib/recommendation-card.ts`). Тексты макета
в обеих написаны руками: общего модуля у Python и TypeScript нет, и
выдумать его ради шести строк — не то лекарство.

Лекарство — этот тест. Разъехаться они теперь не могут молча: правка с
одной стороны валит сборку, а не доезжает до человека кадром, где чат и
экран называют одно и то же разными словами.

Тот же приём, что у `recommendation-absence.ts` (паритет C04.4 в
`test_card_1772.py::TestParityWithMiniApp`) — и по той же причине.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.recommendation import card as c

REPO_ROOT = Path(__file__).resolve().parents[3]
_TS = REPO_ROOT / "apps" / "miniapp" / "src" / "lib" / "recommendation-card.ts"


@pytest.fixture(scope="module")
def ts_source() -> str:
    return _TS.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CARD_HEAD", c.CARD_HEAD),
        ("WHY_HEAD", c.WHY_HEAD),
        ("WHY_MORE_HEAD", c.WHY_MORE_HEAD),
        ("ALT_HEAD", c.ALT_HEAD),
        ("ALT_PRIMARY_HEAD", c.ALT_PRIMARY_HEAD),
        ("ALT_OTHERS_HEAD", c.ALT_OTHERS_HEAD),
        ("BUTTON_PICK", c.BUTTON_PICK),
        ("BUTTON_WHY", c.BUTTON_WHY),
        ("BUTTON_ALT", c.BUTTON_ALT),
        ("BUTTON_SKIP", c.BUTTON_SKIP),
    ],
)
def test_the_screen_says_the_same_words_as_the_chat(ts_source: str, name: str, value: str):
    assert f'export const {name} = "{value}";' in ts_source


def test_the_reason_limit_is_the_same_on_both_surfaces(ts_source: str):
    """Экран режет причины сам: список приходит с сервера, но экран,
    который молча нарисует четвёртую, однажды её и нарисует."""
    assert f"export const MAX_REASONS = {c.MAX_REASONS};" in ts_source
