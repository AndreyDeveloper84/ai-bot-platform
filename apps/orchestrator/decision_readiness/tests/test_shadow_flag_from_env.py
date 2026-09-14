"""Проводка env → settings → ``shadow_flag()`` (флаг теневого режима из окружения).

Замер 14.09: ``DRE_SHADOW_ENABLED`` не читался из окружения нигде в ``config/``,
и переменная в ``.env.staging`` давала бы ``read_default`` — теневой режим не
включился бы, а снаружи это выглядело бы как «включили, строк нет».

Проверяется в ОТДЕЛЬНОМ процессе: подмена ``settings`` фикстурой доказала бы
сама себя, а вопрос ровно в том, читает ли загрузка настроек окружение.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]

PROBE = (
    "import django; django.setup(); "
    "from apps.orchestrator.decision_readiness.shadow import shadow_flag; "
    "r = shadow_flag(); print('FLAG', r.value, r.source.value)"
)


def _flag_in_fresh_process(value: str | None) -> str:
    env = {k: v for k, v in os.environ.items() if k != "DRE_SHADOW_ENABLED"}
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    if value is not None:
        env["DRE_SHADOW_ENABLED"] = value
    done = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    lines = [line for line in done.stdout.splitlines() if line.startswith("FLAG ")]
    assert lines, (done.returncode, done.stdout[-2000:], done.stderr[-2000:])
    return lines[-1]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", "FLAG True settings"),
        ("1", "FLAG True settings"),
        ("false", "FLAG False settings"),
        ("maybe", "FLAG False malformed"),
        (None, "FLAG False read_default"),
    ],
)
def test_environment_reaches_the_shadow_flag(value, expected):
    assert _flag_in_fresh_process(value) == expected
