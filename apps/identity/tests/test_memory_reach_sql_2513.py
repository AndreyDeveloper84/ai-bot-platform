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

SQL_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "measurements" / "DRF-2513-before-sql.sql"
)

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
def test_the_origin_split_is_present_because_the_bridge_writes_inferred():
    """Разрез по происхождению — прямая отметка «до» для моста DRF-2511.

    Мост пишет факты с `source='inferred'`. Значит после моста доля `inferred`
    обязана вырасти, и без этого разреза рост будет неотличим от роста по любой
    другой причине. На пустой базе разреза нет — поэтому проверяется НАЛИЧИЕ
    разреза в тексте запроса и его исполнимость, а не строка вывода.
    """
    assert "происхождение=" in _sql(), "в запросе нет разреза по `source`"
    assert "lg.source" in _sql()
