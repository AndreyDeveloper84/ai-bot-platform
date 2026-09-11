"""Выкладка называет актора и пишет файлы от него — DRF-1646.

Два предмета, и второй тоньше первого.

**Кто исполняет шаг — в лог.** Сегодня шаги молча мешают имя из секрета и
повышение прав и нигде не называют актора. В дереве выкладки при этом лежат
объекты ДВУХ учёток, созданные в одну минуту одного прогона; две трети
root-объектов — служебные файлы git и рабочие копии исходников, а git под
повышением прав не зовёт ни один шаг. Косвенно это выводили три часа; шаг
знает ответ по построению и обязан его назвать.

**Где раскрывается подстановка.** Внутри ssh-строки ``$(id -un)`` раскроет
РАННЕР, а не хост, — и шаг напечатает ``runner`` вместо пользователя
выкладки, то есть назовёт не того актора уверенным тоном. Правильная форма
экранирована: ``\\$(id -un)``.

Эта ошибка была допущена при написании самой правки и поймана не чтением.
Тест ниже — чтобы её не повторил следующий.

**Контейнер пишет в /app от пользователя выкладки.** Каталог проекта
смонтирован внутрь, контейнер работает от root, и всё, что он создаёт,
остаётся на хосте root-овым (замер 11.09.2026: 174 root-объекта в
``staticfiles/``). Оба вызова ``run --rm`` обязаны нести ``--user``: тот,
что пишет, и тот, что сегодня не пишет. Второй — чтобы не осталось канала,
из которого дефект вернётся.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/deploy-dev.yml"

#: Команды, которые пишут в дерево выкладки или меняют состояние рядом с ним.
#: Список НАМЕРЕННО не делится на «пишущие» и «читающие» подкоманды docker:
#: такое знание устаревает молча, а правило без исключений проверяемо.
WRITERS = ("git ", "tar ", "docker compose", "rm -rf", "mkdir ", "mv ")

#: Команды, которым мало сменить пользователя — им нужен ещё и его $HOME.
NEEDS_HOME = ("git ", "docker compose")


def code_lines(step: dict) -> list[str]:
    """Строки шага БЕЗ комментариев.

    Первая версия проверки актора падала на МОЁМ ЖЕ комментарии «``id -un``
    выводил имя…» — сторож читал рассказ о коде вместо кода. За сутки это
    случилось трижды, и каждый раз чинился сторож, а не предмет.
    """

    out = []
    for line in (step.get("run") or "").splitlines():
        bare = line.strip().lstrip("\\")
        if not bare.startswith("#"):
            out.append(bare)
    return out


@pytest.fixture(scope="module")
def steps() -> list[dict]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return data["jobs"]["deploy"]["steps"]


@pytest.fixture(scope="module")
def ssh_steps(steps: list[dict]) -> list[dict]:
    found = [s for s in steps if "ssh -i ~/.ssh/dev_deploy" in (s.get("run") or "")]
    # Положительный контроль: обход обязан находить известные шаги. Ноль
    # здесь читался бы как «нарушений нет», а значил бы «ищу не там».
    assert len(found) >= 3, f"ssh-шагов найдено {len(found)} — это не похоже на выкладку"
    return found


def test_every_ssh_step_names_the_actor(ssh_steps: list[dict]) -> None:
    """Называет — значит ПЕЧАТАЕТ, а не упоминает в пояснении.

    Первая редакция искала подстроку в тексте шага целиком и зеленела бы на
    шаге, который перестал печатать актора вовсе, — достаточно было
    комментария о нём. Носитель проверки был, предмета в ней не было.
    """

    silent = [
        s.get("name")
        for s in ssh_steps
        if not any("id -u" in ln and "echo" in ln for ln in code_lines(s))
    ]

    assert silent == [], f"шаги не печатают, от кого идут: {silent}"


def test_the_actor_is_resolved_on_the_host_not_on_the_runner(
    ssh_steps: list[dict],
) -> None:
    """Подстановка обязана быть экранирована.

    ``$(id -un)`` внутри двойных кавычек ssh-строки раскрывает раннер:
    шаг напечатает ``runner`` и назовёт не того актора — уверенно и
    неверно. Это ровно тот дефект, ради поиска которого печать и заводилась.
    """
    wrong = []
    for step in ssh_steps:
        run = step.get("run") or ""
        # Неэкранированное вхождение — то, перед которым НЕТ обратного слэша.
        for i in range(len(run)):
            if run.startswith("$(id -un)", i) and (i == 0 or run[i - 1] != "\\"):
                wrong.append(step.get("name"))
                break

    assert wrong == [], (
        f"подстановка раскроется на раннере, а не на хосте: {wrong}. Нужна форма \\\\$(id -un)"
    )


def test_both_container_runs_write_as_the_deploy_user(steps: list[dict]) -> None:
    """``--user`` у ОБОИХ вызовов ``run --rm``, а не только у пишущего.

    ``migrate`` сегодня в дерево не пишет, и строка у него выглядит лишней.
    Она не лишняя: канал, оставленный открытым, — это тот самый случай,
    когда чинят запись и оставляют чтение.
    """
    # Комментарии отбрасываются. Первая версия этого сторожа считала
    # командами строки «run --rm web uses the freshly built image» и
    # «\`run --rm web\` берёт СВЕЖЕСОБРАННЫЙ образ» — то есть читала рассказ
    # о коде вместо кода. Дефект был в стороже, и починен сторож.
    runs = []
    for step in steps:
        for line in (step.get("run") or "").splitlines():
            bare = line.strip().lstrip("\\")
            if bare.startswith("#"):
                continue
            if "run --rm" in bare:
                runs.append(bare)

    assert len(runs) >= 2, f"вызовов run --rm найдено {len(runs)} — ищу не там"
    without = [r for r in runs if "--user" not in r]
    assert without == [], f"контейнер запишет файлы от root: {without}"


def test_the_long_lived_services_are_not_switched_to_another_user() -> None:
    """``user:`` в compose-файле салона — НЕ то же самое, и его там быть не должно.

    ``user:`` на сервисе подействовал бы и на долгоживущие web/worker/celery,
    а это смена режима контура, а не починка владельцев файлов. Правка
    точечная: только разовые ``run --rm``.
    """
    compose = WORKFLOW.resolve().parents[2] / "docker-compose.staging.yml"
    if not compose.exists():  # pragma: no cover — файл есть в репозитории
        pytest.skip("docker-compose.staging.yml не найден")

    # Файл несёт тег ``!override`` (расширение compose), которого
    # ``safe_load`` не знает. Тег снимается текстом ДО разбора: он не меняет
    # форму данных, а разбирать весь файл текстом нельзя — нам нужна
    # структура, иначе слово «user:» из комментария сойдёт за настройку.
    import re

    raw = re.sub("!override", "", compose.read_text(encoding="utf-8"))
    data = yaml.safe_load(raw) or {}
    services = data.get("services") or {}
    assert services, "сервисов не найдено — ищу не там"

    switched = [name for name, body in services.items() if (body or {}).get("user")]
    assert switched == [], (
        f"сервисам задан user: {switched} — это меняет режим контура, "
        "а чинить надо владельцев файлов разовых команд"
    )


def test_the_container_writes_as_the_tree_owner_not_as_the_caller(
    steps: list[dict],
) -> None:
    """``--user`` берёт владельца ДЕРЕВА, а не того, кто зашёл.

    Первая редакция брала ``$(id -u)`` — зовущего. Это был прокси для
    «владелец дерева», верный ровно при допущении, что зовущий и есть
    владелец. Допущение ложно: выкладка заходит root-ом, ``id -u`` давал 0,
    и ``--user`` честно передавал ноль.

    **Флаг работал ровно так, как написан, и не достигал цели.** Замер
    11.09.2026: 154 root-объекта в ``staticfiles`` — ровно столько, сколько
    ``collectstatic`` и сообщил. Сторож «флаг присутствует» такого не ловит:
    флаг присутствует.
    """
    user_flags = []
    for step in steps:
        for line in (step.get("run") or "").splitlines():
            bare = line.strip().lstrip("\\")
            if bare.startswith("#"):
                continue
            if "--user" in bare:
                user_flags.append(bare)

    assert user_flags, "флага --user нет вовсе — ищу не там"
    caller_based = [f for f in user_flags if "id -u" in f]
    assert caller_based == [], (
        "--user снова берёт uid ЗОВУЩЕГО. Выкладка заходит root-ом, и это "
        f"передаст ноль: {caller_based}"
    )
    assert all("OWNER_UID" in f for f in user_flags), (
        f"--user должен брать владельца дерева: {user_flags}"
    )


def test_the_owner_is_resolved_before_it_is_used(steps: list[dict]) -> None:
    """Определение владельца стоит ДО первого ``--user``, иначе пусто.

    Порядок значим: пустая подстановка дала бы ``--user :``, и docker отказал
    бы отказом, к владельцам отношения не имеющим на вид.
    """
    for step in steps:
        run = step.get("run") or ""
        lines = [ln.strip().lstrip("\\") for ln in run.splitlines()]
        code = [ln for ln in lines if not ln.startswith("#")]
        where_set = next((i for i, ln in enumerate(code) if ln.startswith("OWNER_UID=")), -1)
        where_used = next((i for i, ln in enumerate(code) if "--user" in ln), -1)
        if where_used == -1:
            continue
        assert where_set != -1, "шаг зовёт --user, не определив владельца"
        assert where_set < where_used, "владелец определяется ПОСЛЕ использования"


def test_the_tarball_does_not_restore_owners_from_the_archive(steps: list[dict]) -> None:
    """``tar --no-same-owner``: под root иначе переносятся чужие номера.

    GNU tar под root по умолчанию берёт uid/gid из архива. Замер 11.09.2026:
    шесть объектов в ``apps/miniapp/dist`` с uid 1001, которого в ``passwd``
    хоста нет вовсе — то есть номер приехал из архива раннера.

    Этот шаг гонит tar ПРЯМО по ssh, docker в нём не участвует, поэтому
    ``--user`` на него не влияет по построению. Правка ему нужна своя.
    """
    extracts = []
    for step in steps:
        for line in (step.get("run") or "").splitlines():
            bare = line.strip().lstrip("\\")
            if bare.startswith("#"):
                continue
            if "tar" in bare and "-xf" in bare:
                extracts.append(bare)

    assert extracts, "распаковки tar не найдено — ищу не там"
    unguarded = [e for e in extracts if "--no-same-owner" not in e]
    assert unguarded == [], f"tar восстановит владельцев из архива: {unguarded}"


def test_the_actor_is_printed_as_a_number_not_a_name(ssh_steps: list[dict]) -> None:
    """Имя актора совпадает с секретом и вычёркивается из лога.

    ``id -un`` печатал имя, равное значению ``DEV_USER``, и GitHub заменял
    его на ``***``. Строка, заведённая ради снятия догадки об акторе,
    догадку не снимала — и выглядела рабочим диагностическим выводом.
    Носитель был, сведений в нём не было.

    Ноль секретом не является. Число к тому же полезнее имени: именно оно
    уходит в ``--user``.
    """

    numeric = [s for s in ssh_steps if any("id -u)" in ln for ln in code_lines(s))]
    assert numeric, "ни один шаг не печатает uid числом"

    masked = [s.get("name") for s in ssh_steps if any("id -un" in ln for ln in code_lines(s))]
    assert masked == [], f"актор печатается именем и будет замаскирован: {masked}"


def logical_lines(step: dict) -> list[str]:
    """Строки шага, склеенные по продолжению.

    Команда, разнесённая обратным слэшем на несколько строк, — ОДНА команда,
    и обёртка стоит в её начале. Построчный разбор поймал на этом сборку
    образов: ``sudo -u ... env`` в первой строке, ``docker compose`` во
    второй. Без склейки сторож ругался бы на верный код — и, что хуже,
    подталкивал бы «чинить» его переносом, пряча от себя настоящие случаи.
    """

    out: list[str] = []
    buf = ""
    for ln in code_lines(step):
        buf = f"{buf} {ln}".strip() if buf else ln
        if buf.endswith("\\"):
            buf = buf[:-1].rstrip()
            continue
        out.append(buf)
        buf = ""
    if buf:
        out.append(buf)
    return out


def _unwrapped(step: dict) -> list[str]:
    """Команды шага, которые пишут в дерево НЕ от владельца."""

    return [
        ln for ln in logical_lines(step) if any(w in ln for w in WRITERS) and "sudo -u" not in ln
    ]


def test_every_write_on_the_box_runs_as_the_tree_owner(ssh_steps: list[dict]) -> None:
    """Мера обязана покрывать ВСЕ каналы записи, а не самый заметный.

    ``--user`` управляет только тем, что происходит внутри контейнера.
    Основную массу файлов пишет git, запущенный по ssh, и до него флаг
    docker не достаёт по построению. Замер 11.09.2026 после выкладки с
    ``--user``: чужих объектов стало не меньше, а БОЛЬШЕ (298 → 434), и 96
    из них создала сама выкладка — fetch, checkout, распаковка.

    Сторож на «есть ``--user``» на такой течи зеленеет: флаг есть. Здесь
    проверяется КЛАСС — каждая пишущая команда идёт от владельца дерева, —
    а не присутствие одного флага.
    """

    caught = {s.get("name"): _unwrapped(s) for s in ssh_steps}
    caught = {name: lines for name, lines in caught.items() if lines}

    assert caught == {}, "в дерево выкладки пишут не от владельца: " + "; ".join(
        f"шаг «{name}»: {ln}" for name, lines in caught.items() for ln in lines
    )


def test_git_and_docker_get_the_owners_home(ssh_steps: list[dict]) -> None:
    """Сменить пользователя мало: без ``-H`` останется чужой ``$HOME``.

    ``sudo`` по умолчанию не меняет ``HOME``, и команда отработала бы от
    нужного пользователя с чужими настройками: git полез бы в
    ``/root/.gitconfig``, docker — в ``/root/.docker/config.json``. Тот же
    класс, что и прежде: мера сработала, а предмет остался прежним.
    """

    homeless = [
        (s.get("name"), ln)
        for s in ssh_steps
        for ln in code_lines(s)
        if "sudo -u" in ln and any(c in ln for c in NEEDS_HOME) and " -H " not in ln
    ]

    assert homeless == [], f"команде нужен HOME владельца, а -H нет: {homeless}"


def test_the_dist_directories_are_checked_before_they_are_removed(
    steps: list[dict],
) -> None:
    """Отказ с названной причиной вместо Permission denied внутри ``rm``.

    Удалить содержимое каталога может тот, у кого права на САМ каталог.
    11.09.2026 восемь каталогов дерева принадлежали root, среди них ``dist``
    и ``dist.prev``: под владельцем ``rm -rf`` упал бы из шага, который к
    правам отношения не имеет.

    Проверять это самим root бессмысленно — ``test -w`` из-под root истинно
    всегда, и проверка сказала бы «можно» про каталог, который владельцу не
    отдан. Вопрос обязан задаваться тому, кто будет писать.
    """

    ship = next((s for s in steps if "dist.new" in (s.get("run") or "")), None)
    assert ship is not None, "шаг выкладки мини-аппа не найден — сторож смотрит не туда"

    lines = code_lines(ship)
    probe = next((i for i, ln in enumerate(lines) if "test -w" in ln), -1)
    wipe = next((i for i, ln in enumerate(lines) if "rm -rf dist.prev" in ln), -1)

    assert probe != -1, "нет проверки прав на dist/dist.prev перед удалением"
    assert wipe != -1, "шаг перестал чистить dist.prev — проверять нечего"
    assert probe < wipe, f"проверка прав ({probe}) стоит ПОСЛЕ удаления ({wipe})"
    assert "sudo -u" in lines[probe], (
        "права проверяет root, а он пишет куда угодно — спрашивать надо владельца"
    )


@pytest.mark.parametrize(
    ("line", "flagged"),
    [
        ('sudo -u "$OWNER" -H git fetch origin', False),
        ("git fetch origin", True),
        ('sudo -u "$OWNER" rm -rf dist.prev', False),
        ("rm -rf dist.prev", True),
        ("sudo docker compose up -d", True),
        ("cd /srv && test -s dist/index.html", False),
    ],
)
def test_the_rule_itself(line: str, flagged: bool) -> None:
    """Положительный и отрицательный контроль на сам разбор.

    Без них сторож, разучившийся видеть пишущие команды, зеленел бы на любом
    файле, и «нарушений нет» было бы неотличимо от «не проверялось».
    Отрицательные случаи не менее важны: правило обязано быть узким, иначе
    оно начнёт требовать владельца там, где ничего не пишется.
    """

    assert bool(_unwrapped({"run": line})) is flagged


def test_a_continued_command_is_read_as_one() -> None:
    """Обёртка в первой строке относится и к продолжению.

    Иначе сторож ругается на верный код, а починка «переносом» прячет от
    него настоящие случаи.
    """

    wrapped = {"run": 'sudo -u "$OWNER" -H env FOO=1 \\\n  docker compose up -d'}
    bare = {"run": "env FOO=1 \\\n  docker compose up -d"}

    # Присутствие впереди отсутствия: если склейка развалит строку в ничто,
    # «нарушений нет» будет значить «разбирать было нечего».
    assert logical_lines(wrapped) == ['sudo -u "$OWNER" -H env FOO=1 docker compose up -d']
    assert logical_lines(bare) == ["env FOO=1 docker compose up -d"]

    assert _unwrapped(wrapped) == []
    assert _unwrapped(bare) != []


def test_the_only_write_root_does_itself_is_handing_the_tree_back(
    steps: list[dict],
) -> None:
    """Исключение из правила «пишет владелец» — ровно одно, и оно названо.

    Правило «все пишущие команды идут от владельца» имеет один осмысленный
    предел: вернуть дерево владельцу может только тот, у кого права, то есть
    root. Эта запись меняет не содержимое, а принадлежность, и она —
    единственное, что root делает сам.

    Исключение без стража превращается в дыру: следующий ``chown`` в этом
    файле проехал бы молча. Поэтому граница пинится с обеих сторон —
    ``chown`` ровно один во всём воркфлоу, он возвращает дерево ИМЕННО
    владельцу и стоит ПЕРЕД проверкой прав.

    Почему вообще нужен: уборка руками не держится. Каталоги, переданные
    владельцу 11.09.2026 в 06:12, снова стали root-овыми после выкладки
    06:37–06:52, шедшей ещё от root, и набор таких каталогов не фиксирован —
    каждый слитый PR с новым каталогом добавлял свой.
    """

    ship = next((s for s in steps if "dist.new" in (s.get("run") or "")), None)
    assert ship is not None, "шаг выкладки мини-аппа не найден — сторож смотрит не туда"

    everywhere = [ln for s in steps for ln in logical_lines(s) if "chown" in ln]
    assert len(everywhere) == 1, f"chown должен быть ровно один — граница исключения: {everywhere}"

    lines = logical_lines(ship)
    hand_back = next((i for i, ln in enumerate(lines) if "chown" in ln), -1)
    probe = next((i for i, ln in enumerate(lines) if "test -w" in ln), -1)

    assert hand_back != -1, "нечего вернуть владельцу — самолечения нет"
    assert probe != -1, "проверка прав пропала — стеречь порядок не у чего"
    assert hand_back < probe, (
        f"возврат дерева ({hand_back}) стоит ПОСЛЕ проверки ({probe}) — "
        "первая же выкладка упадёт на том, что умеет починить сама"
    )
    assert "$OWNER" in lines[hand_back], (
        "chown отдаёт дерево не владельцу, а кому-то названному вслепую"
    )
    assert "::notice::" in lines[hand_back], "молчаливый chown меняет права и не оставляет следа"
