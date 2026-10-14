"""DRF-1943 — в логе нет подписи ссылки на файл из вебхука MAX.

``httpx`` на INFO пишет адрес каждого запроса. Для скачивания голосового это
сама ссылка: запись по ней отдаётся 24 часа без авторизации, а лог уходит в
journald. Решение владельца 06.10.2026 — «голос не храним, только текст».

Узел с настоящим ``LOGGING`` и настоящим ``httpx`` — парой в одном теле:
контроль (фильтр снят → подпись в логе есть, значит строка действительно
пишется и ловится) и проверка (фильтр на месте → подписи нет, а сама строка
о запросе осталась).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any, cast

import httpx
import pytest
from django.conf import settings

from apps.observability.url_log_filter import CUT_MARK, RequestUrlFilter, without_query

SIG = "SIG-MARKER-1943"
VOICE_URL = f"https://a.oneme.ru/v.ogg?sig={SIG}&userId=42"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (VOICE_URL, f"https://a.oneme.ru/v.ogg{CUT_MARK}"),
        ("https://i.oneme.ru/i?r=abc#frag", f"https://i.oneme.ru/i{CUT_MARK}"),
        ("https://api.openai.com/v1/audio/transcriptions", None),
        (
            f'GET {VOICE_URL} "HTTP/1.1 200 OK"',
            f'GET https://a.oneme.ru/v.ogg{CUT_MARK} "HTTP/1.1 200 OK"',
        ),
        ("вопрос? да", None),
        ("", None),
    ],
)
def test_without_query(text: str, expected: str | None) -> None:
    assert without_query(text) == (text if expected is None else expected)


def test_the_filter_is_wired_to_the_httpx_logger() -> None:
    loggers = cast("dict[str, Any]", settings.LOGGING["loggers"])
    assert loggers["httpx"]["filters"] == ["request_url"]
    assert [f for f in logging.getLogger("httpx").filters if isinstance(f, RequestUrlFilter)]


@pytest.fixture
def httpx_filters() -> Iterator[list[Any]]:
    """Фильтры логгера ``httpx``; после узла возвращаются как были."""
    logger = logging.getLogger("httpx")
    saved = list(logger.filters)
    try:
        yield logger.filters
    finally:
        logger.filters[:] = saved


def _download() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"ogg"))
    with httpx.Client(transport=transport) as http:
        http.get(VOICE_URL)


def _httpx_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "httpx"]


def test_a_download_leaves_no_signature_in_the_log(caplog, httpx_filters) -> None:
    caplog.set_level(logging.INFO)

    # Контроль: без фильтра httpx пишет ссылку целиком.
    installed = [f for f in httpx_filters if isinstance(f, RequestUrlFilter)]
    httpx_filters[:] = [f for f in httpx_filters if not isinstance(f, RequestUrlFilter)]
    _download()
    assert any(SIG in line for line in _httpx_lines(caplog))

    # С фильтром — строка о запросе есть, подписи и параметров нет.
    httpx_filters.extend(installed)
    caplog.clear()
    _download()
    lines = _httpx_lines(caplog)
    assert lines == [f'HTTP Request: GET https://a.oneme.ru/v.ogg{CUT_MARK} "HTTP/1.1 200 OK"']
    assert [line for line in lines if SIG in line or "userId" in line] == []


def test_an_api_call_line_stays_as_it_was(caplog) -> None:
    caplog.set_level(logging.INFO)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    with httpx.Client(transport=transport) as http:
        http.post("https://api.openai.com/v1/audio/transcriptions")
    assert _httpx_lines(caplog) == [
        'HTTP Request: POST https://api.openai.com/v1/audio/transcriptions "HTTP/1.1 200 OK"'
    ]


def test_the_filter_never_breaks_logging() -> None:
    record = logging.LogRecord("httpx", logging.INFO, __file__, 1, "%s %s", (object(), 5), None)
    assert RequestUrlFilter().filter(record) is True
    assert record.getMessage().endswith(" 5")
