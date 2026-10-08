"""Что человек просил забыть — одно правило для всех, кто пишет без его слов (DRF-2837).

Дедуп писателей памяти строится по **живым** строкам и надгробия не видит;
сторож забвения стоит на человеке целиком (``upc.soft_deleted_at`` /
``forget_all_requested_at``), а поштучное стирание в него не попадает. Писатель,
который пишет БЕЗ нового заявления человека, вернул бы стёртое, и выглядело бы
это обычной записью. Таких писателей два:

* мост короткой памяти (``apps.orchestrator.memory.evicted_review``, DRF-2511) —
  дочитывает старое сообщение, которого никто не повторял;
* писатель выводов (``apps.identity.services.memory_inferred``) — предложил бы
  снова то, что человек отклонил или стёр.

Живой ход сюда не ходит: сказать то же самое снова — право человека.

# Почему правило читает содержание надгробия

Чтобы не вернуть КОНКРЕТНЫЙ факт, его надо опознать — значит нужны
``(kind, key, value)``, а это и есть содержание. Маркера «без содержания»,
способного на то же, не бывает: род и зона запрещают слишком широко, хеш
значения из малого словаря перебирается. Поэтому отдельного хранилища запретов
нет: правило читает надгробия, пока они лежат
(``memory_deleter.TOMBSTONE_RETENTION``; после отзыва согласия — 24 часа).
После физической очистки запрета нет — помнить стёртое дольше удержания значит
хранить персональные данные после обещанного удаления.
"""

from __future__ import annotations

import uuid
from typing import Any

from apps.identity.models import MemoryEntry

#: Причины удаления, которые означают «человек попросил забыть ЭТО».
#:
#: Из них и только из них следует запрет на возврат: остальные причины —
#: не воля человека. ``ttl_purge`` — срок хранения, ``minor_protection`` —
#: защита несовершеннолетнего, ``unknown_legacy`` — след переноса; вернуть
#: факт после них не значит вернуть стёртое по просьбе.
#:
#: ``withdrawal`` и ``forget_all`` здесь для полноты, хотя до них дело обычно
#: не доходит: отзыв согласия ловит гейт согласия, «забудь всё» — надгробие на
#: человеке целиком. Перечислены, чтобы список читался как ответ на вопрос
#: «какие стирания — воля человека», а не как сегодняшний минимум.
ERASURE_BY_REQUEST: frozenset[str] = frozenset(
    {
        MemoryEntry.DELETION_REASON_USER_DELETE,
        MemoryEntry.DELETION_REASON_USER_REQUEST_MINIAPP,
        MemoryEntry.DELETION_REASON_FORGET_ALL,
        MemoryEntry.DELETION_REASON_WITHDRAWAL,
    }
)


def keys_erased_by_request(user_id: uuid.UUID | None) -> frozenset[tuple[str, Any, Any]]:
    """Ключи ``(kind, key, value)`` зелёных фактов, которые человек просил забыть.

    Источник строки (сказано или выведено) не важен: стёртое сказанное писатель
    выводов предлагать не вправе так же, как стёртое предложение.
    """
    if not user_id:
        return frozenset()

    rows = MemoryEntry.objects.filter(
        user_id=user_id,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        soft_deleted_at__isnull=False,
        deletion_reason__in=sorted(ERASURE_BY_REQUEST),
    )
    keys: set[tuple[str, Any, Any]] = set()
    for row in rows:
        content = row.content if isinstance(row.content, dict) else {}
        keys.add((row.kind, content.get("key"), content.get("value")))
    return frozenset(keys)


__all__ = ["ERASURE_BY_REQUEST", "keys_erased_by_request"]
