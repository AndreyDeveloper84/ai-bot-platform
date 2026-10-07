"""Любимый мастер из подтверждённой памяти — для личной полки «Твои места» (DRF-2830).

Решение владельца 06.10 (O-1b): любимого мастера, которого человек назвал
сам, можно поднять в полке его СОБСТВЕННЫХ салонов. Больше нигде: ключ
``favorite_masters`` между салонами не переходит
(``apps.identity.personal_fields.NEVER_CROSSES``), и в межсалонную выдачу
«Ayla подобрала» он не идёт никогда.

# Что этот модуль делает и чего — нет

Он СОБИРАЕТ элементы ``preferences`` для границы
``internal/recommendation/resolve/`` в режиме «свои салоны». Он их никуда НЕ
ОТПРАВЛЯЕТ: вызывающего у :func:`preferences_from_memory` пока нет. Каталог
сегодня принимает мастера только по id, а у бота в памяти — имя; отправка
подключается, когда каталог научится разрешать имя среди мастеров своих
салонов клиента.

# Имя, а не id

В памяти лежит имя, как оно прозвучало («к Анне» → «Анне») — решение владельца
28.09 п.12. Морфологии у бота нет, поэтому вместе с именем едут его основы —
тот же приём, которым бот ищет мастера по имени
(:func:`apps.marketplace.discovery._name_stem`): каталог сопоставит их по
вхождению. Сам бот имя в id не переводит: «свои салоны» клиента он не знает
(DRF-1626), а поиск имени по всему каталогу и был бы межсалонным применением.

# Происхождение факта

Где факт записан — ``MemoryEntry.source_tenant_id`` (DRF-2544):

* **глобальный бот** — сказано Ayla, а не салону: действует во всех своих
  салонах; каталогу едет литерал :data:`SOURCE_GLOBAL_BOT`, а не id строки
  сентинела, которой в каталоге нет;
* **id салона** — действует только в нём; каталог сам проверит, что салон
  среди своих;
* **неизвестно** (``NULL`` — так лежат все записи до DRF-2544) — факт НЕ едет:
  ``UNKNOWN_ORIGIN_NEVER_CROSSES``.

# Гейты

Заявка на удаление, отсутствие связки с Ayla, закрытая зелёная память — пусто.
Неподтверждённый вывод сюда не попадает: его отсекает
:func:`~apps.identity.services.memory_key_policy.read_current_view`. Любой сбой
— пусто: предпочтение мягкое, и полка без него остаётся полкой.

В журнал имя не пишется — только число собранных элементов.
"""

from __future__ import annotations

import logging
from typing import Any, Final

logger = logging.getLogger(__name__)

MEMORY_KEY: Final = "favorite_masters"

KIND_MASTER: Final = "master"
#: Из памяти — только мягкое. Жёстким предпочтение бывает лишь из текущей реплики.
STRENGTH_SOFT: Final = "soft"
ORIGIN_CONFIRMED_MEMORY: Final = "confirmed_memory"

#: Литерал каталога для факта, сказанного в глобальном боте
#: (``recommendation._types.MEMORY_SOURCE_GLOBAL_BOT``).
SOURCE_GLOBAL_BOT: Final = "global_bot"

#: Не больше трёх имён и трёх основ на имя; основа не длиннее 40 знаков —
#: границы согласованного формата (лист DRF-2831).
MAX_PREFERENCES: Final = 3
MAX_STEMS: Final = 3
MAX_STEM_LEN: Final = 40
#: Основа короче трёх знаков («Ян», «Ия») не едет: каталог такую не принимает
#: и отклонил бы весь запрос полки, а по двум буквам совпало бы пол-салона.
MIN_STEM_LEN: Final = 3


def preferences_from_memory(bot_user: Any) -> tuple[dict[str, Any], ...]:
    """Элементы ``preferences`` о любимых мастерах человека. Никогда не бросает."""
    try:
        return _collect(bot_user)
    except Exception:  # noqa: BLE001 — мягкое предпочтение не должно стоить человеку полки
        logger.exception("marketplace.shelf_preferences.failed")
        return ()


def _collect(bot_user: Any) -> tuple[dict[str, Any], ...]:
    from apps.consent.memory import can_store_green_memory
    from apps.identity.services.deletion_gate import deletion_gate
    from apps.identity.services.memory_key_policy import read_current_view

    ayla_user_id = getattr(bot_user, "ayla_user_id", None)
    if not ayla_user_id:
        return ()
    if deletion_gate(ayla_user_id).blocked:
        return ()
    if not can_store_green_memory(bot_user):
        return ()

    global_bot_id = _global_bot_id()
    out: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, ...], str]] = set()
    for fact in read_current_view(ayla_user_id).green_facts:
        content = fact.content if isinstance(fact.content, dict) else {}
        if content.get("key") != MEMORY_KEY:
            continue
        source = _source_on_the_wire(fact.source_tenant_id, global_bot_id)
        if source is None:
            continue
        name = content.get("value")
        if not isinstance(name, str) or not name.strip():
            continue
        stems = name_stems(name)
        if not stems:
            continue
        identity = (stems, source)
        if identity in seen:
            continue
        seen.add(identity)
        out.append(
            {
                "kind": KIND_MASTER,
                "name": name.strip(),
                "name_stems": list(stems),
                "strength": STRENGTH_SOFT,
                "origin": ORIGIN_CONFIRMED_MEMORY,
                "source_tenant_id": source,
            }
        )
        if len(out) == MAX_PREFERENCES:
            break
    logger.info("marketplace.shelf_preferences.collected count=%d", len(out))
    return tuple(out)


def name_stems(name: str) -> tuple[str, ...]:
    """Основы имени — в casefold, без падежных окончаний, не больше трёх.

    Пусто значит «это не имя»: такой факт не едет вовсе, а не едет «как есть».
    """
    from apps.marketplace.discovery import _name_stem, _name_tokens

    stems: list[str] = []
    for token in _name_tokens(name):
        stem = _name_stem(token)[:MAX_STEM_LEN]
        if len(stem) >= MIN_STEM_LEN and stem not in stems:
            stems.append(stem)
    return tuple(stems[:MAX_STEMS])


def _global_bot_id() -> Any:
    from apps.identity.services.global_tenant import find_global_bot_tenant

    sentinel = find_global_bot_tenant()
    return sentinel.id if sentinel is not None else None


def _source_on_the_wire(source_tenant_id: Any, global_bot_id: Any) -> str | None:
    """Происхождение факта для каталога — или ``None``: факт не едет.

    Сентинела нет в базе — глобальный факт от салонного не отличить; тогда его
    id уезжает как id салона, каталог не найдёт такой салон среди своих и
    отбросит предпочтение. Это сужение, а не расширение.
    """
    if source_tenant_id is None:
        return None
    if global_bot_id is not None and source_tenant_id == global_bot_id:
        return SOURCE_GLOBAL_BOT
    return str(source_tenant_id)


__all__ = [
    "KIND_MASTER",
    "MAX_PREFERENCES",
    "MAX_STEMS",
    "MEMORY_KEY",
    "MIN_STEM_LEN",
    "ORIGIN_CONFIRMED_MEMORY",
    "SOURCE_GLOBAL_BOT",
    "STRENGTH_SOFT",
    "name_stems",
    "preferences_from_memory",
]
