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


def test_root_never_escalates_it_only_descends(backup: dict) -> None:
    """Каждый ``sudo`` в шаге — понижение до владельца, ни одного повышения.

    Этот тест ПЕРЕВЁРНУТ 11.09.2026. Прежняя редакция запрещала любой sudo
    вне docker: ``sudo`` тогда значил повышение до root ради /var/backups, и
    ставка на sudoers была ставкой на незаглянутое. После #1586 входящий по
    ssh — root, и ``sudo`` значит обратное: понижение до владельца дерева.
    Правило поменялось, и сторож, верный прежнему, красил бы верный код.

    Что стережётся теперь: (1) ни одного ``sudo`` без ``-u`` — root ничего
    не делает от себя, кроме входа; (2) ``-H`` при каждом — иначе HOME
    останется /root и docker с git полезут в чужие конфиги; (3) каталог
    снимков — в доме ВЛАДЕЛЬЦА, а не входящего: снимок в /root (drwx------)
    дотянулся бы только до root, а откатывать будет тот, кто владеет деревом.
    """
    run = backup["run"]
    commands = [
        line.strip()
        for line in run.splitlines()
        if "sudo" in line and not line.strip().lstrip("\\").startswith("#")
    ]
    assert commands, "в шаге нет ни одного sudo — значит пишет root, и тест смотрит не туда"

    escalating = [c for c in commands if "sudo -u" not in c]
    assert escalating == [], f"sudo без -u — повышение до root: {escalating}"

    homeless = [c for c in commands if "sudo -u" in c and " -H " not in c]
    assert homeless == [], f"sudo -u без -H — чужой HOME: {homeless}"

    assert "$OWNER_HOME/" in run, "каталог снимков не в доме владельца дерева"
    assert "$HOME/" not in run, "каталог снимков привязан к HOME входящего, а он root"


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


def test_the_step_says_who_writes_and_where(backup: dict) -> None:
    """Шаг называет пользователя и каталог САМ, до дампа.

    10.09.2026 три часа ушло на косвенный вывод того, под кем идёт
    выкладка. Каждый замер был верен и относился не к тому субъекту:
    права мерили у одной учётки, владельца — у каталога, оболочку — не ту.
    В рабочем дереве пилота при этом лежат файлы обеих учёток, созданные
    в одну минуту одного прогона.

    Вопрос «кто исполняет шаг» шаг знает по построению. Печать снимает его
    навсегда и кладёт ответ в лог, а не в переписку.

    Порядок значим: печать до дампа, иначе при падении дампа ответа не
    будет ровно тогда, когда он нужнее всего.

    И назвать мало — надо назвать тем, что переживёт лог. Имя учётки равно
    значению секрета ``DEV_USER``, GitHub вычёркивает его, и 11.09.2026 в
    логе стояло «пользователь ***»: носитель есть, сведений нет. Поэтому
    число, а не имя (DRF-1646).
    """
    lines = [
        ln.strip()
        for ln in backup["run"].splitlines()
        if not ln.strip().lstrip("\\").startswith("#")
    ]
    # Ловим КЛАСС, а не сегодняшнее написание. Первая редакция искала
    # подстроку "id -un" — то есть стерегла ровно ту форму, которая потом и
    # оказалась дефектной: имя равно значению секрета DEV_USER, GitHub
    # вычёркивает его, и в логе остаётся «пользователь ***». Сторож на
    # написании зеленел бы на пустом носителе.
    #
    # Условий два, и второе отрицательное: актор назван ЧИСЛОМ и НЕ назван
    # именем. "id -u" подстрокой входит в "id -un", поэтому ищем со скобкой.
    said = next((i for i, ln in enumerate(lines) if "id -u)" in ln), -1)
    by_name = [ln for ln in lines if "id -un" in ln]
    dump = next((i for i, ln in enumerate(lines) if "pg_dump" in ln), -1)

    assert said != -1, "шаг не называет актора числом: ожидается $(id -u)"
    assert by_name == [], f"актор назван именем и будет замаскирован: {by_name}"
    assert dump != -1, "шаг перестал делать дамп — проверять нечего"
    assert said < dump, f"актор печатается ({said}) ПОСЛЕ дампа ({dump})"
    assert "каталог" in backup["run"], "путь бэкапа не назван в выводе"


def test_the_snapshot_file_is_written_by_the_owner_not_by_a_redirect(backup: dict) -> None:
    """Перенаправление ``>`` делает вызывающая оболочка, а она — root.

    Обернуть ``docker compose`` в ``sudo -u`` мало: пайп ``… | gzip > файл``
    открывает файл ТА оболочка, что читает строку, то есть root, и снимок
    лёг бы root-ом в каталог владельца. Пишет ``tee`` от владельца.

    Найдено пробой 11.09.2026: подмена ``tee`` обратно на ``>`` не красила
    ни одного стража. ``tee`` не в списке пишущих команд, а стороже
    «sudo только понижает» строка без sudo не видна. Дыра названа и закрыта
    здесь.
    """
    lines = [
        ln.strip()
        for ln in backup["run"].splitlines()
        if "pg_dump" in ln and not ln.strip().lstrip("\\").startswith("#")
    ]
    assert len(lines) == 1, f"строка дампа должна быть одна: {lines}"
    dump = lines[0]

    assert "tee" in dump and "sudo -u" in dump.split("tee")[0], (
        "файл снимка пишет не владелец через tee"
    )
    assert '> \\"\$BACKUP_PATH' not in dump, (
        "файл снимка открывает перенаправлением вызывающая оболочка — root"
    )
