"""Страж: ``GH_DEPLOY_TOKEN`` необязателен для получения ``ayla-ai-core``.

Репозиторий ``AndreyDeveloper84/ayla-ai-core`` публичный — решение владельца
от 04.09.2026, запись в ``OPEN_DECISIONS.md`` §22 (реестр решений владельца
лежит в корне рабочей области, вне этого репозитория); проверено анонимным
запросом (``"private": false``). Закреплённый SHA выкачивается вообще без
учётных данных.

До DRF-1466 шаг настройки git-авторизации в ``ci.yml`` и ``replay.yml``
печатал ``::error::`` и делал ``exit 1``, если секрет пуст. Прогноз «fetch
will fail» был ложным, а цена — реальной: прогоны Dependabot (#1360, #1362,
#1363) умирали на этом страже за ~2 минуты, ни разу не дойдя до самой
выкачки. У Dependabot своя область секретов, ``GH_DEPLOY_TOKEN`` в неё не
входит и входить не должен.

Утверждение «страж больше не останавливает прогон» — отрицательное, поэтому
здесь оно проверяется положительно и на тех же данных: скрипт шага
ВЫПОЛНЯЕТСЯ, обеими ветками.

* без токена — код возврата 0, переписывание URL не записано;
* с токеном — код возврата 0, переписывание записано, значение токена в
  вывод не попало.

Вторая половина не менее важна первой: сделать токен необязательным легко
ценой поломки приватного пути, а решение владельца звучит «пока публичными».
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# Собирается из кусков намеренно: цельная строка, похожая на токен, — это
# то, что detect-secrets обязан ловить, и приучать его к исключениям ради
# теста нельзя.
FAKE_VALUE = "gh" + "p_" + ("0" * 36)

# Файлы, где живёт шаг. Оба должны вести себя одинаково: replay.yml делает
# `uv sync --frozen`, который трогает ВЕСЬ lock-файл, включая git+ deps,
# даже когда apps/replay/ ничего оттуда не импортирует.
WORKFLOW_FILES = ["ci.yml", "replay.yml"]

# Заранее кладётся в подставной ~/.gitconfig; зачем — см. `_run_step`.
GITCONFIG_MARKER = "[user]\n\tname = drf1466-marker\n"


def _auth_step(workflow_name: str) -> dict:
    """Шаг, настраивающий git-авторизацию для ayla-ai-core, из workflow'а.

    Ищем по содержанию, а не по имени шага: имя — свободный текст, и
    привязка к нему сделала бы страж хрупким к безобидному переименованию.
    """
    doc = yaml.safe_load((WORKFLOWS / workflow_name).read_text(encoding="utf-8"))
    found = [
        step
        for job in doc["jobs"].values()
        for step in job.get("steps", [])
        if "GH_DEPLOY_TOKEN" in (step.get("env") or {}) and "insteadOf" in (step.get("run") or "")
    ]
    assert len(found) == 1, (
        f"{workflow_name}: ожидался ровно один шаг с GH_DEPLOY_TOKEN и insteadOf, "
        f"найдено {len(found)}. Если шаг раздвоился — раздвоится и поведение."
    )
    return found[0]


@functools.lru_cache(maxsize=1)
def _usable_bash() -> str | None:
    """Путь к bash, который ПЕРЕДАЁТ окружение дочернему процессу.

    На раннере это просто ``/bin/bash``. На машине разработчика под
    Windows ``shutil.which("bash")`` нередко находит bash из WSL — а тот
    живёт в своём пространстве окружения и не видит ни
    ``GH_DEPLOY_TOKEN``, ни ``GIT_CONFIG_GLOBAL``, которые мы задаём.
    Тест на таком bash проходил бы, ничего не проверив, и вдобавок мог бы
    записать переписывание URL в НАСТОЯЩИЙ ~/.gitconfig разработчика.
    Поэтому кандидат допускается, только если он вернул наш пробный
    маркер.
    """
    candidates = [shutil.which("bash"), "/bin/bash"]
    git = shutil.which("git")
    if git:
        # Git for Windows кладёт свой bash рядом: <..>/Git/cmd/git.exe ->
        # <..>/Git/bin/bash.exe. В отличие от WSL он живёт в окружении
        # вызвавшего процесса.
        candidates.append(str(Path(git).resolve().parent.parent / "bin" / "bash.exe"))
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        probe = subprocess.run(  # noqa: S603 — фиксированная команда, без ввода извне
            [candidate, "-c", 'printf %s "$AYLA_BASH_ENV_PROBE"'],
            capture_output=True,
            text=True,
            env={**os.environ, "AYLA_BASH_ENV_PROBE": "ok"},
            timeout=30,
        )
        if probe.returncode == 0 and probe.stdout.strip() == "ok":
            return candidate
    return None


def _run_step(script: str, token: str, tmp_path: Path) -> subprocess.CompletedProcess:
    """Выполнить скрипт шага так, как его выполнит раннер.

    ``-e`` — не украшение: GitHub Actions запускает ``run:`` как
    ``bash --noprofile --norc -e -o pipefail``. Без ``-e`` упавший
    ``git config`` посреди скрипта остался бы незамеченным, потому что
    последней командой стоит ``echo``, и шаг вернул бы 0. Ровно так этот
    тест однажды и «прошёл».

    ``GIT_CONFIG_GLOBAL`` уводится в tmp: ``git config --global`` внутри
    скрипта не должен трогать настоящий ~/.gitconfig разработчика.
    """
    bash = _usable_bash()
    assert bash is not None, "нет пригодного bash — тест должен был быть пропущен"
    gitconfig = tmp_path / "gitconfig"
    # Маркер кладётся ДО прогона намеренно. Без него «переписывания URL в
    # конфиге нет» выглядит одинаково и когда шаг действительно ничего не
    # записал, и когда читается не тот файл (GIT_CONFIG_GLOBAL не долетел) —
    # а тогда `git config --global` ушёл бы в НАСТОЯЩИЙ ~/.gitconfig
    # разработчика, и тест этого не заметил бы.
    gitconfig.write_text(GITCONFIG_MARKER, encoding="utf-8")
    env = {
        **os.environ,
        "GH_DEPLOY_TOKEN": token,
        "GIT_CONFIG_GLOBAL": str(gitconfig),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    return subprocess.run(  # noqa: S603 — скрипт берётся из файла в этом же репозитории
        [bash, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=60,
    )


@pytest.mark.parametrize("workflow_name", WORKFLOW_FILES)
def test_step_never_fails_the_run_on_a_missing_token(workflow_name: str) -> None:
    """Статически: в шаге нет ни ``exit 1``, ни ``::error::``.

    Выполнение ниже проверяет то же самое поведенчески. Оба нужны:
    исполнение ловит логику, текст ловит намерение — вернуть `exit 1` в
    ветку, куда не заходит локальный прогон, иначе слишком легко.
    """
    script = _auth_step(workflow_name)["run"]
    # Присутствие — впереди отсутствия. Пустой или не тот текст дал бы
    # «нет exit 1» даром; эта строка падает первой и по имени.
    assert '-n "$GH_DEPLOY_TOKEN"' in script, (
        f"{workflow_name}: переписывание URL должно происходить только при "
        "непустом значении — иначе в конфиг уходит https://@github.com/, "
        "собранный из значения, которое шаг ни разу не посмотрел."
    )
    assert "exit 1" not in script, (
        f"{workflow_name}: шаг снова останавливает прогон при отсутствии токена. "
        "ayla-ai-core — публичный репозиторий, выкачка пройдёт анонимно; "
        "честный сигнал о недоступной зависимости даёт `uv sync`, а не догадка "
        "перед ним (DRF-1466)."
    )
    assert "::error::" not in script, (
        f"{workflow_name}: отсутствие необязательного токена помечено как ошибка. "
        "Предупреждение — да, ошибка — нет (DRF-1466)."
    )


@pytest.mark.skipif(_usable_bash() is None, reason="нет bash, передающего окружение")
@pytest.mark.parametrize("workflow_name", WORKFLOW_FILES)
def test_step_succeeds_without_a_token_and_writes_no_rewrite(
    workflow_name: str, tmp_path: Path
) -> None:
    """Без токена: шаг проходит, переписывание URL не записано."""
    script = _auth_step(workflow_name)["run"]
    result = _run_step(script, token="", tmp_path=tmp_path)

    assert result.returncode == 0, (
        f"{workflow_name}: шаг упал без токена (rc={result.returncode}).\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    written = (tmp_path / "gitconfig").read_text(encoding="utf-8")
    assert GITCONFIG_MARKER.strip() in written.strip(), (
        f"{workflow_name}: прочитан не тот ~/.gitconfig, что видел шаг — маркер "
        f"не вернулся. Прочитано: {written!r}. Пока это не сойдётся, "
        "утверждение ниже ничего не проверяет."
    )
    assert "insteadOf" not in written, (
        f"{workflow_name}: переписывание URL записано при пустом токене — "
        f"в конфиг ушло: {written!r}"
    )


@pytest.mark.skipif(_usable_bash() is None, reason="нет bash, передающего окружение")
@pytest.mark.parametrize("workflow_name", WORKFLOW_FILES)
def test_step_still_configures_auth_when_a_token_is_present(
    workflow_name: str, tmp_path: Path
) -> None:
    """С токеном: приватный путь не сломан, и значение не утекло в вывод.

    Владелец сказал «пока публичными». Механика обязана ожить без переделки,
    если видимость закроют, — эта половина проверки и есть то, что делает
    первую половину безопасной.
    """
    script = _auth_step(workflow_name)["run"]
    result = _run_step(script, token=FAKE_VALUE, tmp_path=tmp_path)

    assert result.returncode == 0, (
        f"{workflow_name}: шаг упал с токеном (rc={result.returncode}).\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    written = (tmp_path / "gitconfig").read_text(encoding="utf-8")
    assert "insteadOf" in written and FAKE_VALUE in written, (
        f"{workflow_name}: с непустым токеном переписывание URL не записано — "
        f"в конфиг ушло: {written!r}"
    )
    assert FAKE_VALUE not in result.stdout + result.stderr, (
        f"{workflow_name}: значение токена напечатано в журнал прогона. "
        "Маскирование раннером — не оправдание: шаг не должен его печатать."
    )
