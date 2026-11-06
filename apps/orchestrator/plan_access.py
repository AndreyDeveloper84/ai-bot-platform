"""Замок приёмки Плана: кому из людей отвечает новый механизм плана.

Решение владельца 10.10: включение механизма плана — не пользовательский
запуск. Флаг ``PLAN_ENGINE_ENABLED`` у каталога общий (он отвечает любому, кто
до него дошёл), поэтому людей от Плана держит бот — здесь.

План открыт человеку, когда выполнены ОБА условия:

* включён механизм — ``PLAN_ENGINE_ENABLED``;
* его аккаунт мессенджера назван в серверном списке
  ``PLAN_ACCEPTANCE_ACCOUNTS`` (``канал:идентификатор``, например
  ``max:12345``).

**Закрыто по умолчанию и при любой неясности.** Пустой список — никому.
Список не того вида, аккаунт без канала или идентификатора, сбой чтения
настройки — никому. Настоящий запуск для людей — отдельное явное действие
(назвать аккаунты), а не побочное следствие включения флага.

Для человека вне списка всё выглядит как выключенный механизм: модели не
предлагается инструмент сборки, нажатия и «мой план» идут прежними путями,
ручки экрана Mini App отвечают ``plan_engine_disabled``, каталог не
спрашивается.

Это ЕДИНСТВЕННОЕ место, где входы Плана читают флаг. Второе чтение флага —
``apps.orchestrator.dr_shadow.record_turn_safety``: там пишется вердикт ворот
безопасности каждого хода, человеку это ничего не открывает (перепись —
``apps/orchestrator/tests/test_plan_access_2885.py``).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

SETTING = "PLAN_ACCEPTANCE_ACCOUNTS"


def _flag() -> bool:
    from django.conf import settings

    return getattr(settings, "PLAN_ENGINE_ENABLED", False) is True


def acceptance_accounts() -> frozenset[str]:
    """Аккаунты приёмки из настройки. Не список строк — пусто, то есть никому.

    Строка целиком списком не считается: ``"max:1"`` вместо ``("max:1",)`` —
    ошибка настройки, и молча читать её по буквам или как один аккаунт нельзя.
    """
    from django.conf import settings

    raw = getattr(settings, SETTING, ())
    if not isinstance(raw, list | tuple | set | frozenset):
        if raw:
            logger.error("orchestrator.plan_access.setting_malformed type=%s", type(raw).__name__)
        return frozenset()
    if not all(isinstance(item, str) for item in raw):
        logger.error("orchestrator.plan_access.setting_malformed type=non_string_item")
        return frozenset()
    return frozenset(item.strip() for item in raw if item.strip())


def _listed(account: str) -> bool:
    return account in acceptance_accounts()


def _account_of(bot_user: Any) -> str | None:
    channel = getattr(bot_user, "channel", None)
    person = getattr(bot_user, "channel_user_id", None)
    if not isinstance(channel, str) or not isinstance(person, str):
        return None
    channel, person = channel.strip(), person.strip()
    if not channel or not person:
        return None
    return f"{channel}:{person}"


def plan_open_for(bot_user: Any) -> bool:
    """Открыт ли План этому человеку. Любая неясность — закрыт."""
    try:
        if not _flag():
            return False
        account = _account_of(bot_user)
        return account is not None and _listed(account)
    except Exception:  # noqa: BLE001 — не прочитали → закрыто
        logger.warning("orchestrator.plan_access.read_failed", exc_info=True)
        return False


def plan_open_in(conversation: Any) -> bool:
    """То же по разговору — для мест, где человека под рукой нет.

    Человек берётся из самого разговора; разговора или человека в нём нет —
    закрыто.
    """
    try:
        bot_user = getattr(conversation, "bot_user", None)
    except Exception:  # noqa: BLE001 — не прочитали → закрыто
        logger.warning("orchestrator.plan_access.read_failed", exc_info=True)
        return False
    return bot_user is not None and plan_open_for(bot_user)


__all__ = ["SETTING", "acceptance_accounts", "plan_open_for", "plan_open_in"]
