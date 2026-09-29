"""DRF-2634 — SDK openai и anthropic не пишут тело запроса даже при DEBUG.

На DEBUG оба SDK выводят «Request options: …» — весь запрос к модели, а в
нём текст человека (набранный или расшифровка голосового), история и промпт.
``LOGGING`` прибивает логгеры ``*._base_client`` на INFO, и это должно
держаться при самом плохом раскладе: root на DEBUG и отладка SDK, включённая
его же переменной окружения (``OPENAI_LOG`` / ``ANTHROPIC_LOG``).

Каждый узел — пара в одном теле: контроль (пин снят → слово в логе есть,
значит DEBUG действительно ловится и SDK всё ещё пишет тело) и проверка
(пин на месте → слова нет, а строка httpx о самом запросе — есть).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import Any, cast

import anthropic
import httpx
import openai
import pytest
from django.conf import settings

WORD = "ксилофон-2634"

PINNED = (
    "openai._base_client",
    "anthropic._base_client",
    "openai.resources.realtime.realtime",
    "openai.resources.beta.realtime.realtime",
)


def _openai_reply(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-2634",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-test",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "ок"},
                }
            ],
        },
    )


def _anthropic_reply(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "msg_2634",
            "type": "message",
            "role": "assistant",
            "model": "claude-test",
            "content": [{"type": "text", "text": "ок"}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    )


def _call_openai() -> None:
    client = openai.OpenAI(
        api_key="test",  # pragma: allowlist secret — заглушка клиента, не ключ
        max_retries=0,
        http_client=openai.DefaultHttpxClient(transport=httpx.MockTransport(_openai_reply)),
    )
    client.chat.completions.create(model="gpt-test", messages=[{"role": "user", "content": WORD}])


def _call_anthropic() -> None:
    client = anthropic.Anthropic(
        api_key="test",  # pragma: allowlist secret — заглушка клиента, не ключ
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx.MockTransport(_anthropic_reply)),
    )
    client.messages.create(
        model="claude-test", max_tokens=8, messages=[{"role": "user", "content": WORD}]
    )


@pytest.fixture
def sdk_debug(monkeypatch) -> Iterator[None]:
    """Отладка обоих SDK включена их собственным путём, как на стенде.

    ``setup_logging`` читает переменную и ставит DEBUG на ``openai`` /
    ``anthropic`` и ``httpx``; уровни возвращаются после узла.
    """
    names = ("openai", "anthropic", "httpx", *PINNED)
    saved = {name: logging.getLogger(name).level for name in names}
    monkeypatch.setenv("OPENAI_LOG", "debug")
    monkeypatch.setenv("ANTHROPIC_LOG", "debug")
    openai._utils._logs.setup_logging()
    anthropic._utils._logs.setup_logging()
    try:
        yield
    finally:
        for name, level in saved.items():
            logging.getLogger(name).setLevel(level)


def _logged(caplog, call: Callable[[], None]) -> tuple[str, list[str]]:
    caplog.clear()
    call()
    text = "\n".join(r.getMessage() for r in caplog.records)
    httpx_lines = [r.getMessage() for r in caplog.records if r.name == "httpx"]
    return text, httpx_lines


def test_logging_pins_the_body_writers_at_info() -> None:
    loggers = cast(dict[str, dict[str, Any]], settings.LOGGING["loggers"])
    assert {name: loggers[name]["level"] for name in PINNED} == dict.fromkeys(PINNED, "INFO")
    # Пин применён к живым логгерам, а не только лежит в словаре.
    assert [logging.getLogger(name).level for name in PINNED] == [logging.INFO] * len(PINNED)


@pytest.mark.parametrize(
    ("pinned", "call"),
    [("openai._base_client", _call_openai), ("anthropic._base_client", _call_anthropic)],
    ids=["openai", "anthropic"],
)
def test_request_body_stays_out_of_the_log_at_debug(caplog, sdk_debug, pinned, call) -> None:
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger(pinned)
    # Уровень, который поставила САМА конфигурация (``LOGGING`` при загрузке
    # Django), — его и проверяем. Проверка с ``setLevel(INFO)`` руками прошла
    # бы и без пина в настройках: доказывала бы «INFO не пишет тело», а не
    # «настройки его прибили» (DRF-2634, разбор #2190).
    configured = logger.level

    # Контроль: без пина тело запроса с текстом человека уходит в лог.
    logger.setLevel(logging.NOTSET)
    text, _ = _logged(caplog, call)
    assert WORD in text
    assert "Request options" in text

    # С уровнем из настроек — строка httpx о запросе есть, тела нет.
    logger.setLevel(configured)
    text, httpx_lines = _logged(caplog, call)
    assert any(line.startswith("HTTP Request: POST") for line in httpx_lines)
    assert "Request options" not in text
    assert WORD not in text
