"""Запрос замера «до» исполняется и печатает то, что обещает (DRF-2513).

Зачем узел на SQL-файле. Запрос уедет на стенд и будет запущен человеком, у
которого нет ни этого дерева, ни возможности отладить его на месте. Переименуют
столбец — и замер сломается ТАМ, где починить его дороже всего, причём в день,
когда число уже нельзя снять задним числом (мост DRF-2511 его уничтожает).

Поэтому проверяется не «текст файла похож на SQL», а:

* запрос ИСПОЛНЯЕТСЯ на настоящей схеме (ловит переименование столбца и таблицы);
* в выводе есть период — число без охвата процитировать нельзя;
* в выводе есть ЗНАМЕНАТЕЛЬ и разрез по происхождению — то, ради чего он написан;
* запрос ничего не пишет: в нём нет ни одного изменяющего слова.

Чего этот узел НЕ проверяет: правильность самих чисел на живых данных. Это
вопрос стенда, и подменять его фикстурой нельзя — фикстура даст число про
фикстуру.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.db import connection

SQL_PATH = Path(__file__).resolve().parents[3] / "docs" / "measurements" / "DRF-2513-before-sql.sql"

#: Слова, которых в запросе быть не должно. Замер не пишет — и это проверяется
#: текстом, а не обещанием в комментарии.
WRITING_WORDS = (
    "insert",
    "update",
    "delete",
    "truncate",
    "drop",
    "alter",
    "create",
    "grant",
    "revoke",
)


def _sql() -> str:
    return SQL_PATH.read_text(encoding="utf-8")


def _statement() -> str:
    """Запрос без комментариев — именно он уходит в базу."""
    body = re.sub(r"--[^\n]*", "", _sql())
    return body.strip()


def test_the_file_is_where_the_report_says_it_is():
    assert SQL_PATH.exists(), f"замерный запрос не найден: {SQL_PATH}"


def test_the_query_writes_nothing():
    lowered = _statement().lower()

    found = [word for word in WRITING_WORDS if re.search(rf"\b{word}\b", lowered)]

    assert found == [], f"в замерном запросе есть изменяющие слова: {found}"


@pytest.mark.django_db
def test_the_query_runs_on_the_real_schema():
    """Исполнение на настоящей схеме — то, чего не даст чтение глазами.

    Пустая база здесь уместна: проверяется, что запрос ВЫПОЛНИМ (имена таблиц и
    столбцов живы), а не какие числа он даёт.
    """
    with connection.cursor() as cursor:
        cursor.execute(_statement())
        rows = cursor.fetchall()
        columns = [c[0] for c in cursor.description]

    assert columns == ["measured_at", "window_start", "metric", "value"], columns
    # На пустой базе постоянные величины всё равно печатаются — иначе «ничего не
    # нашлось» и «запрос не туда посмотрел» были бы неразличимы.
    metrics = {row[2] for row in rows}
    assert any("люди, активные за окно" in m for m in metrics), sorted(metrics)
    assert any("ЗНАМЕНАТЕЛЬ" in m for m in metrics), sorted(metrics)


@pytest.mark.django_db
def test_the_output_carries_its_own_scope():
    """Период — в каждой строке, а не в шапке отчёта.

    Число, вырванное из строки вывода, обязано нести охват с собой: иначе его
    процитируют без периода, и «после» померится на другом окне.
    """
    with connection.cursor() as cursor:
        cursor.execute(_statement())
        rows = cursor.fetchall()

    assert rows, "запрос не вернул ни строки — постоянные величины обязаны быть"
    for measured_at, window_start, metric, _value in rows:
        assert measured_at is not None, metric
        assert window_start is not None, metric
        assert window_start < measured_at, (metric, window_start, measured_at)


@pytest.mark.django_db
def test_the_origin_split_is_present_but_is_not_the_bridge_marker():
    """Разрез по происхождению полезен сам по себе — но маркером моста НЕ является.

    Так было записано в первой редакции этого файла, и это была ошибка на
    устаревшей посылке: лист DRF-2511 сначала требовал `SOURCE_INFERRED`, потом
    требование сняли (оно предписывало нарушить AYLA-DEC-0024). Мост пишет
    `EXPLICIT`, потому что слова человек произнёс, а опоздание не меняет автора.
    Значит доля `inferred` после моста НЕ вырастет.

    Разрез оставлен: он отвечает на «чего у нас больше — сказанного или
    выведенного», и это самостоятельный вопрос. Но приёмку 2511 он не решает.
    """
    assert "происхождение=" in _sql(), "в запросе нет разреза по `source`"
    assert "lg.source" in _sql()


@pytest.mark.django_db
def test_the_before_mark_of_the_bridge_is_in_the_output_and_is_zero_today():
    """Настоящая отметка «до»: `source_event_id`, и сегодня она ноль.

    Почему именно она. Мосту нужен ключ идемпотентности (требование листа), а
    `source_event_id` — он и есть по §3.1. Сегодняшний писатель его НИКОГДА не
    ставит, и это записано в самом коде: «source_event_id / evidence_refs …
    are never fabricated here» (`apps/identity/services/memory_writer.py`).

    Отсюда свойство, ради которого маркер и выбран: ноль по построению нельзя
    уничтожить правкой. После моста та же строка станет счётчиком его урожая, и
    тем же запросом.

    Узел проверяет ДВЕ вещи, и вторая важнее: что строка есть в выводе, и что
    утверждение про «никогда не ставит» всё ещё верно ПО КОДУ. Если завтра
    кто-нибудь начнёт писать событийный ключ, маркер молча перестанет быть
    маркером — а разницу спишут на мост.
    """
    writer = (
        SQL_PATH.parents[2] / "apps" / "identity" / "services" / "memory_writer.py"
    ).read_text(encoding="utf-8")
    assert "source_event_id / evidence_refs / derivation_method are never" in writer, (
        "писатель больше не обещает не ставить `source_event_id` — маркер "
        "отметки «до» надо выбирать заново"
    )

    with connection.cursor() as cursor:
        cursor.execute(_statement())
        rows = {row[2]: row[3] for row in cursor.fetchall()}

    marker = [m for m in rows if "ОТМЕТКА «ДО»" in m and "source_event_id" in m]
    assert marker, sorted(rows)
    assert rows[marker[0]] == "0", f"{marker[0]} = {rows[marker[0]]}, а ожидался 0"


@pytest.mark.django_db
def test_the_output_names_who_took_the_number():
    """Число без происхождения через неделю станет просто числом.

    Печатается роль в базе, а не человек: роль — не персональные данные, а
    вопрос «кто снял» она закрывает ровно настолько, насколько нужно.
    """
    with connection.cursor() as cursor:
        cursor.execute(_statement())
        rows = {row[2]: row[3] for row in cursor.fetchall()}

    who = [m for m in rows if "КТО СНЯЛ" in m]
    assert who, sorted(rows)
    assert "UTC" in rows[who[0]], rows[who[0]]
