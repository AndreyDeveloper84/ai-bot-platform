"""Страж поздней выкладки (DRF-2073): ``tools/ci/deploy_sha_is_dev_head.sh``.

# Что сломалось 17.09.2026

Перезапущенный старый ci-прогон на ``41bc4514`` завершился ПОЗЖЕ выкладки
``7e80fa98``; его ``workflow_run`` запустил deploy-dev, и стенд откатился на
коммит назад. Прежний ``::warning::голова dev ушла вперёд…`` предупреждал и
выкладывал — откат допускался по построению.

# Что здесь сторожится

Скрипт получает три аргумента (событие, проверенный SHA, голова dev) и
отвечает кодом выхода: ``0`` выкладывать, ``3`` superseded, ``1`` отказ по
форме. Он нарочно не ходит в git сам — иначе гонку можно было бы проверить
только на живом workflow, а он срабатывает лишь с ``dev``.

# Почему первый узел — положительный контроль

«SHA позади головы → 3» зеленел бы и на скрипте, который отвечает ``3`` на
всё. Поэтому ПЕРВЫМ стоит узел «SHA = голова → 0»: без него «superseded»
неотличим от «сторож всегда отказывает». Узел про dispatch — вторая половина
того же контроля: старый SHA, но ручной запуск, должен пройти — правило
владельца dispatch не касается.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "tools" / "ci" / "deploy_sha_is_dev_head.sh"

OLD = "41bc451400000000000000000000000000000000"
HEAD = "7e80fa9800000000000000000000000000000000"

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"), reason="скрипт bash; сторожится на Linux-раннере CI"
)


def _run(event: str, sha: str, head: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), event, sha, head],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


# ─── положительный контроль — и это предмет ──────────────────────────────────


def test_the_head_of_dev_is_deployed() -> None:
    """``0`` на голове: без этого узла «3» ниже ничего не доказывал бы."""
    r = _run("workflow_run", HEAD, HEAD)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "выкладывается" in r.stdout
    assert "superseded" not in r.stdout


def test_a_manual_dispatch_is_not_subject_to_the_rule() -> None:
    """Тот же старый SHA, но ``workflow_dispatch`` → ``0``: dispatch берёт голову
    той ветки, на которой нажали, и правило владельца его не касается."""
    r = _run("workflow_dispatch", OLD, HEAD)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "workflow_dispatch" in r.stdout
    assert "superseded" not in r.stdout


# ─── сам случай 17.09 ────────────────────────────────────────────────────────


def test_a_verified_sha_behind_the_head_is_superseded_not_deployed() -> None:
    """Старый прогон, завершившийся позже: ``3`` и notice, не error.

    Это не поломка, а поздний прогон, которому нечего выкладывать; красный
    звал бы человека чинить то, что уже верно.
    """
    r = _run("workflow_run", OLD, HEAD)
    assert r.returncode == 3, r.stdout + r.stderr
    assert "::notice::superseded" in r.stdout
    assert OLD in r.stdout and HEAD in r.stdout  # оба SHA рядом, глазом
    assert "::error::" not in r.stdout


# ─── форма: слепой сторож не выкладывает ─────────────────────────────────────


@pytest.mark.parametrize(
    ("event", "sha", "head"),
    [
        ("workflow_run", "", HEAD),  # пустой SHA дал бы `git checkout ''`
        ("workflow_run", "deadbeef", HEAD),  # короткий
        ("workflow_run", HEAD, ""),  # ls-remote вернул пустое — голова неизвестна
        ("workflow_run", HEAD, "refs/heads/dev"),  # взяли не ту колонку ls-remote
        ("push", HEAD, HEAD),  # событие, для которого правило не определено
    ],
)
def test_malformed_input_refuses_to_deploy(event: str, sha: str, head: str) -> None:
    r = _run(event, sha, head)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "::error::" in r.stdout
    assert "superseded" not in r.stdout
