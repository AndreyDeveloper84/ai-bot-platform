"""Тесты для `tools/state_check.py` — проверялки файлов состояния.

Три вещи, ради которых эти тесты написаны, и все три пойманы на живом
материале при первом сборе `docs/state/`:

**Разбор блока.** Формат держится не кодом, а договорённостью: поля пишутся
по-русски и двоеточием. Если разбор тихо потеряет поле `проверка`, факт уедет
в список «без проверки» и перестанет проверяться, оставаясь на вид записанным.
Худший из возможных исходов для этого инструмента.

**Красная лампа.** Проверялка ценна ровно в тот день, когда факт разошёлся с
кодом. Тест держит именно это: тот же блок, то же место, изменённое
`ожидание` — и вердикт обязан стать «протухло», а код возврата ненулевым.

**Белый список.** Поле `проверка` приезжает из markdown-файла, который правит
человек. Без белого списка такой файл был бы исполняемым кодом. Здесь
проверяется, что не-git и не-gh команда не исполняется вовсе, а помечается как
отклонённая — и что при этом безобидный `git grep -c` НЕ попадает под запрет
глобальных флагов (`-c` у git значит две разные вещи: `git -c key=val` —
инъекция конфигурации, `git grep -c` — счётчик совпадений; их разводит позиция,
а не имя, и один неверный список ломает все проверки разом).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

# `tools/` не пакет (нет `__init__.py`) — импорт через путь, тем же приёмом,
# что и `tests/tools/test_env_guard.py`.
sys.path.insert(0, str(_PROJECT_ROOT / "tools"))
import state_check  # type: ignore[import-not-found]  # noqa: E402


_BLOCK = """\
# Заголовок файла

## Работает

### Гейт продажи мастера — одно определение на весь код
статус:   РАБОТАЕТ
где:      apps/catalog/master_state.py:278
проверка: git grep -c "AVAILABLE = ADMITTED" origin/dev -- apps/catalog/master_state.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1540

## Замер не снят

