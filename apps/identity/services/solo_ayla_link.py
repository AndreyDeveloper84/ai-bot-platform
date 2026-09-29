"""Единственная дверь, через которую соло-мастер получает канонический ключ.

### Зачем отдельный модуль, а не строка в онбординге

У ``CatalogMaster.ayla_user_id`` сегодня ДВА писателя, и я узнал это от
собственного теста-переписи, а не до него:

* ``apps/catalog/services/upserter.py:204`` — синхронизация, ключ
  приезжает из выгрузки Ayla;
* ``apps/master_api/views.py:691`` — принятие приглашения, blank-fill из
  ``bot_user.ayla_user_id``.

Соло-мастера Ayla не создавала и в выгрузке его нет, поэтому первый его
не свяжет **никогда**. Второй мог бы — но он берёт ``bot_user.ayla_user_id``,
который ``ensure_ayla_link`` пишет **не проверяя ``is_proxy``**: для
человека без подтверждённого владения там лежит прокси. На сегодняшних
данных дыра не выстреливает (у 31 мастера из 34 ключ уже стоит от
синхронизации, а blank-fill чужое не перезаписывает), но соло-строка —
ровно тот случай, где ключа нет и писать будет нечему помешать.

Поэтому третий писатель, а не переиспользование второго: дверь узкая, с
проверками, и стоит там, где известно, что ключ должен быть настоящим.

### Что здесь проверяется и почему именно это

**Подставной ключ не пишется никогда.** ``master_state.py`` говорит
прямо: прокси-id занял бы ключ значением, по которому совпадения не
будет никогда, — то есть навсегда сломал бы сопоставление, оставив
строку выглядящей связанной. ``resolve_external_user`` заводит прокси
**лениво, на первом же обращении** (``users/services.py``), так что
ответ «вот твой ключ, is_proxy=true» — не редкий случай, а обычный:
именно его получит бот для человека, чья MAX-идентичность ещё не
связана с настоящим аккаунтом.

**Чужой ключ не затирается молча.** Если в строке уже стоит ДРУГОЙ ключ,
это не повторный вызов, а столкновение двух личностей, и разрешать его
перезаписью значит выбрать одну из них не глядя.

**Повтор с тем же ключом безвреден.** Связывание пойдёт по пути с
повторами (оператор, ретрай, второй заход человека), и отказ на
идентичном повторе превратил бы нормальный сценарий в инцидент.

### Чего здесь НЕТ

Здесь нет получения ключа. Откуда он берётся — вопрос потока
подтверждения владения (§122, вариант В), и он решается не тут. Эта
функция принимает уже полученный ответ и отвечает на один вопрос:
**можно ли его записывать**. Все три обсуждаемых варианта — бот ведёт
OTP, человек приносит код из приложения, оператор связывает руками —
сходятся ровно в этой точке, поэтому дверь строится до выбора между
ними.
"""

from __future__ import annotations

import logging
from uuid import UUID

logger = logging.getLogger(__name__)


class SoloLinkRefused(Exception):
    """Ключ отвергнут — связывание не состоялось, строка не тронута."""

    #: Ответ назвал подставного пользователя.
    PROXY = "proxy_identity"
    #: Ключа в ответе нет вовсе.
    MISSING = "missing_key"
    #: В строке уже стоит другой ключ.
    CONFLICT = "already_linked_to_another"

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


def link_solo_provider_to_ayla(master, *, ayla_user_id, is_proxy) -> bool:
    """Записать канонический ключ соло-мастеру — или отказать.

    Возвращает ``True``, если ключ записан этим вызовом, и ``False``,
    если он уже стоял (повтор). Отказ — исключение, а не ``False``:
    «не записали, потому что нельзя» и «не записали, потому что уже
    было» — разные события, и складывать их в один возврат значит
    потерять то самое различие, ради которого функция существует.

    Args:
        master: строка ``CatalogMaster`` соло-мастера.
        ayla_user_id: ключ из ответа ``internal/me/identity/``.
        is_proxy: оттуда же — настоящий это аккаунт или подставной.
    """
    if is_proxy:
        # Ленивое создание прокси — обычный ответ, а не край. Записать
        # его значит занять ключ значением, по которому совпадения не
        # будет никогда, и строка при этом станет выглядеть связанной.
        logger.info(
            "identity.solo_link.refused reason=%s master=%s",
            SoloLinkRefused.PROXY,
            getattr(master, "pk", None),
        )
        raise SoloLinkRefused(
            SoloLinkRefused.PROXY,
            "ответ назвал подставного пользователя; связывания не было",
        )

    if not ayla_user_id:
        logger.info(
            "identity.solo_link.refused reason=%s master=%s",
            SoloLinkRefused.MISSING,
            getattr(master, "pk", None),
        )
        raise SoloLinkRefused(
            SoloLinkRefused.MISSING,
            "в ответе нет ayla_user_id",
        )

    incoming = ayla_user_id if isinstance(ayla_user_id, UUID) else UUID(str(ayla_user_id))
    current = master.ayla_user_id

    if current is not None and current != incoming:
        logger.warning(
            "identity.solo_link.refused reason=%s master=%s current=%s incoming=%s",
            SoloLinkRefused.CONFLICT,
            master.pk,
            current,
            incoming,
        )
        raise SoloLinkRefused(
            SoloLinkRefused.CONFLICT,
            f"строка уже связана с {current}, пришёл {incoming}",
        )

    if current == incoming:
        return False

    master.ayla_user_id = incoming
    master.save(update_fields=["ayla_user_id"])
    logger.info(
        "identity.solo_link.written master=%s ayla_user_id=%s",
        master.pk,
        incoming,
    )
    return True
