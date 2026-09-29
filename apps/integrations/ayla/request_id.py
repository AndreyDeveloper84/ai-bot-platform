"""X-Request-ID на каждом исходящем вызове бот → каталог (DRF-1616, блокер B-7.2).

Каталог с апреля принимает ``X-Request-ID``, кладёт его в каждую строку своего
лога и возвращает в ответе (``users.middleware.RequestIDMiddleware``). Бот до
этого PR заголовок не слал — каталог генерировал свой id, и один ход
пользователя жил под двумя несвязанными идентификаторами: ``trace_id`` в боте
и случайный uuid в каталоге. Сопоставить «бот ждал 5 с» и «каталог отвечал
5 с» было нечем.

Значение — ``trace_id`` хода (``apps.tenancy.context.current_trace_id``): тот
же id стоит в логах бота, в ``AIRequestMetric.request_id`` и в событиях
replay. Вне хода (management-команда, тест без scope) — свежий uuid4 на
вызов, чтобы каталог всё равно получил id, а не пустую строку.

Единственная точка сборки: каждый словарь заголовков в ``*_client.py``
проходит через :func:`with_request_id`. Сторож в
``tests/test_outbound_request_id.py`` читает AST клиентов и краснеет на
словаре с ``Authorization`` / ``X-Service-Token``, который идёт мимо.
"""

from __future__ import annotations

import uuid
from typing import Final

from apps.tenancy.context import current_trace_id

REQUEST_ID_HEADER: Final = "X-Request-ID"
# Каталог не ограничивает длину входящего значения и кладёт его в лог как
# есть. Ограничиваем у себя: trace_id бота — uuid/hex, всё длиннее — не наш.
_MAX_LEN: Final = 128


def outbound_request_id() -> str:
    """trace_id текущего хода, иначе свежий uuid4 hex."""

    trace = current_trace_id()
    if trace:
        value = str(trace).strip()
        if value and len(value) <= _MAX_LEN and value.isprintable():
            return value
    return uuid.uuid4().hex


def with_request_id(headers: dict[str, str]) -> dict[str, str]:
    """Вернуть копию ``headers`` с ``X-Request-ID``.

    Явно переданный вызывающим ``X-Request-ID`` не перебивается — это
    для повторов, которые должны ехать под тем же id.
    """

    if REQUEST_ID_HEADER in headers:
        return dict(headers)
    return {**headers, REQUEST_ID_HEADER: outbound_request_id()}
