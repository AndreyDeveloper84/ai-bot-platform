"""Автоматическая попытка связать соло-мастера с Ayla (§148, срез S2).

Решение владельца §148: **пробуем автоматически, падаем в
``SETUP_PENDING``, оператор добивает**. Падение здесь — не заглушка и не
недоделка: это правильный исход, у которого есть имя и продолжение.

### Две причины, и путать их нельзя

**Непосредственная причина падения на пилоте — прокси-ответ.**
``resolve_external_user`` на стороне каталога заводит прокси **лениво, на
первом обращении**, и для MAX-личности, не связанной с настоящим
аккаунтом, возвращает ``is_proxy=true``. Записать такой ключ запрещено
(``apps/catalog/master_state.py``: он занял бы колонку значением, по
которому совпадения не будет никогда), поэтому
:func:`link_solo_provider_to_ayla` отказывает, и это верно.

**Почему прокси нельзя превратить в настоящий силами бота** — другая
причина, и она не здесь. Связывание живёт за ручкой
``POST /internal/users/bind-external/``, которую страж
``IsIdentityProvisioningBearer`` закрывает для боевого токена бота **по
построению**: «bot-driven binding не поддерживается, пока не появится
verified ownership flow». Секрет провижининга в сервис бота не
выкладывается никогда.

Замер пилота 11.09.2026 (``176.119.159.141``, ``dev-web-1``) добавляет
третью краску: провижининг-токен там **не задан**, то есть ручка
выключена целиком — не «закрыта для бота», а недоступна никому. Оператор
сегодня связывает не кнопкой, а действием уровня базы.

Три факта названы порознь намеренно. Слитые в «не получилось», они
отправят следующего строить ручку, которая уже есть и закрыта осознанно.

### Форма, в которой автоматика достраивается сверху

Эта функция ничего не решает о готовности: она пробует и возвращает
**машинную причину** неудачи. Готовность по-прежнему считает
``setup_state`` по строке каталога, поэтому в день, когда связывание
начнёт получаться, здесь не изменится ни строки — изменится ответ
каталога, и состояние переедет в ``READY`` само.
"""

from __future__ import annotations

import logging
from typing import Optional

from apps.identity.services.solo_ayla_link import (
    SoloLinkRefused,
    link_solo_provider_to_ayla,
)

logger = logging.getLogger(__name__)

#: Ayla недоступна или ответила неразборчиво. Отличается от отказа по
#: содержанию: здесь повтор осмыслен, там — нет.
AYLA_UNREACHABLE = "ayla_unreachable"


def attempt_solo_link(master, bot_user) -> Optional[str]:
    """Попробовать связать соло-мастера. ``None`` — связали.

    Возвращает машинную причину неудачи, а не ``False``: «Ayla не
    ответила» и «ответила прокси» требуют разного — первое повторить,
    второе нет, — и одно слово на двоих отняло бы у оператора это
    различие.

    Исключений не выпускает: регистрация человека не должна падать
    оттого, что внешняя система недоступна. Человек получает кабинет и
    честное «пока не видно клиентам» в любом случае.
    """
    from apps.integrations.ayla.identity_client import (
        IdentityResolveError,
        resolve_identity,
    )
    from apps.integrations.ayla.user_proxy import external_user_id_for

    try:
        identity = resolve_identity(external_user_id_for(bot_user))
    except IdentityResolveError as exc:
        logger.info(
            "identity.solo_link.attempt_failed reason=%s master=%s detail=%s",
            AYLA_UNREACHABLE,
            getattr(master, "pk", None),
            exc,
        )
        return AYLA_UNREACHABLE

    try:
        link_solo_provider_to_ayla(
            master,
            ayla_user_id=identity.ayla_user_id,
            is_proxy=identity.is_proxy,
        )
    except SoloLinkRefused as exc:
        # Сторож отказал — и это ожидаемый исход, а не сбой. На пилоте
        # сегодня он будет приходить всегда с причиной `proxy_identity`.
        logger.info(
            "identity.solo_link.attempt_refused reason=%s master=%s",
            exc.reason,
            getattr(master, "pk", None),
        )
        return exc.reason

    # DRF-1790 — the master card now carries the real key; the person's
    # BotUser rows must follow in the same moment, or the bot keeps naming a
    # proxy subject on every subject-bound call until the next dependent
    # action happens to re-ask. Same writer as ensure_ayla_link, same rule
    # (proxy → real is the one permitted overwrite), the identity we already
    # hold — no second HTTP call.
    from apps.identity.services.ayla_link import persist_resolved_identity

    persist_resolved_identity(bot_user, identity, trigger="solo_link")
    logger.info("identity.solo_link.attempt_linked master=%s", getattr(master, "pk", None))
    return None
