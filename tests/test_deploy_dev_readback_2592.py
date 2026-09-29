"""DRF-2592 — у красного readback два смысла, и шаг называет, какой из них.

«Выкладке нельзя верить» (дерево хоста ≠ проверенному SHA) и «посмотреть не
удалось» (ssh вернул 255 — отказ транспорта) до правки были одним красным:
под ``bash -e`` обрыв ssh ронял шаг без единого слова. 28.09 выкладка
``2fb6a50c`` прошла и жила, а красным стал только этот шаг.

Узлы исполняют ``run`` шага так, как его исполняет GitHub (``bash -e``), с
подменёнными ``ssh`` и ``sleep`` в ``PATH``. Узел «шаг красный» прошёл бы при
обоих исходах и не различал бы ничего — поэтому пара: откат краснеет словами
про откат, обрыв — словами про непроверенность, и слова не пересекаются.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/deploy-dev.yml"
STEP_PREFIX = "Readback — deployed tree is the verified SHA"

VERIFIED = "a" * 40
OTHER = "b" * 40

ROLLBACK_WORDS = "ОТКАТ ИЛИ ЧУЖАЯ ЗАПИСЬ"
UNVERIFIED_WORDS = "ПРОВЕРКА НЕ ВЫПОЛНЕНА, выкладка не опровергнута"

_SSH_STUB = """#!/usr/bin/env bash
echo x >> "$STUB_DIR/ssh.calls"
n=$(wc -l < "$STUB_DIR/ssh.calls")
mode="$STUB_MODE"
if [ "$mode" = "down-then-tree" ]; then
  if [ "$n" -eq 1 ]; then mode=down; else mode=tree; fi
fi
case "$mode" in
  down) echo "Connection closed by 10.0.0.1 port 22" >&2; exit 255 ;;
  remote-fail) echo "fatal: unable to access origin" >&2; exit 1 ;;
  tree) printf '%s\\n%s\\n%s\\n' "$STUB_TREE" "$STUB_TREE" ancestor ;;
esac
"""

_SLEEP_STUB = """#!/usr/bin/env bash
echo "$1" >> "$STUB_DIR/sleep.calls"
"""


@pytest.fixture(scope="module")
def script() -> str:
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["deploy"]["steps"]
    found = [s for s in steps if (s.get("name") or "").startswith(STEP_PREFIX)]
    assert len(found) == 1, [s.get("name") for s in found]
    return found[0]["run"]


@pytest.fixture(scope="module")
def bash() -> str:
    path = shutil.which("bash")
    assert path, "bash нужен: GitHub исполняет шаг именно им"
    return path


def _run(bash: str, script: str, tmp_path: Path, mode: str, tree: str = VERIFIED):
    stubs = tmp_path / "bin"
    stubs.mkdir(parents=True)
    for name, body in (("ssh", _SSH_STUB), ("sleep", _SLEEP_STUB)):
        f = stubs / name
        f.write_text(body, encoding="utf-8", newline="\n")
        f.chmod(0o755)
    (tmp_path / "step.sh").write_text(script, encoding="utf-8", newline="\n")
    env = {
        **os.environ,
        "PATH": f"{stubs}{os.pathsep}{os.environ.get('PATH', '')}",
        "STUB_DIR": str(tmp_path),
        "STUB_MODE": mode,
        "STUB_TREE": tree,
        "DEV_HOST": "dev.example",
        "DEV_USER": "deploy",
        "DEV_DEPLOY_PATH": "/srv/bot",
        "DEPLOY_SHA": VERIFIED,
        "HEAD_AT_GUARD": VERIFIED,
        "EVENT_NAME": "push",
    }
    proc = subprocess.run(
        [bash, "-e", str(tmp_path / "step.sh")],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )

    def calls(name: str) -> int:
        f = tmp_path / f"{name}.calls"
        return len(f.read_text(encoding="utf-8").splitlines()) if f.exists() else 0

    return proc.returncode, proc.stdout + proc.stderr, calls("ssh"), calls("sleep")


class TestTheTwoRedsAreNamedDifferently:
    def test_a_rolled_back_tree_is_red_with_rollback_words(self, bash, script, tmp_path) -> None:
        rc, out, ssh, _ = _run(bash, script, tmp_path, "tree", tree=OTHER)
        assert rc == 1
        assert ROLLBACK_WORDS in out
        assert UNVERIFIED_WORDS not in out
        assert ssh == 1

    def test_ssh_that_never_answers_is_red_with_unverified_words(
        self, bash, script, tmp_path
    ) -> None:
        rc, out, ssh, sleeps = _run(bash, script, tmp_path, "down")
        assert rc == 1
        assert UNVERIFIED_WORDS in out
        assert "код 255" in out
        assert ROLLBACK_WORDS not in out
        # Настойчивее одного входа: три попытки, пауза между ними, не после последней.
        assert (ssh, sleeps) == (3, 2)

    def test_the_two_reds_never_share_words(self, bash, script, tmp_path) -> None:
        _, rollback, _, _ = _run(bash, script, tmp_path / "r", "tree", tree=OTHER)
        _, unverified, _, _ = _run(bash, script, tmp_path / "u", "down")
        assert ROLLBACK_WORDS in rollback and UNVERIFIED_WORDS in unverified
        assert ROLLBACK_WORDS not in unverified and UNVERIFIED_WORDS not in rollback


class TestRetryIsOnlyForTheTransport:
    def test_one_dropped_connection_then_a_good_tree_is_green(self, bash, script, tmp_path) -> None:
        rc, out, ssh, sleeps = _run(bash, script, tmp_path, "down-then-tree")
        assert rc == 0, out
        assert "стенд на голове dev" in out
        assert (ssh, sleeps) == (2, 1)

    def test_a_failed_remote_command_is_unverified_and_not_retried(
        self, bash, script, tmp_path
    ) -> None:
        rc, out, ssh, sleeps = _run(bash, script, tmp_path, "remote-fail")
        assert rc == 1
        assert UNVERIFIED_WORDS in out
        assert "кодом 1" in out
        assert ROLLBACK_WORDS not in out
        # Повтор — только на 255: ответ хоста не лечится повторным входом.
        assert (ssh, sleeps) == (1, 0)

    def test_the_verified_tree_is_green_on_the_first_try(self, bash, script, tmp_path) -> None:
        rc, out, ssh, sleeps = _run(bash, script, tmp_path, "tree")
        assert rc == 0, out
        assert "стенд на голове dev" in out
        assert (ssh, sleeps) == (1, 0)
