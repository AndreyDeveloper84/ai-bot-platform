"""``manage.py audit_false_completions`` — перепись уже поставленных ложных штампов (DRF-2462).

Часовой детектор до DRF-2454 ключевался на ``BookingRequest.status``, которую не
двигает ни одно входящее событие, и штамповал ``completed_at`` визитам, которые
канон уже отменил. Производство остановлено (#2072 слит), остались **поставленные**
штампы. Эта команда их **считает и разбирает по состояниям канона**, а по
отдельному решению владельца — снимает у доказанно ложных.

# Сухой прогон по умолчанию, ``--apply`` — по слову владельца

Умолчание — чтение: числа, разбивка, очередь, ничего не меняется. ``--apply``
снимает ``completed_at`` и ``completed_by`` **только** у корзины ``false_by_canon``
и ничего больше. Гейт здесь человеческий, а не технический: это коррекция боевых
данных, и запускает её владелец своим решением (DRF-2462, этап 7 промпта). Команда
отвечает за другое — она **не умеет** тронуть законную строку, недоказуемую строку,
чужое поле или очередь событий.

# Три корзины, и признак законности назван

Ошибка возможна в обе стороны: оставить ложный штамп и снять законный. Поэтому
строки раскладываются по **доказательству**, а не по подозрению:

* ``false_by_canon`` — штамп поставлен часами (``completed_by=system``), а зеркало
  канона говорит, что визита не было: ``cancelled`` · ``no_show`` ·
  ``pending_payment`` · ``tentative``. Это и есть предмет уборки;
* ``legitimate`` — есть положительное доказательство: зеркало ``completed``
  **или** закрывал человек (``completed_by`` не ``system``). Такую строку не
  трогают: «зеркало отстало, а визит был» — живой случай, и снять штамп значило бы
  отнять у мастера состоявшийся визит;
* ``unprovable`` — зеркала нет (``no_mirror``), их два на один ключ
  (``ambiguous``) или в строке нет человека (``no_key``). Доказательства нет **ни
  в одну сторону**, и обнулять здесь — та же ошибка, что штамповать: решение
  требует id визита рядом со строкой (DRF-2461), а не догадки по времени.

Ключ сопоставления со зеркалом — тот же, что у детектора
(:func:`apps.bookings.completion_evidence.mirror_evidence`): одно правило, один
дом. Две копии одного сопоставления разошлись бы молча, и уборка чистила бы не то,
что производил детектор.

# Предел, который надо знать ДО ``--apply``: зеркало — копия, а не канон

Всё выше читает ``RemoteBookingProxy`` — **зеркало** канона в базе бота. У копии
есть **измеренное** расхождение с источником: DRF-2437, визит ``f3deff6f…`` (бот) /
``186a7d50…`` (каталог) — бот и зеркало говорят ``confirmed``, а каталожная
``appointments_appointment`` — ``awaiting_payment``. Отсюда:

* ``legitimate`` означает «зеркало не возражает», а **не** «канон подтвердил»:
  неоплаченный визит может попасть в эту корзину, пока копия отстаёт;
* ``false_by_canon`` от этого не страдает: там зеркало говорит ПРОТИВ штампа, и
  ошибка возможна только в сторону отказа от уборки, а не лишней уборки;
* поэтому перед снятием штампов состояние каждой строки-кандидата читается у
  **источника**: ``docs/drf2462_canon_crosscheck.sql`` (read-only, база каталога).
  Без этой сверки ``--apply`` не готов — так и сказано владельцу.

# Очередь событий — часть переписи, а не отдельный вопрос

По каждой строке печатается, **что уже лежит в очереди**: события
``booking.completed`` из ``DomainEvent`` с этим ``booking_id``, и сколько из них
ещё не разобрано (``is_dispatched=False``). Разбор очереди выпустит отзывы, баллы
и сегменты — необратимо. Поэтому уборка и очередь согласуются **до** того, как
кто-то тронет любую из двух, и для этого их надо видеть рядом.

# Почему команда живёт в ``apps/booking/``, а не рядом с детектором

``BookingRequest`` принадлежит этому приложению, и страж границ (G9,
``tools/lint/import_boundaries.py``) прав: читать её из ``apps/bookings/`` значит
завести второй источник того же факта. Перепись — чтение владельца о своей
модели, поэтому команда стоит здесь; правило сопоставления с зеркалом по-прежнему
одно и лежит у детектора (``apps.bookings.completion_evidence``), потому что
копия правила разошлась бы молча. Внести себя в BASELINE стража было бы дешевле и
неправдой: там человеческий вердикт о **неизбежном** чтении, а это чтение
избежать можно — достаточно стоять в своём приложении.

# Ноль доказывается охватом

Первым числом печатается охват: сколько строк вообще несёт ``completed_at``. При
пустом охвате (не та база, не тот стенд) все числа ниже — тоже нули, и отчёт
прочитался бы как «ложных штампов нет». Поэтому при нулевом охвате сказано прямо,
что нули ничего не доказывают.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

#: Состояния зеркала, при которых поставленный штамп — ложный.
from apps.bookings.completion_evidence import MIRROR_REFUSES  # noqa: E402

#: Кто подписан под закрытием, когда его поставили часы.
SYSTEM_CLOSER = "system"

BUCKET_FALSE = "false_by_canon"
BUCKET_LEGITIMATE = "legitimate"
BUCKET_UNPROVABLE = "unprovable"


def _classify(booking: Any) -> tuple[str, str]:
    """(корзина, причина) для одной строки со штампом.

    Причина — машинное имя: ``mirror_cancelled``, ``human_closer``,
    ``mirror_completed``, ``no_mirror``, ``ambiguous``, ``no_key``.
    """

    from apps.bookings.completion_evidence import mirror_evidence

    if booking.completed_by and booking.completed_by != SYSTEM_CLOSER:
        # Человек назвал себя закрывающим — это положительное доказательство,
        # и оно сильнее отставшего зеркала.
        return (BUCKET_LEGITIMATE, "human_closer")

    may_stamp, reason = mirror_evidence(booking)
    if reason == "mirror_completed":
        return (BUCKET_LEGITIMATE, reason)
    if reason.startswith("mirror_"):
        state = reason.removeprefix("mirror_")
        if state in MIRROR_REFUSES:
            return (BUCKET_FALSE, reason)
        # Незнакомое состояние канона: не объявляем ложным по незнанию.
        return (BUCKET_UNPROVABLE, reason)
    if may_stamp:
        # ``mirror_confirmed`` — зеркало не возражает, но и не подтверждает
        # приход. Штамп по нему поставлен по правилу детектора, и уборке он не
        # принадлежит: это состояние «часы досчитали, канон не спорил».
        return (BUCKET_LEGITIMATE, reason)
    return (BUCKET_UNPROVABLE, reason)


class Command(BaseCommand):
    help = (
        "Перепись поставленных ложных штампов завершения: разбивка по состояниям "
        "канона и по очереди событий. Сухой прогон по умолчанию; --apply снимает "
        "штампы ТОЛЬКО у корзины false_by_canon и только по решению владельца."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--tenant",
            default="",
            help="сузить до одного салона по слагу (по умолчанию — все)",
        )
        parser.add_argument(
            "--ids",
            action="store_true",
            help="печатать id строк корзины false_by_canon (для согласования уборки)",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help=(
                "снять completed_at/completed_by у строк false_by_canon. Только по "
                "отдельному решению владельца: это коррекция боевых данных"
            ),
        )

    def handle(self, *args, **options) -> None:
        from django.conf import settings
        from django.db import connection
        from django.utils import timezone

        from apps.booking.models import BookingRequest
        from apps.eventbus.models import DomainEvent

        slug = options["tenant"].strip()
        rows = BookingRequest.all_tenants.filter(completed_at__isnull=False)
        if slug:
            rows = rows.filter(tenant__slug=slug)
        rows = rows.select_related("tenant").order_by("visit_at")

        stamped = list(rows)
        buckets: Counter[str] = Counter()
        reasons: Counter[str] = Counter()
        false_rows: list[Any] = []
        for booking in stamped:
            bucket, reason = _classify(booking)
            buckets[bucket] += 1
            reasons[f"{bucket}:{reason}"] += 1
            if bucket == BUCKET_FALSE:
                false_rows.append(booking)

        # Очередь: события по ЭТИМ строкам, и сколько из них ещё не разобрано.
        false_ids = [str(b.pk) for b in false_rows]
        queued_total = 0
        queued_pending = 0
        if false_ids:
            events = DomainEvent.objects.filter(
                event_name="booking.completed",
                data__booking_id__in=false_ids,
            )
            queued_total = events.count()
            queued_pending = events.filter(is_dispatched=False).count()

        db = settings.DATABASES["default"]
        w = self.stdout.write
        w(
            f"предмет: база {db.get('NAME')}@{db.get('HOST') or 'local'} · vendor {connection.vendor}"
        )
        w(f"снято:   {timezone.now().isoformat(timespec='seconds')}")
        w(f"область: BookingRequest со штампом completed_at · салон {slug or 'все'}")
        w("режим:   только чтение, ничего не меняется")
        w("")
        # Охват — ПЕРВЫМ числом: ноль ниже честен только на непустом охвате.
        w(f"охват (строк со штампом): {len(stamped)}")
        if not stamped:
            w(
                "ВНИМАНИЕ: охват пуст — нули ниже ничего не доказывают. "
                "Проверьте базу, слаг салона и что это тот стенд."
            )
        w("")
        w(f"ложных по канону (false_by_canon): {buckets[BUCKET_FALSE]}")
        w(f"законных (legitimate):             {buckets[BUCKET_LEGITIMATE]}")
        w(f"недоказуемых (unprovable):         {buckets[BUCKET_UNPROVABLE]}")
        w("")
        w("по причинам:")
        for key in sorted(reasons):
            w(f"  {key:<40} {reasons[key]:>4}")
        w("")
        w("очередь по ложным строкам:")
        w(f"  событий booking.completed:  {queued_total}")
        w(f"  из них НЕ разобрано:        {queued_pending}")
        if queued_pending:
            w(
                "  ВНИМАНИЕ: разбор очереди выпустит по этим строкам отзывы, баллы и "
                "сегменты — необратимо. Уборка и очередь согласуются вместе."
            )
        if options["ids"] and false_rows:
            w("")
            w("id строк false_by_canon (для согласования уборки):")
            for booking in false_rows:
                w(f"  {booking.pk} · {booking.tenant.slug} · визит {booking.visit_at.isoformat()}")

        if not options["apply"]:
            w("")
            w(
                "сухой прогон: ничего не изменено. Снятие — тем же вызовом с --apply, "
                "и только по отдельному решению владельца."
            )
            return

        # Коррекция боевых данных. Число кандидатов напечатано ВЫШЕ, до первой
        # записи: оператор видит, что берётся, а не узнаёт после.
        w("")
        w(f"ЗАПИСЬ (--apply): кандидатов {len(false_rows)}")
        cleared, skipped = self._clear(false_rows, w)
        w("")
        w(f"снято штампов: {cleared} · пропущено (строка изменилась): {skipped}")
        w(
            "производные показатели (счётчик визитов мастера, «последняя услуга») "
            "пересчитываются из источника сами: они читают completed_at запросом, "
            "а не хранят копию."
        )

    @staticmethod
    def _clear(false_rows: list[Any], w: Any) -> tuple[int, int]:
        """Снять штамп у названных строк — по одной, под блокировкой, с перепроверкой.

        Три свойства, каждое по своей причине:

        * **перепроверка под блокировкой.** Между переписью и записью строка могла
          измениться (мастер подтвердил визит, зеркало доехало). Классификация
          повторяется внутри транзакции на свежей строке, и если она больше не
          ``false_by_canon`` — строка **пропускается**, а не чистится по устаревшему
          вердикту;
        * **идемпотентность по построению.** Область команды — строки со штампом;
          снятая строка в неё больше не попадает, поэтому повторный прогон ничего
          не находит и ничего не делает. Это свойство запроса, а не флага;
        * **before/after в журнал.** Коррекция боевых данных обязана оставлять след:
          что было, что стало и по какой причине строка признана ложной.
        """

        from django.db import transaction

        from apps.booking.models import BookingRequest

        cleared = 0
        skipped = 0
        for row in false_rows:
            with transaction.atomic():
                fresh = (
                    BookingRequest.all_tenants.select_for_update()
                    .filter(pk=row.pk, completed_at__isnull=False)
                    .first()
                )
                if fresh is None:
                    skipped += 1
                    continue
                bucket, reason = _classify(fresh)
                if bucket != BUCKET_FALSE:
                    w(f"  ПРОПУСК {fresh.pk}: стало {bucket}:{reason}")
                    skipped += 1
                    continue
                before_at = fresh.completed_at.isoformat() if fresh.completed_at else ""
                before_by = fresh.completed_by
                BookingRequest.all_tenants.filter(pk=fresh.pk).update(
                    completed_at=None, completed_by=""
                )
                cleared += 1
                w(
                    f"  СНЯТО {fresh.pk}: {reason} · было completed_at={before_at} "
                    f"completed_by={before_by!r} → стало NULL/''"
                )
                logger.warning(
                    "booking.false_completion.cleared booking=%s reason=%s "
                    "before_completed_at=%s before_completed_by=%r",
                    fresh.pk,
                    reason,
                    before_at,
                    before_by,
                )
        return (cleared, skipped)
