"""Адрес запроса в логе — без строки запроса (DRF-1943).

``httpx`` на INFO пишет каждую выполненную операцию одной строкой::

    HTTP Request: GET https://a.oneme.ru/…?sig=…&userId=… "HTTP/1.1 200 OK"

Для вызовов API это полезная строка. Для скачивания файла по ссылке из
вебхука MAX (голосовое, фото еды) это сама ссылка с подписью: запись по ней
отдаётся 24 часа без авторизации, а лог уходит в journald и живёт дольше
выкладки. Решение владельца 06.10.2026 — «голос не храним, только текст» —
такая строка нарушает: записи нет, а рабочая ссылка на неё лежит в журнале.

Фильтр вешается на логгер ``httpx`` (``LOGGING["loggers"]``), а не на
обработчик: тогда адрес обрезан для любого получателя записи — консоли,
Sentry, ``caplog`` в тестах. Обрезается всё после пути: подпись и параметры
у MAX лежат в строке запроса. Метод, хост, путь и статус остаются — по ним
строка по-прежнему читается.

``PIIRedactingFilter`` этого не делает и делать не должен: он знает телефоны,
почту и карты, а подпись в адресе — не ПДн по форме.
"""

from __future__ import annotations

import logging
import re
from typing import Any

#: Что стоит в логе на месте строки запроса.
CUT_MARK = "?…"

_URL_WITH_QUERY = re.compile(r"(https?://[^\s\"'?#]*)[?#][^\s\"']*")


def without_query(text: str) -> str:
    """``https://host/path?sig=…`` → ``https://host/path?…``; прочий текст как есть."""
    return _URL_WITH_QUERY.sub(lambda m: f"{m.group(1)}{CUT_MARK}", text)


def _clean(arg: Any) -> Any:
    # ``httpx.URL`` — не строка; узнаём по ``scheme``, чтобы не импортировать httpx.
    if isinstance(arg, str) or hasattr(arg, "scheme"):
        text = str(arg)
        cleaned = without_query(text)
        return cleaned if cleaned != text else arg
    return arg


class RequestUrlFilter(logging.Filter):
    """Убирает строку запроса из адресов в записи лога. Никогда не бросает."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = without_query(record.msg)
            if isinstance(record.args, tuple):
                record.args = tuple(_clean(arg) for arg in record.args)
            elif isinstance(record.args, dict):
                record.args = {key: _clean(value) for key, value in record.args.items()}
        except Exception:  # noqa: BLE001 — фильтр не должен ронять логирование
            pass
        return True
