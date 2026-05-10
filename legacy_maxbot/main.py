"""MAX Bot entrypoint — отдельный async-процесс рядом с Django gunicorn'ом.

Запуск:
    python -m maxbot.main

Режимы (env MAX_BOT_MODE):
- polling — long-poll, для локалки/dev
- webhook — production за nginx

См. docs/plans/maxbot-phase1.md §4.1.
"""
from __future__ import annotations

import asyncio
import logging
import os

# Django нужен ДО импорта handlers/ — те читают модели services_app.
from maxbot.django_bootstrap import setup_django
setup_django()

from maxapi import Bot, Dispatcher  # noqa: E402

from maxbot.config import get_config  # noqa: E402

logger = logging.getLogger("maxbot")


def build_dispatcher() -> Dispatcher:
    """Собирает Dispatcher с router'ами + middleware (logging + error alerts)."""
    from maxbot.handlers import get_routers
    from maxbot.middleware import ErrorAlertMiddleware, LoggingMiddleware

    dp = Dispatcher()
    dp.middlewares = [LoggingMiddleware(), ErrorAlertMiddleware()]
    dp.include_routers(*get_routers())
    return dp


async def run() -> None:
    cfg = get_config()
    bot = Bot(token=cfg.token)
    dp = build_dispatcher()

    try:
        me = await bot.get_me()
    except Exception as exc:
        logger.error(
            "Не удалось подключиться к MAX API: %s. "
            "Проверьте MAX_BOT_TOKEN в .env (он должен совпадать с токеном бота "
            "из MAX для партнёров).",
            exc,
        )
        raise
    logger.info("MAX bot online: user_id=%s username=%s", me.user_id, me.username)

    # Eager MCP start — preload subprocess + chromadb singleton, чтобы первый
    # AI-запрос не платил cold-start ~1.4s spawn + ~2.4s init chromadb.
    # При ошибке — лог и продолжаем (AI-handler сам ensure_started lazy).
    #
    # MAXBOT_SKIP_WARMUP=1 — пропустить blocking warmup. Полезно в dev/CI
    # когда OPENAI_PROXY/api.openai.com недоступны (тогда search_faq висит
    # на embeddings call). Бот всё равно поднимается, AI lazy-инициализуется
    # при первом FAQ-запросе.
    skip_warmup = os.getenv("MAXBOT_SKIP_WARMUP", "").lower() in ("1", "true", "yes")
    mcp_ready = False
    if skip_warmup:
        logger.info("MCP warmup skipped via MAXBOT_SKIP_WARMUP — lazy init")
    else:
        try:
            from maxbot.mcp_client import MaxbotMCPClient
            await MaxbotMCPClient.instance().ensure_started()
            # Прогрев chromadb singleton — первый search_faq при старте, без ответа клиенту
            await MaxbotMCPClient.instance().call_tool("search_faq", {"query": "warmup", "k": 1})
            logger.info("MCP eager start OK — chromadb singleton прогрет")
            mcp_ready = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("MCP eager start failed (lazy fallback): %s", exc)

    # Прогрев response cache — фоновая задача, не блокирует webhook listener.
    # Первый клиент после рестарта по «как записаться?» получает мгновенный
    # ответ если warmup успел (~30-60s на 10 вопросов через прокси).
    if mcp_ready:
        from maxbot.warmup import warmup_response_cache
        asyncio.create_task(
            warmup_response_cache(mcp_client=MaxbotMCPClient.instance())
        )

    if cfg.mode == "polling":
        logger.info("Mode: long-polling")
        await bot.delete_webhook()
        await dp.start_polling(bot)
    else:
        logger.info(
            "Mode: webhook %s:%s path=%s secret=%s",
            cfg.webhook_host, cfg.webhook_port, cfg.webhook_path, bool(cfg.webhook_secret),
        )
        # secret=None если пустая строка → SDK не валидирует X-Max-Bot-Api-Secret
        await dp.handle_webhook(
            bot=bot,
            host=cfg.webhook_host,
            port=cfg.webhook_port,
            path=cfg.webhook_path,
            secret=cfg.webhook_secret or None,
        )


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


if __name__ == "__main__":
    _configure_logging()
    asyncio.run(run())
