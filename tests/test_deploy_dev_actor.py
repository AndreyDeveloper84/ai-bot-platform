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
