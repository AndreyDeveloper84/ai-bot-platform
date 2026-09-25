"""Чем доказывается «визит состоялся» до штампа `completed_at` (DRF-2454).

Часовой детектор (``apps.bookings.tasks.detect_completed_bookings``) ключевался
на ``BookingRequest.status == CONFIRMED``. Это **не состояние визита**: репозиторий
сам это и говорит рядом — «``BookingRequest.status`` is the legacy path's
terminal-state signal and **no inbound event ever moves it**»
(``apps/bookings/followups.py``). Канон — единственная машина состояний визита
(ADR-0009, «no dual-write»), и его отмены доезжают до **зеркала**
(``RemoteBookingProxy``), а не до этой колонки.

Замер стенда (окно b6, 24.09): 9 из 11 строк несут ``completed_at`` при
``status=confirmed``; у их каталожных двойников ``completed`` 3, ``cancelled`` 3,
``confirmed`` 2, ``awaiting_payment`` 1. То есть детектор объявил состоявшимися
**три отменённых визита и один неоплаченный**.

# Правило здесь: штамп только по положительному свидетельству

Не «штампуем, пока не доказано обратное», а наоборот. Цена ошибок несимметрична:

* не поставить штамп состоявшемуся визиту — **обратимо**: строка закроется на
  следующем тике, когда свидетельство появится, и ни одно последствие не
  выстрелило;
* поставить штамп отменённому — **необратимо** после разбора очереди: запрошенный
  отзыв, начисленные баллы и изменённый сегмент не отзываются.

Поэтому отсутствие свидетельства — отказ, а не разрешение.

# Чем сопоставляется строка и зеркало, и почему это названо честно

У ``BookingRequest`` **нет** ссылки на визит каталога: id визита не хранится
нигде рядом с этой строкой (перепись в теле PR). Поэтому пара ищется по ключу
``(салон, человек, время начала)``. Это **соответствие, а не тождество**, и
поэтому:

* две подходящие строки зеркала — отказ ``ambiguous``: угадывать, какая из них
  этот визит, значит решать судьбу последствий подбрасыванием монеты;
* нет человека в строке (``bot_user`` пуст) — отказ ``no_key``: ключа нет;
* нет подходящего зеркала — отказ ``no_mirror``. Это **дорогой** отказ: устаревшие
  строки YClients зеркала не имеют по построению и перестанут закрываться
  автоматически. Цена названа в теле PR; настоящая починка — хранить id визита
  рядом со строкой, и это отдельный лист, а не тихая правка здесь.

# Что НЕ делается

Не трогаются уже поставленные штампы (шесть ложных строк — отдельный лист: сперва
перестать производить). Не меняется ``BookingRequest.status`` — писать в него
здесь значило бы ровно тот dual-write, который запрещён. Не читается каталог по
HTTP: зеркало уже есть, и второй источник того же факта разошёлся бы с ним.
"""

from __future__ import annotations

from typing import Any

#: Состояния зеркала, при которых визит СЧИТАЕТСЯ состоявшимся для штампа.
#: ``confirmed`` здесь потому, что канон присылает ``booking.completed`` не
#: всегда (это названная дыра, см. ``followups._should_send_b11_ayla``), и
#: требовать ``completed`` значило бы не закрывать почти ничего. Штамп — это
#: «часы досчитали, и канон не возражает», а не «канон подтвердил приход».
MIRROR_ALLOWS: tuple[str, ...] = ("confirmed", "completed")

#: Состояния, при которых штамп не ставится никогда: визита либо не было, либо
#: он ещё не оплачен / не подтверждён. Именно здесь жили три отмены и одна
#: неоплата со стенда.
MIRROR_REFUSES: tuple[str, ...] = ("cancelled", "no_show", "pending_payment", "tentative")

#: Причины отказа — машинные имена, они же уходят в счётчики и в лог.
REASON_NO_KEY = "no_key"
REASON_NO_MIRROR = "no_mirror"
REASON_AMBIGUOUS = "ambiguous"


def mirror_evidence(booking: Any) -> tuple[bool, str]:
    """Можно ли штамповать этот визит, и почему — одной машинной причиной.

    Returns:
      ``(True, "mirror_<status>")`` — зеркало нашлось и не возражает;
      ``(False, "<reason>")`` — свидетельства нет либо оно против.
    """

    from apps.booking.models import RemoteBookingProxy

    if booking.bot_user_id is None or booking.visit_at is None:
        return (False, REASON_NO_KEY)

    statuses = list(
        RemoteBookingProxy.all_tenants.filter(
            tenant_id=booking.tenant_id,
            bot_user_id=booking.bot_user_id,
            start_at=booking.visit_at,
        ).values_list("status", flat=True)[:2]
    )
    if not statuses:
        return (False, REASON_NO_MIRROR)
    if len(statuses) > 1:
        # Ключ неточен по построению: две строки на один ключ — не повод
        # выбрать любую, а повод не решать.
        return (False, REASON_AMBIGUOUS)

    status = statuses[0]
    if status in MIRROR_ALLOWS:
        return (True, f"mirror_{status}")
    # Незнакомое состояние зеркала тоже отказ: список разрешающих — положительный,
    # и новое состояние канона не должно молча попасть в «состоялся».
    return (False, f"mirror_{status}")


__all__ = [
    "MIRROR_ALLOWS",
    "MIRROR_REFUSES",
    "REASON_AMBIGUOUS",
    "REASON_NO_KEY",
    "REASON_NO_MIRROR",
    "mirror_evidence",
]
