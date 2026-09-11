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
    silent = [s.get("name") for s in ssh_steps if "id -un" not in (s.get("run") or "")]

    assert silent == [], f"шаги не называют, от кого идут: {silent}"


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

    def code_lines(step: dict) -> list[str]:
        # Комментарии отбрасываются. Первая версия этой проверки падала на
        # МОЁМ ЖЕ комментарии «``id -un`` выводил имя…» — сторож читал
        # рассказ о коде вместо кода. Третий такой случай за сутки, и
        # каждый раз чинился сторож, а не предмет.
        out = []
        for line in (step.get("run") or "").splitlines():
            bare = line.strip().lstrip("\\")
            if not bare.startswith("#"):
                out.append(bare)
        return out

    numeric = [s for s in ssh_steps if any("id -u)" in ln for ln in code_lines(s))]
    assert numeric, "ни один шаг не печатает uid числом"

    masked = [s.get("name") for s in ssh_steps if any("id -un" in ln for ln in code_lines(s))]
    assert masked == [], f"актор печатается именем и будет замаскирован: {masked}"
