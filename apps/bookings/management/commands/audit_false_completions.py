"""``manage.py audit_false_completions`` — перепись уже поставленных ложных штампов (DRF-2462).

Часовой детектор до DRF-2454 ключевался на ``BookingRequest.status``, которую не
двигает ни одно входящее событие, и штамповал ``completed_at`` визитам, которые
канон уже отменил. Производство остановлено (#2072 слит), остались **поставленные**
штампы. Эта команда их **считает и разбирает по состояниям канона**; она ничего не
меняет вовсе — ни в сухом прогоне, ни как-либо иначе.

# Почему только чтение

Снимать штамп — решение владельца, и скрипт для него готовит главное окно
(``--apply`` здесь нет намеренно: команда, которая умеет писать, однажды
запускается с флагом «посмотреть» и без него). Здесь только предъявление факта.

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

# Очередь событий — часть переписи, а не отдельный вопрос

По каждой строке печатается, **что уже лежит в очереди**: события
``booking.completed`` из ``DomainEvent`` с этим ``booking_id``, и сколько из них
ещё не разобрано (``is_dispatched=False``). Разбор очереди выпустит отзывы, баллы
и сегменты — необратимо. Поэтому уборка и очередь согласуются **до** того, как
кто-то тронет любую из двух, и для этого их надо видеть рядом.

# Ноль доказывается охватом

Первым числом печатается охват: сколько строк вообще несёт ``completed_at``. При
пустом охвате (не та база, не тот стенд) все числа ниже — тоже нули, и отчёт
прочитался бы как «ложных штампов нет». Поэтому при нулевом охвате сказано прямо,
что нули ничего не доказывают.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from django.core.management.base import BaseCommand

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
        "канона и по очереди событий. Только чтение — ничего не меняет."
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
        w("")
        w("снятие штампов здесь не делается: это решение владельца и отдельный скрипт.")
