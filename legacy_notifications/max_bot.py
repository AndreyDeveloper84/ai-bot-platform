"""Прямая отправка сообщений в MAX-бот через REST API (для админ-actions).

Зачем НЕ использовать maxapi SDK: это вызывается из Django admin (sync),
а maxapi асинхронный. Прямой POST через requests с тем же Bearer-token —
~10 строк, не требует лишних зависимостей.

Используется в `services_app.admin.BotInquiryAdmin.send_reply_to_client`
для T-09 push-back: менеджер пишет ответ в админке → action шлёт в MAX.
"""
from __future__ import annotations

import logging

import requests
from django.conf import settings


logger = logging.getLogger(__name__)


def send_max_message(
    *,
    chat_id: int,
    text: str,
    attachments=None,
    timeout: int = 10,
) -> bool:
    """Отправить text + опциональные attachments в MAX-чат через REST API.

    attachments — list maxapi Attachment-объектов (например результат
    InlineKeyboardBuilder.as_markup()) ИЛИ list[dict] в wire-format MAX API.
    Пустой/None — обычное текстовое сообщение.

    Returns True при HTTP 2xx. False — если token не задан или API упал
    (исключение НЕ пробрасывается — caller получит False и решит что делать).
    """
    token = getattr(settings, "MAX_BOT_TOKEN", "")
    if not token:
        logger.warning("send_max_message: MAX_BOT_TOKEN не задан")
        return False

    payload: dict = {"text": text}
    if attachments:
        serialized = []
        for a in attachments:
            if hasattr(a, "model_dump"):
                serialized.append(a.model_dump(exclude_none=True))
            elif hasattr(a, "dict"):
                serialized.append(a.dict(exclude_none=True))
            elif isinstance(a, dict):
                serialized.append(a)
            else:
                logger.warning(
                    "send_max_message: skipping unknown attachment type %r", type(a).__name__,
                )
        if serialized:
            payload["attachments"] = serialized

    try:
        r = requests.post(
            "https://botapi.max.ru/messages",
            headers={"Authorization": token, "Content-Type": "application/json"},
            params={"chat_id": chat_id},
            json=payload,
            timeout=timeout,
        )
        if not r.ok:
            logger.warning(
                "send_max_message HTTP %s for chat_id=%s: %s",
                r.status_code, chat_id, r.text[:200],
            )
        return r.ok
    except Exception as exc:  # noqa: BLE001
        logger.warning("send_max_message exception for chat_id=%s: %s", chat_id, exc)
        return False
