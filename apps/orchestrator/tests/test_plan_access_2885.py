"""DRF-2885 — замок приёмки Плана: кому из людей отвечает механизм плана.

Решение владельца 10.10: включение ``PLAN_ENGINE_ENABLED`` — не пользовательский
запуск. План открыт человеку, только когда механизм включён И его аккаунт
назван в ``PLAN_ACCEPTANCE_ACCOUNTS``; пусто или любая неясность — никому.

Здесь сам замок и перепись его читателей. Обход по путям человека (чат,
экран Mini App, прямые ручки, действия с шагами) —
``apps/miniapp_api/tests/test_plan_access_bypass_2885.py``.

* a1 — открыто только при флаге И названном аккаунте;
* a2 — пустой список — никому;
* a3 — список не того вида — никому (строка целиком списком не считается);
* a4 — человек без канала или идентификатора — закрыто;
* a5 — сбой чтения настройки — закрыто;
* a6 — по разговору: человек берётся из разговора, нет его — закрыто;
* a7 — умолчание настроек: список пуст;
* c1 — перепись: флаг читают только замок и запись вердикта хода;
* c2 — прежних «голых» проверок флага во входах Плана не осталось.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from apps.orchestrator import plan_access
from apps.orchestrator.plan_access import plan_open_for, plan_open_in

_APPS = Path(__file__).resolve().parents[2]


def _person(person: Any = "1001", channel: Any = "max") -> SimpleNamespace:
    return SimpleNamespace(channel=channel, channel_user_id=person)


@pytest.fixture
def listed(settings) -> None:
    settings.PLAN_ENGINE_ENABLED = True
    settings.PLAN_ACCEPTANCE_ACCOUNTS = ("max:1001",)


def test_a1_open_only_with_the_flag_and_a_named_account(listed: None, settings) -> None:
    assert plan_open_for(_person("1001")) is True
    # Другой человек; тот же идентификатор в другом канале; флаг выключен.
    assert plan_open_for(_person("1002")) is False
    assert plan_open_for(_person("1001", channel="telegram")) is False
    settings.PLAN_ENGINE_ENABLED = False
    assert plan_open_for(_person("1001")) is False


def test_a2_an_empty_list_opens_the_plan_to_no_one(listed: None, settings) -> None:
    assert plan_open_for(_person()) is True  # замок вообще открывается
    empties: list[Any] = [(), [], frozenset(), ("", "  ")]
    for empty in empties:
        settings.PLAN_ACCEPTANCE_ACCOUNTS = empty
        assert plan_open_for(_person()) is False, empty


@pytest.mark.parametrize(
    "malformed",
    [
        "max:1001",  # строка вместо списка: ни «один аккаунт», ни «по буквам»
        "max:1001,max:1002",
        None,
        True,
        1001,
        {"max:1001": True},
        ("max:1001", 1001),  # не строка среди строк — весь список не читается
        ("max:1001", None),
    ],
)
def test_a3_a_list_of_the_wrong_shape_opens_the_plan_to_no_one(
    listed: None, settings, malformed: Any
) -> None:
    assert plan_open_for(_person()) is True  # с верным списком этот человек проходит
    settings.PLAN_ACCEPTANCE_ACCOUNTS = malformed
    assert plan_open_for(_person()) is False


def test_a3_entries_are_matched_whole_and_spaces_around_them_do_not_matter(
    listed: None, settings
) -> None:
    settings.PLAN_ACCEPTANCE_ACCOUNTS = (" max:1001 ", "max:20")
    assert plan_open_for(_person("1001")) is True
    # Ни часть идентификатора, ни шаблон аккаунтом не считаются.
    for other in ("100", "10011", "2", "200"):
        assert plan_open_for(_person(other)) is False, other
    settings.PLAN_ACCEPTANCE_ACCOUNTS = ("*", "max:*", "max:")
    assert plan_open_for(_person("1001")) is False


@pytest.mark.parametrize(
    "bot_user",
    [
        None,
        SimpleNamespace(),
        SimpleNamespace(channel="max"),
        SimpleNamespace(channel_user_id="1001"),
        _person(""),
        _person("   "),
        _person(None),
        _person(1001),  # идентификатор числом — не тот вид, не угадывается
        _person("1001", channel=""),
        _person("1001", channel=None),
    ],
)
def test_a4_a_person_without_a_channel_or_an_id_is_closed(
    listed: None, settings, bot_user: Any
) -> None:
    # Список нарочно содержит «пустые» ключи: такой человек всё равно закрыт.
    settings.PLAN_ACCEPTANCE_ACCOUNTS = ("max:1001", "max:", ":1001", ":", "None:None")
    assert plan_open_for(_person("1001")) is True
    assert plan_open_for(bot_user) is False


def test_a5_a_failed_read_of_the_setting_is_closed(
    listed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert plan_open_for(_person()) is True

    def broken() -> frozenset[str]:
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(plan_access, "acceptance_accounts", broken)
    assert plan_open_for(_person()) is False


def test_a5_a_flag_that_is_not_exactly_true_is_closed(listed: None, settings) -> None:
    assert plan_open_for(_person()) is True
    for not_true in ("true", "1", 1, "yes", None):
        settings.PLAN_ENGINE_ENABLED = not_true
        assert plan_open_for(_person()) is False, not_true


def test_a6_by_conversation_the_person_is_taken_from_the_conversation(listed: None) -> None:
    assert plan_open_in(SimpleNamespace(bot_user=_person("1001"))) is True
    assert plan_open_in(SimpleNamespace(bot_user=_person("1002"))) is False
    assert plan_open_in(SimpleNamespace(bot_user=None)) is False
    assert plan_open_in(SimpleNamespace()) is False
    assert plan_open_in(None) is False


def test_a7_by_default_the_list_is_empty_and_the_engine_is_off() -> None:
    """Умолчание настроек проекта, без подмен: закрыто дважды."""
    from django.conf import settings

    assert settings.PLAN_ACCEPTANCE_ACCOUNTS == ()
    assert settings.PLAN_ENGINE_ENABLED is False
    assert plan_open_for(_person()) is False


# ─── перепись читателей флага ────────────────────────────────────────────

#: Кто читает ``PLAN_ENGINE_ENABLED`` в коде приложения — и зачем. Новый
#: читатель мимо замка краснит c1: вход Плана обязан спрашивать замок.
FLAG_READERS = {
    "orchestrator/plan_access.py": "замок: единственное место, где флаг читают входы Плана",
    "orchestrator/dr_shadow.py": (
        "record_turn_safety: пишет вердикт ворот безопасности каждого хода; человеку "
        "ничего не открывает и каталог не спрашивает. Пишется для всех, а не только "
        "для названных аккаунтов: иначе «стоп» из хода до внесения в список пропал бы"
    ),
}


def _reads_the_flag(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and node.value == "PLAN_ENGINE_ENABLED":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "PLAN_ENGINE_ENABLED":
            return True
        if isinstance(node, ast.Name) and node.id == "PLAN_ENGINE_ENABLED":
            return True
    return False


def _app_sources() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in _APPS.rglob("*.py"):
        relative = path.relative_to(_APPS).as_posix()
        if "/tests/" in f"/{relative}" or relative.endswith("conftest.py"):
            continue
        out[relative] = path.read_text(encoding="utf-8")
    return out


def test_c1_only_the_lock_and_the_turn_verdict_record_read_the_flag() -> None:
    sources = _app_sources()
    assert len(sources) > 500, "перепись прочла подозрительно мало файлов"
    readers = {name for name, source in sources.items() if _reads_the_flag(source)}
    assert readers == set(FLAG_READERS), (
        "PLAN_ENGINE_ENABLED читается мимо замка приёмки: вход Плана обязан "
        "спрашивать apps.orchestrator.plan_access.plan_open_for / plan_open_in. "
        f"Лишние: {sorted(readers - set(FLAG_READERS))}; "
        f"пропали: {sorted(set(FLAG_READERS) - readers)}"
    )


def test_c1_the_reader_of_the_flag_reads_a_form_that_the_census_sees() -> None:
    """Перепись видит и строку, и атрибут, и имя — и не видит упоминания в прозе."""
    assert _reads_the_flag('getattr(settings, "PLAN_ENGINE_ENABLED", False)') is True
    assert _reads_the_flag("settings.PLAN_ENGINE_ENABLED") is True
    assert _reads_the_flag("from x import PLAN_ENGINE_ENABLED\nPLAN_ENGINE_ENABLED") is True
    assert _reads_the_flag('"""Флаг ``PLAN_ENGINE_ENABLED`` выключен."""\nx = 1') is False


def test_c2_no_entry_keeps_a_bare_flag_check_of_its_own() -> None:
    """Прежние проверки «включён ли механизм» ушли вместе с именами."""
    sources = _app_sources()
    for name in ("engine_enabled", "plan_engine_enabled"):
        users = sorted(
            path
            for path, source in sources.items()
            if any(
                (isinstance(node, ast.Name) and node.id == name)
                or (isinstance(node, ast.FunctionDef) and node.name == name)
                or (isinstance(node, ast.alias) and node.name == name)
                for node in ast.walk(ast.parse(source))
            )
        )
        assert users == [], (name, users)
