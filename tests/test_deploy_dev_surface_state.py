"""Шаг публикации состояния поверхности не роняет следующую выкладку — DRF-1661.

Две причины, по которым первая редакция шага ломала бы **следующий** прогон,
и обе — про файл, а не про команду (находка владельца ``deploy-dev.yml``,
11.09.2026):

1. Файл писался в **отслеживаемый** путь. Следующая выкладка начинается с
   ``git checkout dev && git pull --ff-only`` — при изменённом отслеживаемом
   файле checkout откажет «local changes would be overwritten», и упадёт
   шаг ДО миграций: вся выкладка не пойдёт.
2. Писал ``exec -T web`` — процесс **внутри** контейнера, uid root.
   ``sudo -u`` на хосте внутрь не достаёт: кто пишет в примонтированное
   дерево, решает ``--user``. Итог — root-овый файл в дереве владельца.

Оба условия здесь проверяются по коду шага, а не по его описанию.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/deploy-dev.yml"
STEP_NAME = "Publish surface state (bot) into the deploy tree"


@pytest.fixture(scope="module")
def step() -> dict:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    found = [s for s in data["jobs"]["deploy"]["steps"] if s.get("name") == STEP_NAME]
    assert len(found) == 1, f"шаг «{STEP_NAME}» найден {len(found)} раз"
    return found[0]


def _code(step: dict) -> list[str]:
    return [
        ln.strip().lstrip("\\")
        for ln in (step.get("run") or "").splitlines()
        if not ln.strip().lstrip("\\").startswith("#")
    ]


def test_the_step_comes_after_smoke_and_before_the_alert() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    names = [s.get("name") for s in data["jobs"]["deploy"]["steps"]]
    smoke = names.index("Smoke — /readyz/ on dev (port 8014)")
    alert = names.index("Telegram smoke alert (informational)")
    assert smoke < names.index(STEP_NAME) < alert


def test_the_write_path_is_ignored_by_git_and_not_tracked(step: dict) -> None:
    """Путь берётся из кода шага, а не из этого теста: переименуют
    переменную — тест это увидит, а не продолжит проверять старый путь."""
    code = "\n".join(_code(step))
    m = re.search(r"OUT=(\S+)", code)
    assert m, "шаг не объявляет OUT=<путь>"
    path = m.group(1)
    assert "--write" in code and "$OUT" in code, "запись идёт не в OUT"

    ignored = subprocess.run(
        ["git", "check-ignore", "-q", path], cwd=ROOT, check=False, capture_output=True
    )
    assert ignored.returncode == 0, f"{path} не игнорируется git — следующий checkout dev упадёт"
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", path], cwd=ROOT, check=False, capture_output=True
    )
    assert tracked.returncode != 0, f"{path} отслеживается — это замер, а не исходник"


def test_the_container_writes_as_the_tree_owner_not_via_exec(step: dict) -> None:
    """``run --rm --user "$OWNER_UID:$OWNER_GID"``, а не ``exec -T``:
    ``sudo -u`` на хосте внутрь контейнера не достаёт."""
    code = _code(step)
    assert len(code) > 5, code  # присутствие — прежде отсутствия: шаг не пуст
    run_lines = [ln for ln in code if "run --rm" in ln]
    assert len(run_lines) == 1, run_lines
    assert not any("exec -T" in ln for ln in code), "exec пишет от root внутри контейнера"
    assert '--user \\"\\$OWNER_UID:\\$OWNER_GID\\"' in run_lines[0] or (
        '--user "$OWNER_UID:$OWNER_GID"' in run_lines[0]
    ), run_lines[0]
    # Владелец файла печатается ЧИСЛОМ рядом с uid дерева — расхождение
    # видно в логе, а не после падения следующей выкладки.
    assert any("stat -c %u" in ln and "OWNER_UID" in ln for ln in code)
