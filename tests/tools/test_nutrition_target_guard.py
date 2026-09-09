"""Тесты для tools/lint/nutrition_target_guard.py — сторожа ориентиров.

Доказывают ОБЕ стороны, как и остальные тесты сторожей в этой папке:
что он ловит и что он НЕ ловит. Вторая половина важнее: сторож, который
краснеет на честном коде, снимут через неделю — и вместе с ним уйдёт
защита от того, ради чего его заводили.

Предмет сторожа — §82 и §85: плоская норма 2000 ккал и формула воды
30 мл × вес сняты владельцем 09.09.2026, ориентира нет ни у кого, и
отсутствие обязано доезжать отсутствием ключа. Место без ориентира
читается как недоделка, и «разумное умолчание» туда уже возвращали
дважды.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# `tools/` не пакет (нет __init__.py) — импорт через путь, тем же
# приёмом, что в test_negative_assert_guard.py.
sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "lint"))
import nutrition_target_guard as guard  # type: ignore[import-not-found]  # noqa: E402


def _scan(body: str) -> list[str]:
    return [v.detail for v in guard.scan_source(body, path="sample.py")]


class TestItCatchesSubstitution:
    """Четыре формы возврата, все встречались в этом репозитории живьём."""

    def test_plain_assignment(self) -> None:
        assert _scan("calories_target = 2000\n") == ["calories_target = 2000"]

    def test_keyword_argument(self) -> None:
        assert _scan("WellnessToday(calories_target=2100)\n") == [
            "calories_target=2100",
        ]

    def test_dict_key(self) -> None:
        """Ключ словаря — строка, и разбор ИМЁН её не видит.

        Это форма, в которой подстановка приезжает в теле ответа: не
        `x = 2000`, а `{"calories_goal": 2000}` в собранном payload.
        """
        assert _scan('payload = {"calories_goal": 2000}\n') == [
            '"calories_goal": 2000',
        ]

    def test_or_default(self) -> None:
        """`... or 2000` — та самая форма, которой возвращали восьмёрку."""
        hits = _scan('norm = body.get("water_goal_ml") or 2000\n')
        assert any(h.startswith("or-умолчание") for h in hits), hits

    def test_attribute_assignment(self) -> None:
        assert _scan("profile.water_goal_ml = 2000\n") == [
            "water_goal_ml = 2000",
        ]


class TestItLeavesHonestCodeAlone:
    """Что сторож пропускает — и почему это правильно."""

    def test_absence_is_not_a_violation(self) -> None:
        """Ради этого он и заведён: `None` — разрешённая форма."""
        assert _scan("calories_target = None\n") == []

    def test_zero_is_not_a_violation(self) -> None:
        """Ноль разрешён намеренно.

        Внутри модулей ноль давно читается как «нет» и проверяется на
        положительность у каждого потребителя. Запретить его значило бы
        сломать честный код ради формы, а сторож, который краснеет на
        честном коде, снимут.
        """
        assert _scan("today_norm_ml = 0\n") == []

    def test_the_fact_is_not_a_target(self) -> None:
        """Съеденное и выпитое — ФАКТ, и число в нём законно.

        Если бы сторож ловил и факт, он запретил бы фикстуры вида
        `calories_eaten=1240` — а это правда о человеке, а не выдуманная
        мишень. Ровно эту разницу пропустили в дневнике, где БЖУ
        вычисляли из калорий постоянным коэффициентом: выдуманный факт
        о себе человек не оспорит, в отличие от выдуманной нормы.
        """
        assert _scan("calories_eaten = 1240\nwater_ml = 250\n") == []

    def test_a_variable_target_is_not_a_violation(self) -> None:
        """Ориентир, ПРИШЕДШИЙ откуда-то, — не подстановка.

        После утверждения методики (§85) ориентир станет настоящим и
        будет присваиваться этим же именам. Сторож про источник числа,
        а не про существование поля.
        """
        assert _scan("calories_target = summary.calories_goal\n") == []


class TestTheScannerStillSees:
    """Контроль присутствия: тесты выше — утверждения об отсутствии.

    Все пять в классе `TestItLeavesHonestCodeAlone` прошли бы победно в
    мире, где разбор сломан и не находит уже ничего. Здесь сканер
    запускается по исходнику, виноватому заведомо, и обязан найти в нём
    все формы разом.
    """

    _GUILTY = """
def render(body, profile):
    calories_target = 2000
    payload = {"calories_goal": 2000}
    norm = body.get("water_goal_ml") or 2000
    profile.today_norm_ml = 2400
    return WellnessToday(calories_target=2100), payload, norm
"""

    def test_all_four_forms_are_found(self) -> None:
        hits = _scan(self._GUILTY)
        assert "calories_target = 2000" in hits
        assert '"calories_goal": 2000' in hits
        assert "calories_target=2100" in hits
        assert "today_norm_ml = 2400" in hits
        assert any(h.startswith("or-умолчание") for h in hits), hits

    def test_the_allowlist_is_short_and_explicit(self) -> None:
        """Разрешённых файлов мало, и все — фикстуры «ориентир ЕСТЬ».

        Список путями, а не шаблоном: `**/tests/**` пустил бы сюда любой
        будущий тест, а тест — самое частое место, где выдумка заводится
        «на время». Если список начнёт расти, растёт он видимо.
        """
        assert len(guard.ALLOWED) <= 8, sorted(guard.ALLOWED)
        assert all(p.startswith("apps/") for p in guard.ALLOWED)
        assert all("/tests/" in p for p in guard.ALLOWED), sorted(guard.ALLOWED)
