"""Общие примитивы доступа к красной зоне: GUC и имя обращающегося.

Выделено из :mod:`apps.identity.services.red_zone_reader` (DRF-2180), когда
у красной зоны появился ВТОРОЙ законный путь — массовое снятие по «забудь
всё» (:func:`apps.identity.services.memory_deleter.
soft_delete_all_zones_for_forget_all`). Копировать `SET LOCAL` во второй
модуль было нельзя: правило «доступ к красной строке только под GUC» живёт
ровно в той мере, в какой у него одна реализация.

# Почему GUC нужен даже там, где сегодня работает без него

Политика ``memory_entry_non_red_visible`` (миграция 0008,
``AS RESTRICTIVE FOR SELECT``) прячет красные строки от любого SELECT без
``ayla.red_zone_access_context``, и **WHERE у UPDATE подчиняется той же
политике SELECT**. Сегодня приложение подключается под ``platform`` —
суперпользователем контейнера, который RLS обходит, — поэтому запрос без
GUC красное видит. В день перехода на ``ayla_app`` (ADR-0011 §16, фаза 2,
шаг 5) тот же код молча перестал бы видеть красные строки: без исключения,
с зелёным результатом и пустым журналом. Регрессии на это пишутся под
``SET LOCAL ROLE ayla_app`` — иначе они ничего не стерегут, потому что вся
сюита идёт под суперпользователем.

# Имя обращающегося

Три модуля собирали ``accessor_principal`` каждый по-своему
(``system:{host}:{pid}``, ``memory_writer:{host}:{pid}``,
``forget_all_sweep:{host}:{pid}``). Каждая форма защитима поодиночке, но
аудитору, который ищет по префиксу, они дают три разных ответа на один
вопрос. :func:`red_zone_principal` — один формат: ``{кто}:{host}:{pid}``.
"""

from __future__ import annotations

import logging
import os
import socket
import uuid

from django.db import connection

logger = logging.getLogger(__name__)

GUC_NAME = "ayla.red_zone_access_context"


def red_zone_principal(role: str, actor: str) -> str:
    """Конкретная личность обращающегося (round-2 AS2): ``{actor}:{host}:{pid}``.

    ``role`` не участвует в строке, но берётся аргументом намеренно: вызов
    читается как «кто по роли и кто конкретно», и роль рядом не даёт собрать
    имя, не подумав о ней.
    """
    return f"{actor}:{socket.gethostname()}:{os.getpid()}"


def _set_red_zone_guc(request_id: uuid.UUID) -> None:
    """Шаг 1 любого обращения к красной зоне: открыть RLS на эту транзакцию."""
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT set_config('{GUC_NAME}', %s, true)",
                [str(request_id)],
            )


def _reset_red_zone_guc() -> None:
    """Round-5 F1: снять GUC на любом пути выхода (только Postgres).

    RESET выполняется даже когда транзакция откатилась: ``set_config`` с
    ``is_local=true`` снимается в КОНЦЕ транзакции, а внешний ``atomic()``
    вызывающего держит её живой после освобождения нашего SAVEPOINT.
    Round-5 F1-C (#703): если соединение умерло, сам ``connection.cursor()``
    бросит — выпустить это из ``finally`` значило бы ЗАМАСКИРОВАТЬ исходное
    исключение, поэтому неудачный RESET пишется в лог и никогда не бросается.
    """
    if connection.vendor == "postgresql":
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"RESET {GUC_NAME}")
        except Exception:
            logger.exception("RESET of red_zone_access_context failed — connection likely unusable")
