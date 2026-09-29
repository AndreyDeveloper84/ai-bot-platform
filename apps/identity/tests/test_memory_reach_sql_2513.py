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

import inspect
import re
import uuid
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


def _metrics() -> dict[str, str]:
    with connection.cursor() as cursor:
        cursor.execute(_statement())
        return {row[2]: row[3] for row in cursor.fetchall()}


def _one(rows: dict[str, str], *needles: str) -> str:
    found = [m for m in rows if all(n in m for n in needles)]
    assert len(found) == 1, (needles, sorted(rows))
    return found[0]


#: Боевой код, в котором ищется писатель событийного ключа: всё под `apps/`,
#: кроме узлов и миграций. Модель поле объявляет, писатель о нём молчит вслух.
_KNOWN_MENTIONS = {
    "apps/identity/models.py",
    "apps/identity/services/memory_writer.py",
}


def test_nobody_writes_the_event_key():
    """Ноль сторожа держится на том, что событийный ключ не пишет НИКТО.

    Первая редакция опиралась на фразу в комментарии писателя — это охрана
    прозы: фраза переживает и дефект, и починку. Здесь перепись носителей по
    всему боевому коду: любое новое упоминание `source_event_id` вне двух
    известных мест — сигнал, что ноль в выводе перестал значить «никто».

    Первая редакция называла ноль «отметкой до» для моста DRF-2511 и ждала, что
    мост начнёт ставить ключ. Мост (#2080) пишет через `write_entry`, у которого
    такого параметра нет, — это и проверяется вторым утверждением.
    """
    from apps.identity.services.memory_writer import write_entry

    assert "source_event_id" not in inspect.signature(write_entry).parameters

    apps_dir = SQL_PATH.parents[2] / "apps"
    scanned = 0
    mentions = set()
    for path in apps_dir.rglob("*.py"):
        rel = path.relative_to(SQL_PATH.parents[2]).as_posix()
        if "/tests/" in rel or "/migrations/" in rel or path.name.startswith("test_"):
            continue
        scanned += 1
        if "source_event_id" in path.read_text(encoding="utf-8"):
            mentions.add(rel)

    assert scanned > 300, f"просмотрено {scanned} файлов — перепись не туда смотрит"
    assert mentions == _KNOWN_MENTIONS, (
        f"просмотрено {scanned} файлов; новые носители `source_event_id`: "
        f"{sorted(mentions - _KNOWN_MENTIONS)}, пропавшие: {sorted(_KNOWN_MENTIONS - mentions)}"
    )


@pytest.mark.django_db(transaction=True)
def test_the_guard_zero_is_read_over_rows_written_by_the_product(settings):
    """Ноль сторожа снят на НЕПУСТОМ охвате, и сторож умеет не быть нулём.

    На пустой базе «0» дал бы и запрос, считающий не то (проверено подменой:
    `IS NOT NULL` → `IS NULL` на пустой базе проходил зелёным). Поэтому строки
    пишет продуктовый путь входа — `record_explicit_green_facts`, тот же, через
    который пишет и мост, — а не прямой `create`.
    """
    from apps.consent.services import record_global_consent
    from apps.identity.models import MemoryEntry
    from apps.identity.services import resolve_or_create_global_bot_user
    from apps.orchestrator.memory.personal_context import record_explicit_green_facts

    settings.STRICT_TENANT_SCOPE = "strict"

    written = 0
    for uid in ("mem-2513-a", "mem-2513-b"):
        bu = resolve_or_create_global_bot_user(
            channel="max", channel_user_id=uid, ayla_user_id=uuid.uuid4()
        )
        record_global_consent(bu, source="welcome")
        # bridge=False: зеркало в Ayla — REST наружу, к локальной строке отношения не имеет.
        written += record_explicit_green_facts(bu, "кстати, я веган", bridge=False)

    live = MemoryEntry.objects.filter(sensitivity_zone="green", soft_deleted_at__isnull=True)
    assert written == 2 and live.count() == 2, (written, live.count())

    rows = _metrics()
    coverage = _one(rows, "ОХВАТ СТОРОЖА")
    guard = _one(rows, "СТОРОЖ:", "source_event_id")
    assert rows[coverage] == "2", f"{coverage} = {rows[coverage]}"
    assert rows[guard] == "0", f"{guard} = {rows[guard]} при охвате {rows[coverage]}"

    # Подмена: одна строка получает событийный ключ — сторож обязан это увидеть,
    # а охват не сдвинуться.
    live.filter(pk=live.first().pk).update(source_event_id=uuid.uuid4())
    rows = _metrics()
    assert rows[guard] == "1", f"{guard} = {rows[guard]} после подмены"
    assert rows[coverage] == "2", f"{coverage} = {rows[coverage]} после подмены"


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