### Раскладка мастеров на боевом пилоте
статус:   НЕ ЗАМЕРЕНО
проверка: нечем
почему:   доступа к прод-БД у этого слоя нет
снято:    2026-09-08 (замер чужой)
"""


# --------------------------------------------------------------------------
# Разбор блока
# --------------------------------------------------------------------------
def test_parses_all_fields_of_a_block() -> None:
    facts = state_check.parse_facts(_BLOCK, Path("STATE-TEST.md"))

    assert len(facts) == 2
    first = facts[0]
    assert first.title == "Гейт продажи мастера — одно определение на весь код"
    assert first.section == "Работает"
    assert first.status == "РАБОТАЕТ"
    assert first.where == "apps/catalog/master_state.py:278"
    assert first.check == (
        'git grep -c "AVAILABLE = ADMITTED" origin/dev -- apps/catalog/master_state.py'
    )
    assert first.expect == "=1"
    assert first.taken == "2026-09-08 @ 8c276fa (ai-bot-platform)"
    assert first.task == "DRF-1540"
    assert first.repo == "ai-bot-platform"


def test_block_without_a_repo_in_taken_defaults_to_this_checkout() -> None:
    facts = state_check.parse_facts(_BLOCK, Path("STATE-TEST.md"))
    assert facts[1].repo == "замер чужой"  # скобки прочитаны как есть, не выдуманы


def test_nothing_to_check_block_keeps_its_reason() -> None:
    facts = state_check.parse_facts(_BLOCK, Path("STATE-TEST.md"))
    second = facts[1]
    assert second.check == state_check.NOTHING_TO_CHECK
    assert second.why == "доступа к прод-БД у этого слоя нет"

    result = state_check.check_fact(second, {"замер чужой": None}, timeout=5)
    assert result.verdict == state_check.UNCHECKED
    assert "прод-БД" in result.reason


def test_block_without_a_check_field_is_reported_as_a_format_violation() -> None:
    text = (
        "## Работает\n\n"
        "### Факт без способа перепроверки\n"
        "статус:   РАБОТАЕТ\n"
        "где:      apps/whatever.py:1\n"
    )
    facts = state_check.parse_facts(text, Path("STATE-TEST.md"))
    orphans = state_check.missing_check(facts)

    assert len(orphans) == 1
    assert orphans[0].title == "Факт без способа перепроверки"


# --------------------------------------------------------------------------
# Распознавание протухшего факта
# --------------------------------------------------------------------------
def test_a_fact_whose_expectation_no_longer_matches_is_stale(tmp_path: Path) -> None:
    """Тот же блок, то же место — изменено только `ожидание`.

    Файл `pyproject.toml` в корне репозитория содержит ровно одну строку
    `[project]`. Факт с `ожидание: =1` держится; тот же факт с `ожидание: =7`
    обязан стать протухшим — это и есть красная лампа инструмента.

    Ревизия здесь `HEAD`, а не `origin/dev`, которую требует формат живых
    файлов состояния: в чекауте CI (`actions/checkout` тянет только голову PR)
    ссылки `origin/dev` может не быть вовсе, и тест краснел бы на упавшей
    команде вместо несошедшегося числа — то есть по правильной причине, но не
    по той, которую он проверяет. Что упавшая команда тоже считается
    протухшей, держит отдельный тест ниже.
    """
    holds = state_check.parse_facts(
        "## Работает\n\n"
        "### Проект объявлен один раз\n"
        "статус:   РАБОТАЕТ\n"
        "где:      pyproject.toml\n"
        'проверка: git grep -c "^\\[project\\]$" HEAD -- pyproject.toml\n'
        "ожидание: =1\n"
        "снято:    2026-09-08 @ 8c276fa (ai-bot-platform)\n",
        tmp_path / "STATE-TEST.md",
    )[0]
    repos = {"ai-bot-platform": _PROJECT_ROOT}

    assert state_check.check_fact(holds, repos, timeout=60).verdict == state_check.HOLDS

    holds.expect = "=7"
    stale = state_check.check_fact(holds, repos, timeout=60)
    assert stale.verdict == state_check.STALE
    assert stale.got == "1"  # проверялка называет, ЧТО получила, а не только «не сошлось»


def test_stale_fact_makes_the_run_return_nonzero(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "STATE-FAKE.md").write_text(
        "## Работает\n\n"
        "### Заведомо неверное число\n"
        "статус:   РАБОТАЕТ\n"
        "где:      pyproject.toml\n"
        'проверка: git grep -c "^\\[project\\]$" HEAD -- pyproject.toml\n'
        "ожидание: =999\n"
        "снято:    2026-09-08 @ 8c276fa (ai-bot-platform)\n",
        encoding="utf-8",
    )

    assert state_check.main(["--dir", str(state_dir)]) == 1


def test_a_command_that_falls_over_counts_as_stale_not_as_unchecked() -> None:
    """`git show` несуществующей ревизии — это разошедшийся факт, а не «нечем».

    Молчаливо записать такое в «не проверено» значило бы прятать поломку в
    разделе, который читают как «тут и не собирались проверять».
    """
    fact = state_check.parse_facts(
        "## Работает\n\n"
        "### Ссылка на ревизию, которой нет\n"
        "статус:   РАБОТАЕТ\n"
        "где:      nowhere\n"
        "проверка: git show no-such-revision-at-all:pyproject.toml\n"
        "ожидание: есть\n"
        "снято:    2026-09-08 @ 8c276fa (ai-bot-platform)\n",
        Path("STATE-TEST.md"),
    )[0]

    result = state_check.check_fact(fact, {"ai-bot-platform": _PROJECT_ROOT}, timeout=60)
    assert result.verdict == state_check.STALE
    assert "команда упала" in result.reason


# --------------------------------------------------------------------------
# Белый список
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "python -c print(1)",
        "curl https://example.invalid",
        "bash -lc git grep foo",
        "git push origin dev",
        "git commit -m wat",
        "git config --global user.name nope",
        "gh api /repos/o/r",
        "gh pr merge 1487",
        "./tools/state_check.py",
    ],
)
def test_commands_outside_the_whitelist_are_rejected_not_run(command: str) -> None:
    argv, why = state_check.classify_command(command)
    assert argv is None, f"команда {command!r} не должна исполняться"
    assert why


def test_rejected_command_never_reaches_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отклонение обязано случиться ДО запуска, а не после."""
    called: list[list[str]] = []
    monkeypatch.setattr(
        state_check,
        "run_command",
        lambda argv, cwd, timeout: called.append(argv) or (0, "", ""),
    )

    fact = state_check.parse_facts(
        "## Работает\n\n"
        "### Команда не из белого списка\n"
        "статус:   РАБОТАЕТ\n"
        "где:      nowhere\n"
        "проверка: rm -rf apps\n"
        "ожидание: нет\n"
        "снято:    2026-09-08 @ 8c276fa (ai-bot-platform)\n",
        Path("STATE-TEST.md"),
    )[0]

    result = state_check.check_fact(fact, {"ai-bot-platform": _PROJECT_ROOT}, timeout=5)

    assert result.verdict == state_check.REJECTED
    assert called == [], "отклонённая команда всё-таки была запущена"


