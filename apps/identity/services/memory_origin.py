"""Откуда пришёл факт памяти — ``MemoryEntry.source_tenant_id`` (DRF-2544).

Правило владельца 24.08 (``docs/OD_MEMORY.md`` §3): сказанное самим человеком
переходит между салонами, наблюдённое и выведенное — не переходит; различие
делается по ``source_tenant_id``. До этой правки поле не заполнял ни один
писатель: на стенде 28.09 оно пусто у всех пяти записей, и «глобальный факт»
нельзя было отличить от «не заполнили».

Значение решается здесь, в момент записи, в одном месте — его зовёт
:func:`apps.identity.services.memory_writer.write_entry`, так что его получают
все места записи, а не те, кто вспомнил передать. По порядку:

1. **Глобальная поверхность** (:func:`global_surface_scope`, ставится на
   границе глобального обработчика) → id сентинела ``global_bot``. Раньше
   салона в области: внутри глобального хода бывает ``tenant_scope`` мастера
   (передача записи), но человек говорил с Ayla, а не с салоном.
2. **Салон в области** (``current_tenant()``, его ставит потребитель
   салонного потока) → id этого салона.
3. **Ни то, ни другое** → :data:`ORIGIN_UNKNOWN`. Названо, а не случайно:
   так лежат все записи до этой правки, и так ляжет запись с пути, который
   не объявил себя. Что читатель обязан делать с неизвестным происхождением —
   ``apps.identity.personal_fields.UNKNOWN_ORIGIN_NEVER_CROSSES``.

Явно переданный ``source_tenant_id`` побеждает всё это.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

logger = logging.getLogger(__name__)

#: Происхождение неизвестно: запись до DRF-2544 или с пути, который не
#: объявил ни салон, ни глобальную поверхность. Не «глобальный факт» —
#: глобальный факт несёт id сентинела ``global_bot``.
ORIGIN_UNKNOWN: uuid.UUID | None = None

_GLOBAL_SURFACE: ContextVar[bool] = ContextVar("memory_global_surface", default=False)


@contextmanager
def global_surface_scope() -> Iterator[None]:
    """Всё внутри — ход глобальной Ayla: факты пишутся с сентинелом ``global_bot``.

    Ставить на границе, которая знает ответ (глобальный обработчик), как
    ``bot_scope`` и ``tenant_scope``; ``finally`` — чтобы признак не перешёл
    на следующее сообщение того же воркера.
    """

    token = _GLOBAL_SURFACE.set(True)
    try:
        yield
    finally:
        _GLOBAL_SURFACE.reset(token)


def resolve_source_tenant_id() -> uuid.UUID | None:
    """Салон, в котором сказан записываемый факт; правило — в докстринге модуля."""

    if _GLOBAL_SURFACE.get():
        from apps.identity.services.global_tenant import find_global_bot_tenant

        sentinel = find_global_bot_tenant()
        if sentinel is None:
            # Сентинел ставит сид-миграция; создавать его из записи памяти —
            # не дело писателя. Без него честнее «неизвестно», чем догадка.
            logger.warning("identity.memory_origin.global_sentinel_missing")
            return ORIGIN_UNKNOWN
        return sentinel.id

    from apps.tenancy.context import current_tenant

    tenant = current_tenant()
    if tenant is not None:
        return tenant.id

    logger.info("identity.memory_origin.unknown")
    return ORIGIN_UNKNOWN
