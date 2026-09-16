"""Проводка env → settings → ``release_flag()`` (флаг снятия барьера RECOMMEND).

Форма взята у ``test_shadow_flag_from_env.py`` не ради симметрии: вопрос ровно
тот же, и ответить на него может только ОТДЕЛЬНЫЙ процесс. Подмена ``settings``
фикстурой доказала бы сама себя, а спрашивается здесь, читает ли **загрузка
настроек** окружение — то есть появляется ли атрибут вообще.

Замер 14.09 показал цену пропущенной проводки: ``DRE_SHADOW_ENABLED`` не читался
из ``config/`` нигде, и переменная в ``.env.staging`` дала бы ``read_default`` —
снаружи это выглядело бы как «включили, а строк нет». У барьера ошибка
зеркальная и хуже: барьер, чей флаг не доходит из окружения, **снять нельзя**,
и причина будет невидима — оператор увидит выставленную переменную и запрет,
который на неё не отвечает.

Отдельно проверяется, что ``malformed`` не сваливается в ``read_default``:
исход у них один (барьер стоит), а причины разные, и только вторая — чьё-то
решение.
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
    "from apps.orchestrator.decision_readiness.release import release_flag; "
    "r = release_flag(); print('FLAG', r.value, r.source.value)"
)


def _flag_in_fresh_process(value: str | None) -> str:
    env = {k: v for k, v in os.environ.items() if k != "DRE_RECOMMEND_RELEASE_ENABLED"}
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    if value is not None:
        env["DRE_RECOMMEND_RELEASE_ENABLED"] = value
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
def test_environment_reaches_the_release_flag(value, expected):
    assert _flag_in_fresh_process(value) == expected
