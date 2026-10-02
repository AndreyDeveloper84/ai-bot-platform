"""Адрес поддержки клиента — клиентский бот, и ничто другое (DRF-2751).

### Решение

Владелец (02.10.2026): поддержка клиента идёт **через сам клиентский
MAX-бот**. Внутренний чат сотрудников — тот, куда приходят оповещения о
LLM, выкладке и передаче человеку, — клиенту не показывается.

### Адрес выводится, а не вписывается

Клиентский бот уже назван в реестре: запись потока ``max_global``, её
публичная ссылка — ``MAX_BOT_<S>_LINK`` (:attr:`BotEntry.link`). Эта ссылка
уже доходит до людей двумя путями: салонный бот отправляет ею в клиентский
(``salon_handler._client_bot_link_button``), мини-приложение получает её
как ``chat_link``. Адрес поддержки — та же ссылка со стартовым параметром
:data:`SUPPORT_START_PAYLOAD`.

Своей настройки «впишите адрес поддержки клиента» нет вовсе, и это
намеренно: единственный адрес, который может получить клиент, строится из
ссылки клиентского бота. Вписать сюда внутренний чат некуда.

### Сторож стоит на том, что доходит до клиента

:func:`client_bot_link_problem` проверяет саму ссылку клиентской записи:

* это ссылка на бота — ``https``, один сегмент пути (``/join/<код>`` и
  ``/c/<id>`` — приглашение в чат, не бот);
* не салонный бот;
* не id получателя внутренних оповещений
  (``HANDOFF_NOTIFY_MAX_CHAT_IDS`` / ``_USER_IDS``);
* её имя совпадает с ``web_app`` той же записи, если тот задан. Это
  сильнее формы: бот назван в реестре дважды, и два названия обязаны
  сходиться (на пилоте ``web_app`` — буква в букву имя из ссылки, см.
  ``start_links.MAX_START_LINK_TEMPLATE``).

Не прошла — :func:`client_support_link` отдаёт пусто: адреса нет, и это
лучше, чем не тот адрес. На контуре не для отладки об этом говорит
``support.E002`` (:mod:`apps.channels.checks`).

### Пределы

* Публичная ссылка на чат или канал вида ``https://max.ru/<имя>`` по форме
  неотличима от ссылки на бота. Её ловит только сверка с ``web_app``; когда
  ``web_app`` не задан, проверяются форма и «не салонный бот», не больше.
* Что ссылка ОТКРЫВАЕТСЯ и приводит в бот — живой тап, не код.
* ``AYLA_SUPPORT_CONTACT`` здесь не участвует: его читает только кнопка
  салонного бота для персонала, клиент его не видит. Адресат для персонала
  — отдельный вопрос владельцу.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

#: Поток клиентского бота в реестре — так его уже находит салонный бот.
CLIENT_STREAM = "max_global"

#: Стартовый параметр: ``https://max.ru/<бот>?start=support``. MAX доставляет
#: его как ``bot_started.payload``, парсер сворачивает в «/start support».
SUPPORT_START_PAYLOAD = "support"

# Причины отказа — закрытый словарь; по ним пишутся узлы и текст проверки.
PROBLEM_NOT_A_LINK = "not_a_link"
PROBLEM_STAFF_RECIPIENT = "staff_recipient_id"
PROBLEM_NOT_A_BOT_LINK = "not_a_bot_link"
PROBLEM_SALON_BOT = "salon_bot"
PROBLEM_HANDLE_MISMATCH = "handle_differs_from_web_app"


def _bot_address(link: str) -> tuple[str, str] | None:
    """``(хост, имя бота)`` ссылки вида ``https://<хост>/<имя>`` — или None.

    Ровно один сегмент пути, без параметров и якоря: ссылка в реестре —
    адрес бота, а не переход с намерением.
    """

    parts = urlsplit((link or "").strip())
    if parts.scheme != "https" or not parts.hostname or parts.query or parts.fragment:
        return None
    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) != 1:
        return None
    return parts.hostname.lower(), segments[0]


def _entry(stream: str) -> Any:
    from apps.channels.bot_registry import effective_registry, resolve_by_stream

    return resolve_by_stream(stream, effective_registry())


def _salon_handles() -> set[str]:
    """Имена салонного бота: из его ссылки и из ``web_app`` (на пилоте задан он)."""

    from apps.channels.bot_registry import SALON_STREAM

    entry = _entry(SALON_STREAM)
    if entry is None:
        return set()
    handles = {(getattr(entry, "web_app", "") or "").strip()}
    address = _bot_address(getattr(entry, "link", "") or "")
    if address is not None:
        handles.add(address[1])
    handles.discard("")
    return handles


def _staff_recipient_ids() -> set[str]:
    from django.conf import settings

    ids: set[str] = set()
    for name in ("HANDOFF_NOTIFY_MAX_CHAT_IDS", "HANDOFF_NOTIFY_MAX_USER_IDS"):
        ids.update(str(value).strip() for value in (getattr(settings, name, None) or ()))
    ids.discard("")
    return ids


def client_bot_link() -> str:
    """Ссылка клиентской записи реестра как она задана, или ``""``."""

    entry = _entry(CLIENT_STREAM)
    return (getattr(entry, "link", "") or "").strip() if entry is not None else ""


def client_bot_link_problem() -> str | None:
    """Почему ссылку клиентского бота нельзя отдавать клиенту — или None.

    None и для пустой ссылки: «не задана» — не «задана неверно», адреса
    тогда просто нет (:func:`client_support_link` отдаёт пусто).
    """

    link = client_bot_link()
    if not link:
        return None
    if link in _staff_recipient_ids():
        return PROBLEM_STAFF_RECIPIENT
    if "://" not in link:
        return PROBLEM_NOT_A_LINK
    address = _bot_address(link)
    if address is None:
        return PROBLEM_NOT_A_BOT_LINK
    if address[1] in _salon_handles():
        return PROBLEM_SALON_BOT
    web_app = (getattr(_entry(CLIENT_STREAM), "web_app", "") or "").strip()
    if web_app and web_app != address[1]:
        return PROBLEM_HANDLE_MISMATCH
    return None


def client_support_link() -> str:
    """``<ссылка клиентского бота>?start=support``, или ``""``.

    Пусто — когда ссылки у клиентской записи нет или она не прошла сторож.
    """

    link = client_bot_link()
    if not link or client_bot_link_problem() is not None:
        return ""
    address = _bot_address(link)
    assert address is not None  # сторож выше это уже проверил
    host, handle = address
    return f"https://{host}/{handle}?start={SUPPORT_START_PAYLOAD}"
