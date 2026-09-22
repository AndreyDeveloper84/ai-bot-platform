"""DRF-2272 — каждая строка, уходящая в постоянный журнал, проходит фильтр ПДн.

Логи контейнеров переезжают в journald и живут дольше выкладки (≥7 дней).
Фильтр ``PIIRedactingFilter`` (DRF-859) висел только на root-обработчике,
а мимо root шли два пути:

* uvicorn пишет access- и error-логи своими обработчиками
  (``propagate=False``): строка «метод путь?query статус» уходила на диск
  как есть;
* Celery 5 по умолчанию захватывает root (``worker_hijack_root_logger``) и
  ставит свой обработчик — в воркере фильтр и JSON снимались целиком.

Узлы: телефон, e-mail и карта в сообщении и в access-строке замаскированы;
положительная пара — сама строка на месте.
"""

from __future__ import annotations

import copy
import io
import logging
import logging.config
from collections.abc import Iterator
from typing import Any

import pytest
from django.conf import settings

PHONE = "+7 905 123 4567"
EMAIL = "client@example.com"
CARD = "4111 1111 1111 1111"


@pytest.fixture
def sink() -> Iterator[io.StringIO]:
    """Порядок как в бою: сначала свой конфиг ставит uvicorn, затем при
    загрузке приложения Django применяет ``LOGGING`` — с консолью в буфер."""
    from uvicorn.config import LOGGING_CONFIG

    logging.config.dictConfig(copy.deepcopy(LOGGING_CONFIG))
    stream = io.StringIO()
    config: dict[str, Any] = copy.deepcopy(dict(settings.LOGGING))
    config["handlers"]["console"]["stream"] = stream
    logging.config.dictConfig(config)
    try:
        yield stream
    finally:
        logging.config.dictConfig(settings.LOGGING)


def _assert_masked(text: str) -> None:
    assert "[PHONE]" in text and "[EMAIL]" in text and "[CARD]" in text
    for raw in (PHONE, EMAIL, CARD, "905 123 4567", "4111 1111"):
        assert raw not in text, raw


class TestUvicornGoesThroughTheFilter:
    def test_the_access_line(self, sink) -> None:
        # Та же форма, что у ``uvicorn.protocols.http`` access-логгера.
        logging.getLogger("uvicorn.access").info(
            '%s - "%s %s HTTP/%s" %d',
            "10.0.0.1:52000",
            "GET",
            f"/api/v1/x?phone={PHONE}&email={EMAIL}&card={CARD}",
            "1.1",
            200,
        )
        out = sink.getvalue()
        assert '"GET /api/v1/x?phone=' in out  # положительно: строка записана
        _assert_masked(out)

    def test_the_error_line(self, sink) -> None:
        logging.getLogger("uvicorn.error").error(
            "Exception in ASGI application for %s %s %s", PHONE, EMAIL, CARD
        )
        out = sink.getvalue()
        assert "Exception in ASGI application" in out
        _assert_masked(out)

    def test_an_app_line_is_still_filtered(self, sink) -> None:
        """Контроль: прежний путь через root не сломан."""
        logging.getLogger("apps.somewhere").info("client %s %s %s", PHONE, EMAIL, CARD)
        out = sink.getvalue()
        assert "client" in out
        _assert_masked(out)


class TestCeleryKeepsTheRootHandler:
    def test_the_worker_does_not_hijack_root(self) -> None:
        from config.celery import app

        assert settings.CELERY_WORKER_HIJACK_ROOT_LOGGER is False
        assert app.conf.worker_hijack_root_logger is False
