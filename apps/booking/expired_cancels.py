"""Периодическая задача: добить отмены, чьё окно возврата истекло (DRF-2346).

Отмена из Mini App двухшаговая: сервер переводит запись в ``CANCEL_REQUESTED``
и держит пять секунд на возврат, а завершает её клиент — таймером на странице.
Клиент ненадёжен по природе: человек закрывает приложение, теряет сеть, гасит
вкладку. До этой задачи такая запись оставалась ``CANCEL_REQUESTED``
**навсегда**: напоминания не сняты, администратор видит «клиент попросил
отменить», мастер ждёт, слот занят. А человеку уже сказали, что запись
отменена.

### Почему здесь, а не в ``apps/bookings/tasks.py``

``BookingRequest`` принадлежит этому приложению, и читать его из соседнего
запрещает сторож границ (G9): второй читатель расходится с владельцем молча,
когда ``BOOKING_VIA_AYLA_REST`` перестаёт писать в эти таблицы. Задача живёт у
владельца строки, а ``tasks.py`` её только ввозит — тем же приёмом, что
``escalate_stale_reminders`` и ``send_post_visit_followups``.

### Почему переход не свой

Завершает отмену ``commit_cancel`` — ровно тот переход, что делает клиент.
Второго пути отмены здесь нет намеренно: он разошёлся бы с первым в снятии
напоминаний, в событиях и в аудите, и разошёлся бы молча. Два свойства берутся
оттуда и потому не могут разъехаться с клиентским путём:

* **возврат сильнее подметания.** ``commit_cancel`` перечитывает строку под
  замком и отказывает всему, что уже не ``CANCEL_REQUESTED``. Человек,
  успевший вернуть запись, получил ``CONFIRMED`` — подметание его не тронет.
  Различие идёт по состоянию, а не по тому, кто успел первым.
* **напоминания снимаются в том же ходе**, одной транзакцией. Завершить
  отмену, не сняв их, значило бы заменить один дефект другим: человек не
  придёт, а бот ему напомнит.

### Чего задача не делает

Человеку, чьё окно добил сервер, ничего не уходит: он уже закрыл приложение,
плашки нет. Нужно ли писать ему в чат — решение владельца, и оно вне этого
листа; названо, чтобы не всплыло потом как «а почему мне никто не сказал».
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task  # type: ignore[import-untyped]
from django.utils import timezone

from apps.booking.models import BookingRequest
from apps.booking.services.transitions import (
    UNDO_WINDOW_SECONDS,
    InvalidBookingTransition,
    commit_cancel,
)

logger = logging.getLogger(__name__)

#: Сколько строк добиваем за один проход. Тот же довод, что у ``BATCH_LIMIT``
#: в ``apps/bookings/tasks.py``: после простоя очередь может быть длинной, и
#: один тик не должен держать соединение с базой дольше, чем нужно.
EXPIRED_CANCEL_BATCH_LIMIT = 200


@shared_task(name="bookings.commit_expired_cancels")
def commit_expired_cancels() -> dict[str, int]:
    """Завершить отмены, чьё окно возврата истекло."""
    deadline = timezone.now() - timedelta(seconds=UNDO_WINDOW_SECONDS)
    counters = {"scanned": 0, "committed": 0, "skipped": 0}

    rows = list(
        BookingRequest.all_tenants.filter(
            status=BookingRequest.Status.CANCEL_REQUESTED,
            cancel_requested_at__lte=deadline,
        )
        .select_related("bot_user")
        .order_by("cancel_requested_at")[:EXPIRED_CANCEL_BATCH_LIMIT]
    )

    for row in rows:
        counters["scanned"] += 1
        actor = row.bot_user
        if actor is None:
            # Строка без человека: отмену не за кого доводить, и аудит назвал
            # бы пустое авторство. Такую строку разбирает человек, а не бет.
            logger.warning("booking.commit_expired_cancels.no_actor booking=%s", row.pk)
            counters["skipped"] += 1
            continue
        try:
            # Актёр — владелец записи: подметание не «отменяет за него», а
            # доводит до конца то, о чём он уже попросил.
            commit_cancel(row, actor=actor)
        except InvalidBookingTransition:
            # Строка ушла из-под нас между выборкой и переходом — чаще всего
            # это возврат, успевший внутри окна. Это не ошибка.
            counters["skipped"] += 1
            continue
        except Exception:  # noqa: BLE001 — одна больная строка не глушит остальные
            logger.exception("booking.commit_expired_cancels.failed booking=%s", row.pk)
            counters["skipped"] += 1
            continue
        counters["committed"] += 1

    if counters["committed"] or counters["skipped"]:
        logger.info(
            "booking.commit_expired_cancels.summary scanned=%d committed=%d skipped=%d",
            counters["scanned"],
            counters["committed"],
            counters["skipped"],
        )
    return counters