def test_rejected_command_makes_the_run_return_nonzero(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "STATE-FAKE.md").write_text(
        "## Работает\n\n"
        "### Команда не из белого списка\n"
        "статус:   РАБОТАЕТ\n"
        "где:      nowhere\n"
        "проверка: curl https://example.invalid\n"
        "ожидание: есть\n"
        "снято:    2026-09-08 @ 8c276fa (ai-bot-platform)\n",
        encoding="utf-8",
    )

    assert state_check.main(["--dir", str(state_dir)]) == 2


@pytest.mark.parametrize(
    "command",
    [
        'git grep -c "AVAILABLE" origin/dev -- apps/catalog/master_state.py',
        "git ls-tree -r --name-only origin/dev -- docs",
        "git log --oneline -5 origin/dev",
        "git merge-base --is-ancestor abc def",
        "git show origin/dev:pyproject.toml",
        "gh run list --workflow deploy-dev.yml --limit 1",
        "gh pr view 1487 --json state",
    ],
)
def test_reading_commands_pass_the_whitelist(command: str) -> None:
    argv, why = state_check.classify_command(command)
    assert argv is not None, f"{command!r} отвергнута: {why}"


def test_pager_escape_hatch_of_git_grep_is_still_blocked() -> None:
    """`git grep -O<pager>` запускает произвольную программу — это не чтение."""
    argv, why = state_check.classify_command("git grep -O/bin/sh pattern origin/dev -- apps")
    assert argv is None
    assert "флаг" in why


def test_a_multiline_check_is_refused() -> None:
    argv, why = state_check.classify_command('git grep "a\nb" origin/dev')
    assert argv is None
    assert "многострочной" in why


# --------------------------------------------------------------------------
# Сравнение с ожиданием
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("expect", "output", "ok"),
    [
        ("есть", "apps/foo.py\n", True),
        ("есть", "", False),
        ("нет", "", True),
        ("нет", "apps/foo.py\n", False),
        # формат `git grep -c` — суммируются счётчики, а не строки
        (">0", "origin/dev:apps/a.py:3\n", True),
        ("=3", "origin/dev:apps/a.py:3\n", True),
        ("=5", "origin/dev:apps/a.py:3\norigin/dev:apps/b.py:2\n", True),
        ("0", "", True),
        (">0", "", False),
        # формат `git ls-tree` — считаются строки
        ("=2", "apps/a.py\napps/b.py\n", True),
        # подстрока
        ("2026-05-19", "completed\tsuccess\t...\t2026-05-19T02:38:07Z\n", True),
        ("2026-09-08", "completed\tsuccess\t...\t2026-05-19T02:38:07Z\n", False),
    ],
)
def test_expectation_forms(expect: str, output: str, ok: bool) -> None:
    got, _ = state_check.compare(expect, output)
    assert got is ok


# --------------------------------------------------------------------------
# Живые файлы состояния
# --------------------------------------------------------------------------
def test_every_shipped_fact_carries_a_check_field() -> None:
    """Правило README, приколоченное к тесту: строка без `проверка` не кладётся."""
    facts: list[state_check.Fact] = []
    for path in sorted((_PROJECT_ROOT / "docs" / "state").glob("STATE-*.md")):
        facts.extend(state_check.parse_facts(path.read_text(encoding="utf-8"), path))

    assert facts, "в docs/state/ не нашлось ни одного факта — разбор сломан"
    orphans = state_check.missing_check(facts)
    assert orphans == [], [f"{f.location} {f.title}" for f in orphans]


def test_every_shipped_check_passes_the_whitelist() -> None:
    """Команды в поставляемых файлах не должны отвергаться самой проверялкой."""
    problems: list[str] = []
    for path in sorted((_PROJECT_ROOT / "docs" / "state").glob("STATE-*.md")):
        for fact in state_check.parse_facts(path.read_text(encoding="utf-8"), path):
            if fact.check.strip().lower() == state_check.NOTHING_TO_CHECK:
                assert fact.why, f"{fact.location}: `нечем` без объяснения `почему`"
                continue
            argv, why = state_check.classify_command(fact.check)
            if argv is None:
                problems.append(f"{fact.location}: {why}")
    assert problems == []
