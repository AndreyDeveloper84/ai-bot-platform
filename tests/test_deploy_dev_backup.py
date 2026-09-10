"""Пилот не выкладывается без снимка базы.

Шага не было НИКОГДА: на боевой тропе (`deploy.yml`) бэкап стоит с DRF-1578,
а `deploy-dev.yml` — та, по которой едет пилот, — грепом по ``pg_dump``
не находила ничего. При этом выкладывается несколько раз в день именно
пилот, а `main` не выкладывался с 19.05: защита стояла там, где её некому
применять.

Тесты здесь дешёвые и держат ровно то, что делает шаг шагом, а не
украшением:

* он есть;
* он стоит ДО сборки и миграций — иначе снимок описывает уже изменённую
  схему, то есть не то состояние, в которое откатываются;
* пустой дамп блокирует выкладку, а не проезжает с предупреждением;
* у снимков есть ротация с названной глубиной — бэкап без неё это способ
  забить диск и остановить пилот тем же, чем он должен был его защитить.

Тот же довод, что у `apps/catalog/tests/test_beat_schedule.py`: шаг можно
выронить посторонней правкой, и без этих проверок весь репозиторий остался
бы зелёным.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_DEV = REPO_ROOT / ".github" / "workflows" / "deploy-dev.yml"

BACKUP_STEP = "Postgres backup before deploy"
RESTART_STEP = "Pull + rebuild + restart dev services"


@pytest.fixture(scope="module")
def steps() -> list[dict]:
    doc = yaml.safe_load(DEPLOY_DEV.read_text(encoding="utf-8"))
    return doc["jobs"]["deploy"]["steps"]


@pytest.fixture(scope="module")
def backup(steps: list[dict]) -> dict:
    named = [s for s in steps if s.get("name") == BACKUP_STEP]
    assert len(named) == 1, f"шагов «{BACKUP_STEP}» должно быть ровно один, найдено {len(named)}"
    return named[0]


def test_the_pilot_path_takes_a_snapshot_at_all(backup: dict) -> None:
    """Положительная стража: шаг не только назван, но и снимает дамп."""

    assert "pg_dump" in backup["run"]


def test_the_snapshot_is_taken_before_anything_changes_on_the_box(steps: list[dict]) -> None:
    """Порядок — это и есть предмет.

    Снимок после сборки и `migrate` описывал бы схему, уже изменённую этой
    же выкладкой, то есть не то состояние, в которое откатываются.
    """

    names = [s.get("name") for s in steps]
    assert names.index(BACKUP_STEP) < names.index(RESTART_STEP)


def test_an_empty_dump_blocks_the_deploy(backup: dict) -> None:
    """`pg_dump`, упавший внутри пайпа, оставляет валидный gzip-заголовок в
    два десятка байт: файл есть, размер ненулевой, данных нет. Порог —
    единственное, что отличает это от успеха."""

    run = backup["run"]
    assert "-ge 1024" in run, "порог размера снят — пустой дамп проедет за успех"
    assert "exit 1" in run, "шаг обязан ронять выкладку, а не предупреждать"


def test_the_step_is_not_softened_into_a_warning(backup: dict) -> None:
    """`continue-on-error` превратил бы блокирующий шаг в уведомление."""

    assert backup.get("continue-on-error") in (None, False)


def test_snapshots_are_rotated_with_a_named_depth(backup: dict) -> None:
    """Ротации нет и на боевой тропе — перенести шаг как есть значило бы
    перенести и способ забить диск. Пилот выкладывается несколько раз в
    день, снимки растут линейно."""

    run = backup["run"]
    match = re.search(r"KEEP=(\d+)", run)
    assert match, "глубина ротации должна быть названа числом, а не подразумеваться"
    depth = int(match.group(1))
    assert 1 <= depth <= 50, f"глубина {depth} вне разумного: диск не резиновый, откат не вечен"
    assert "rm -f" in run, "глубина названа, а удаления нет — ротация только на словах"


def test_the_credentials_are_read_from_the_container_not_written_here(backup: dict) -> None:
    """Пилот поднимается с `--env-file .env.staging`, которого в репозитории
    нет. Вписанные сюда имя пользователя и базы были бы догадкой, а
    блокирующий шаг на догадке блокирует выкладку, а не защищает её."""

    run = backup["run"]
    assert "$POSTGRES_USER" in run
    assert "$POSTGRES_DB" in run


def test_the_backup_addresses_the_same_stack_the_deploy_restarts(
    backup: dict, steps: list[dict]
) -> None:
    """Снимок не того проекта — это снимок, которого нет.

    Сравнение с соседним шагом, а не с константой: если выкладку однажды
    переведут на другой проект compose, разойтись они не имеют права.
    """

    restart = next(s for s in steps if s.get("name") == RESTART_STEP)
    project = re.search(r"-p (\S+)", restart["run"])
    assert project, "у шага перезапуска не найден проект compose"
    assert f"-p {project.group(1)}" in backup["run"]


def test_the_backup_path_needs_no_root(backup: dict) -> None:
    """Файловые операции шага идут БЕЗ sudo — иначе шаг ставит на sudoers.

    Замер на пилоте 10.09.2026: ``/var/backups`` — root:root, каталога
    ``ayla-bot-staging`` там нет, ``touch`` от пользователя выкладки
    отвечает ``Permission denied``. Первая редакция обходила это через
    ``sudo mkdir`` и ``sudo tee``, и это была ставка на незаглянутое: все
    восемь вызовов sudo в этом воркфлоу — про docker, то есть правило
    sudoers вполне может быть выдано ровно на docker.

    Тогда блокирующий шаг упал бы на ``mkdir`` и остановил выкладку —
    механизм, заведённый ради защиты, сломал бы то, что защищает.

    Домашний каталог снимает вопрос: прав достаточно по построению.
    ``sudo docker`` остаётся — он единственный, чья работа на хосте
    доказана тем, что выкладка идёт.
    """
    run = backup["run"]
    commands = [
        line.strip()
        for line in run.splitlines()
        # Комментарии внутри ssh-строки начинаются с экранированной решётки;
        # они законно упоминают sudo, объясняя, почему его тут нет.
        if "sudo" in line and not line.strip().lstrip("\\").startswith("#")
    ]
    non_docker = [c for c in commands if "sudo docker" not in c]
    assert non_docker == [], f"sudo вне docker: {non_docker}"
    assert "$HOME/" in run, "каталог бэкапов не в домашнем каталоге пользователя"


def test_the_write_is_probed_before_the_dump(backup: dict) -> None:
    """Проверка записи стоит ДО ``pg_dump`` и делается тем же пользователем.

    Без неё первым о потерянных правах сказал бы ``pg_dump`` — то есть
    отказ пришёл бы из шага, к правам отношения не имеющего, и читался бы
    как сбой базы.
    """
    # Порядок ищется по КОМАНДАМ, а не по тексту. Первая версия этой
    # проверки сравнивала позиции подстрок и упала на моём же комментарии
    # «...и ДО pg_dump»: сторож читал рассказ о коде вместо кода. Дефект
    # был в стороже, и починен сторож, а не порядок в шаге.
    lines = [
        ln.strip()
        for ln in backup["run"].splitlines()
        if not ln.strip().lstrip("\\").startswith("#")
    ]
    probe = next((i for i, ln in enumerate(lines) if "write-probe" in ln), -1)
    dump = next((i for i, ln in enumerate(lines) if "pg_dump" in ln), -1)

    assert probe != -1, "проверки записи нет вовсе"
    assert dump != -1, "шаг перестал делать дамп — проверять нечего"
    assert probe < dump, f"проверка записи ({probe}) стоит ПОСЛЕ дампа ({dump})"
    assert "не записываем пользователем выкладки" in backup["run"], (
        "отказ по правам не назван словами"
    )
