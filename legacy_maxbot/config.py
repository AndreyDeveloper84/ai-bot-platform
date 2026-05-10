"""Конфигурация MAX-бота из переменных окружения.

Читается **после** django.setup() — чтобы гарантировать что .env уже загружен
через mysite/settings/__init__.py (там вызов python-dotenv).

Требует:
- MAX_BOT_TOKEN (обязательно)

Опционально:
- MAX_BOT_MODE: polling | webhook (default: polling)
- MAX_WEBHOOK_HOST: default 127.0.0.1
- MAX_WEBHOOK_PORT: default 8003
- MAX_WEBHOOK_SECRET: secret-path суффикс для защиты webhook (default — без)
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from django.core.exceptions import ImproperlyConfigured


VALID_MODES = ("polling", "webhook")


@dataclass(frozen=True)
class MaxBotConfig:
    token: str
    mode: str
    webhook_host: str
    webhook_port: int
    webhook_path: str
    webhook_secret: str  # пустая строка = без secret-header validation


def get_config() -> MaxBotConfig:
    token = os.environ.get("MAX_BOT_TOKEN", "").strip()
    if not token:
        raise ImproperlyConfigured(
            "MAX_BOT_TOKEN не задан. Установите переменную окружения с токеном "
            "MAX-бота (см. docs/plans/maxbot-phase1.md §1)."
        )

    mode = os.environ.get("MAX_BOT_MODE", "polling").strip()
    if mode not in VALID_MODES:
        raise ImproperlyConfigured(
            f"MAX_BOT_MODE='{mode}' — недопустимое значение. "
            f"Должно быть одно из: {VALID_MODES}."
        )

    try:
        port = int(os.environ.get("MAX_WEBHOOK_PORT", "8003"))
    except ValueError as exc:
        raise ImproperlyConfigured(f"MAX_WEBHOOK_PORT — нечисловое: {exc}")
    if not (1 <= port <= 65535):
        raise ImproperlyConfigured(f"MAX_WEBHOOK_PORT={port} — вне диапазона 1..65535")

    return MaxBotConfig(
        token=token,
        mode=mode,
        webhook_host=os.environ.get("MAX_WEBHOOK_HOST", "127.0.0.1"),
        webhook_port=port,
        webhook_path=os.environ.get("MAX_WEBHOOK_PATH", "/api/maxbot/webhook/"),
        webhook_secret=os.environ.get("MAX_WEBHOOK_SECRET", "").strip(),
    )
