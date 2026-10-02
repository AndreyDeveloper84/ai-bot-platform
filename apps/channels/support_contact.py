"""Адрес поддержки клиента — клиентский бот, и ничто другое (DRF-2751).

### Решение

Владелец (02.10.2026): поддержка клиента идёт **через сам клиентский
MAX-бот**. Внутренний чат сотрудников — тот, куда приходят оповещения о
LLM, выкладке и передаче человеку, — клиенту не показывается.

### Адрес выводится, а не вписывается

Клиентский бот уже назван в реестре: запись потока ``max_global``, её
публичная ссылка — ``MAX_BOT_<S>_LINK`` (:attr:`BotEntry.link`). Той же
ссылкой салонный бот отправляет человека в клиентский
(``salon_handler._client_bot_link_button``), и её же получает
мини-приложение как ``chat_link``. Адрес поддержки — эта ссылка со
стартовым параметром :data:`SUPPORT_START_PAYLOAD`. Второго правила «как
зовут клиентского бота» здесь нет.

### Сторож — белый список

:func:`support_contact_problem` принимает РОВНО один адрес: ссылку на
клиентского бота из реестра (с ``?start=support`` или без параметров).
Всё остальное — отказ с названной причиной: свободный текст, числовой
id, ссылка-приглашение в чат, чужой хост (включая прежнюю заглушку
``max.me/aylasupport``), салонный бот, любой другой бот.

Почему не чёрный список: адреса внутреннего чата в репозитории нет — в
окружении лежат только числовые id получателей
(``HANDOFF_NOTIFY_MAX_CHAT_IDS`` / ``_USER_IDS``). Запретить то, чего не
знаешь, нельзя; разрешить единственное известное — можно. Внутренний чат
не проходит по построению, каким бы ни был его адрес.

### Чего здесь нет

* Проверки, что ссылка ОТКРЫВАЕТСЯ: это живой тап, не код.
* Адресата для персонала салона: кнопка «Обратиться в поддержку»
  салонного бота читает ``AYLA_SUPPORT_CONTACT`` и после этого листа
  показывает значение, только если оно прошло этот сторож. Куда писать
  сотруднику салона — отдельный вопрос владельцу.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit

#: Поток клиентского бота в реестре — так его уже находит салонный бот.
CLIENT_STREAM = "max_global"

#: Стартовый параметр: ``https://max.ru/<бот>?start=support``. MAX доставляет
#: его как ``bot_started.payload``, парсер сворачивает в «/start support».
SUPPORT_START_PAYLOAD = "support"

# Причины отказа — закрытый словарь; по ним пишутся узлы и текст проверки.
PROBLEM_NOT_A_LINK = "not_a_link"
PROBLEM_STAFF_RECIPIENT = "staff_recipient_id"
PROBLEM_FOREIGN_HOST = "foreign_host"
PROBLEM_NOT_A_BOT_LINK = "not_a_bot_link"
PROBLEM_SALON_BOT = "salon_bot"
PROBLEM_OTHER_BOT = "not_the_client_bot"
PROBLEM_UNEXPECTED_QUERY = "unexpected_query"
PROBLEM_NO_CLIENT_LINK = "no_client_bot_link"


def _bot_address(link: str) -> tuple[str, str] | None:
    """``(хост, handle)`` ссылки вида ``https://<хост>/<handle>`` — или None.

    Ровно один сегмент пути: ``/join/<код>`` и ``/c/<id>`` — приглашение в
    чат, а не бот. Схема только ``https``.
    """

    parts = urlsplit((link or "").strip())
    if parts.scheme != "https" or not parts.hostname:
        return None
    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) != 1:
        return None
    return parts.hostname.lower(), segments[0]


def _entry(stream: str):
    from apps.channels.bot_registry import effective_registry, resolve_by_stream

    return resolve_by_stream(stream, effective_registry())


def _entry_link(stream: str) -> str:
    entry = _entry(stream)
    return (getattr(entry, "link", "") or "").strip() if entry is not None else ""


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


def client_bot_link() -> str:
    """Публичная ссылка на клиентского бота из реестра, или ``""``."""

    return _entry_link(CLIENT_STREAM)


def client_support_link() -> str:
    """``<ссылка клиентского бота>?start=support``, или ``""``.

    Пусто — когда у клиентской записи реестра нет ссылки (или она не ссылка
    на бота): адреса поддержки тогда нет, и называть вместо него что-то
    другое нельзя.
    """

    link = client_bot_link()
    address = _bot_address(link)
    if address is None:
        return ""
    host, handle = address
    return f"https://{host}/{handle}?start={SUPPORT_START_PAYLOAD}"


def _staff_recipient_ids() -> set[str]:
    from django.conf import settings

    ids: set[str] = set()
    for name in ("HANDOFF_NOTIFY_MAX_CHAT_IDS", "HANDOFF_NOTIFY_MAX_USER_IDS"):
        ids.update(str(value).strip() for value in (getattr(settings, name, None) or ()))
    ids.discard("")
    return ids


def support_contact_problem(value: str) -> str | None:
    """Почему ``value`` нельзя показать клиенту как адрес поддержки — или None.

    None — значение и есть ссылка на клиентского бота. Пустое значение сюда
    не подаётся: «адрес не задан» — другой факт (``support.W001``), а не
    «задан неверный».
    """

    candidate = (value or "").strip()
    if candidate in _staff_recipient_ids():
        return PROBLEM_STAFF_RECIPIENT
    if "://" not in candidate:
        return PROBLEM_NOT_A_LINK

    client = _bot_address(client_bot_link())
    if client is None:
        # Сверить не с чем — значит, не подтверждено. Отказ, а не допуск.
        return PROBLEM_NO_CLIENT_LINK

    parts = urlsplit(candidate)
    if parts.scheme != "https" or (parts.hostname or "").lower() != client[0]:
        return PROBLEM_FOREIGN_HOST
    address = _bot_address(candidate)
    if address is None:
        return PROBLEM_NOT_A_BOT_LINK

    if address != client and address[1] in _salon_handles():
        return PROBLEM_SALON_BOT
    if address != client:
        return PROBLEM_OTHER_BOT
    query = parse_qsl(parts.query, keep_blank_values=True)
    if query and query != [("start", SUPPORT_START_PAYLOAD)]:
        return PROBLEM_UNEXPECTED_QUERY
    if parts.fragment:
        return PROBLEM_UNEXPECTED_QUERY
    return None


def shown_support_contact() -> str:
    """Значение ``AYLA_SUPPORT_CONTACT``, которое можно показать человеку, или ``""``.

    Заданное, но не прошедшее сторож значение не показывается никому: лучше
    «напишите в поддержку» без адреса, чем адрес внутреннего чата.
    """

    from django.conf import settings

    contact = str(getattr(settings, "AYLA_SUPPORT_CONTACT", "") or "").strip()
    if not contact or support_contact_problem(contact) is not None:
        return ""
    return contact
