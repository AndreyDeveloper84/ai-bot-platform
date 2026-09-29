"""Что каждый из двух переключателей диетолога на самом деле держит.

Комментарий в ``config/settings/base.py`` обещал «два осознанных действия
оператора, прежде чем хоть одна строка диетолога дойдёт до живого
человека». Для еженедельной рассылки это правда, для реактивного ответа
и строки в дневнике — нет: ``NUTRITION_COACH_DRY_RUN`` их не читает
вовсе, и открывает их первый же переключатель.

Расхождение прожило от T1 до 07.09.2026 и нашлось чтением, а не
падением, — потому что его нечему было ловить. Здесь оно ловится.

Проверка разбором исходника, а не поведением, по той же причине, что и в
``test_no_nagging_contract``: «эта поверхность НЕ спрашивает сухой
прогон» — утверждение про отсутствие вызова, и поведением оно
неотличимо от «спрашивает, но сегодня ответ такой же».

Что страж ловит — замер 07.09.2026, а не намерение: приписать
``or coach_flags.dry_run()`` к воротам в ``coach_observation`` →
``2 failed, 1 passed``; снять → ``3 passed``.
"""

from __future__ import annotations

import ast
from pathlib import Path

#: Корень приложений. Файл лежит в ``apps/nutrition_coach/tests/``.
_APPS = Path(__file__).resolve().parents[2]

#: Имена, под которыми модуль флагов импортируют вызывающие.
_MODULE_ALIASES = frozenset({"flags", "coach_flags", "_coach_flags"})


def _readers(attr: str) -> set[str]:
    """Файлы под ``apps/``, читающие ``<alias>.<attr>`` у флагов диетолога.

    Тесты и миграции не в счёт: вопрос про боевые вызовы.
    """
    found: set[str] = set()
    for path in _APPS.rglob("*.py"):
        parts = path.parts
        if "tests" in parts or "migrations" in parts:
            continue
        source = path.read_text(encoding="utf-8")
        if "nutrition_coach import flags" not in source and "nutrition_coach.flags" not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == attr
                and isinstance(node.value, ast.Name)
                and node.value.id in _MODULE_ALIASES
            ):
                found.add(path.relative_to(_APPS).as_posix())
    return found


class TestDryRunCoversThePushAndNothingElse:
    def test_only_the_weekly_beat_asks_for_dry_run(self) -> None:
        """Единственный боевой читатель — планировщик рассылки.

        Второй файл — команда сухого прогона: она печатает состояние
        флага оператору, а не решает по нему.
        """
        assert _readers("dry_run") == {
            "nutrition_proactive/tasks.py",
            "nutrition_coach/management/commands/nutrition_coach_dryrun.py",
        }

    def test_the_solicited_surfaces_ask_only_the_master_switch(self) -> None:
        """Реактивный ответ и строка в дневнике — на первом переключателе.

        Это не дефект: обе поверхности отвечают человеку, который сам
        пришёл, и «показать, что бы я сказала» для них не имеет смысла —
        сухой прогон придуман для НЕПРОШЕНОЙ рассылки. Но это ровно то,
        чего не говорил прежний комментарий, поэтому сказано здесь.
        """
        enabled = _readers("enabled")

        assert "channels/max/handler.py" in enabled
        assert "orchestrator/coach_observation.py" in enabled
        assert not {"channels/max/handler.py", "orchestrator/coach_observation.py"} & _readers(
            "dry_run"
        )

    def test_the_scan_is_not_vacuous(self) -> None:
        """Контроль присутствия: разбор вообще что-то находит.

        Переименуют модуль флагов или сменят псевдоним импорта — оба
        множества станут пустыми, и утверждения выше прошли бы победно.
        """
        assert _readers("enabled")
        assert _readers("dry_run")
