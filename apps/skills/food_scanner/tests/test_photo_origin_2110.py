"""Фото-запись всегда несёт своё происхождение §136 (DRF-2110).

Замер модуля 8 (19.09): чат и прокси Mini App слали ``entry_origin`` только
при поправке; подтверждённая как есть фото-запись уходила без него, каталог
хранил NULL («до §136 или не передано»), и ``photo_estimated_confirmed`` не
писал никто. Здесь: обе половины на обоих путях, и перепись по роли — каждый
``log_meal(... scan_id=...)`` под ``apps/`` несёт ``entry_origin=`` явным
keyword (иначе перепись слепа к ``**extra``); ложный вход в обе стороны.

Caption — решение этого листа: НЕ передавать. Сканер получает только фото
без подписи (``FoodScannerSkill.matches`` требует пустой текст; фото с
подписью уходит модели), так что подписи у него нет по маршрутизации;
промпт каталога без неё корректен.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from apps.integrations.ayla.nutrition_client import FoodLogResponse
from apps.skills.food_clarify.text_entry import (
    PHOTO_ORIGIN_ESTIMATED_CONFIRMED,
    PHOTO_ORIGIN_USER_CORRECTED,
)
from apps.skills.food_scanner.skill import FoodScannerSkill
from apps.skills.food_scanner.tests.test_skill import _context

REPO_ROOT = Path(__file__).resolve().parents[4]


def _to_diary(*, grams_map: dict | None = None) -> list[dict]:
    ctx = _context("cb:food:to_diary:scan-1")
    ctx.conversation.skill_state = {"food_scan_grams": grams_map} if grams_map else {}
    captured: list[dict] = []

    async def _log(**kwargs):
        captured.append(kwargs)
        return FoodLogResponse(
            log_id="log-1",
            dish_name="Борщ",
            meal_type="other",
            calories=250.0,
            raw={"entry_origin": kwargs.get("entry_origin")},
        )

    client = Mock()
    client.log_meal = _log
    with (
        patch("apps.skills.food_scanner.skill.get_nutrition_client", return_value=client),
        patch("apps.conversations.services.write_skill_state"),
    ):
        FoodScannerSkill().handle(ctx)
    return captured


@pytest.fixture(autouse=True)
def _gates_open(settings):
    settings.NUTRITION_ENABLED = True
    with (
        patch(
            "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=True
        ),
        patch("apps.consent.nutrition.diary_is_granted", return_value=True),
    ):
        yield


class TestTheChatNamesBothHalves:
    def test_a_plain_confirmation_is_photo_estimated_confirmed(self) -> None:
        captured = _to_diary()
        assert len(captured) == 1 and captured[0]["scan_id"] == "scan-1"
        assert captured[0]["entry_origin"] == PHOTO_ORIGIN_ESTIMATED_CONFIRMED
        assert "portion_multiplier" not in captured[0]

    def test_a_corrected_portion_is_photo_user_corrected(self) -> None:
        captured = _to_diary(grams_map={"scan-1": {"grams": 500, "portion_g": 250}})
        assert captured[0]["entry_origin"] == PHOTO_ORIGIN_USER_CORRECTED
        assert captured[0]["portion_multiplier"] == 2.0

    def test_the_four_values_are_the_136_vocabulary(self) -> None:
        from apps.skills.food_clarify.text_entry import (
            ORIGIN_ESTIMATED_CONFIRMED,
            ORIGIN_USER_CORRECTED,
        )

        assert {
            ORIGIN_ESTIMATED_CONFIRMED,
            ORIGIN_USER_CORRECTED,
            PHOTO_ORIGIN_ESTIMATED_CONFIRMED,
            PHOTO_ORIGIN_USER_CORRECTED,
        } == {
            "text_estimated_confirmed",
            "text_user_corrected",
            "photo_estimated_confirmed",
            "photo_user_corrected",
        }


# --- перепись по роли: log_meal со scan_id несёт entry_origin явным keyword ------


def _log_meal_calls(tree: ast.AST) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "log_meal"
    ]


def census(source: str) -> tuple[int, list[int]]:
    """(вызовов log_meal со scan_id=, строки тех из них, где нет entry_origin=)."""
    tree = ast.parse(source)
    with_scan = 0
    missing: list[int] = []
    for call in _log_meal_calls(tree):
        keys = {kw.arg for kw in call.keywords if kw.arg is not None}
        if "scan_id" not in keys:
            continue
        with_scan += 1
        if "entry_origin" not in keys:
            missing.append(call.lineno)
    return with_scan, missing


class TestCensusOfScanLogs:
    def test_every_log_by_scan_id_names_its_origin(self) -> None:
        seen = 0
        offences: list[str] = []
        for path in sorted((REPO_ROOT / "apps").rglob("*.py")):
            if "tests" in path.parts or path.name == "nutrition_client.py":
                continue
            with_scan, missing = census(path.read_text(encoding="utf-8"))
            seen += with_scan
            rel = path.relative_to(REPO_ROOT).as_posix()
            offences += [f"{rel}:{line}" for line in missing]
        assert seen >= 2, "the census found fewer than the chat + proxy log-by-scan calls"
        assert offences == [], "log_meal(scan_id=…) without entry_origin=: " + ", ".join(offences)

    def test_the_census_reads_keywords_not_kwargs_splats(self) -> None:
        """Ложный вход в обе стороны — и `**extra` не считается: перепись требует явного keyword."""
        bare = "async def go(c):\n    return await c.log_meal(external_user_id='x', scan_id='s', meal_type='other')\n"
        assert census(bare) == (1, [2])
        splat = (
            "async def go(c, extra):\n"
            "    return await c.log_meal(external_user_id='x', scan_id='s', meal_type='other', **extra)\n"
        )
        assert census(splat) == (1, [2])
        named = (
            "async def go(c):\n"
            "    return await c.log_meal(external_user_id='x', scan_id='s', meal_type='other', entry_origin='photo_estimated_confirmed')\n"
        )
        assert census(named) == (1, [])
        text_path = "async def go(c):\n    return await c.log_meal(external_user_id='x', dish_name='борщ', meal_type='other')\n"
        assert census(text_path) == (0, [])
