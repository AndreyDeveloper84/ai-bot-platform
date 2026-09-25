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
* ``legitimate`` — есть положительное доказательство: канон ``completed``, зеркало
  ``completed`` **или** закрывал человек (``completed_by`` не ``system``). Такую
  строку не трогают: снять штамп значило бы отнять у мастера состоявшийся визит;
* ``unprovable`` — зеркала нет (``no_mirror``), их два на один ключ
  (``ambiguous``), в строке нет человека (``no_key``) **или зеркало говорит
  ``confirmed``**. Последнее важно: ``confirmed`` в копии значит лишь «на момент
  заведения канон не спорил», и доказательством визита он не является.
  Доказательства нет ни в одну сторону, и обнулять здесь — та же ошибка, что
  штамповать.

Ключ сопоставления со зеркалом — тот же, что у детектора
(:func:`apps.bookings.completion_evidence.mirror_evidence`): одно правило, один
дом. Две копии одного сопоставления разошлись бы молча, и уборка чистила бы не то,
что производил детектор.

# Зеркало стухает, поэтому решает канон: ``--canon-file`` (DRF-2519)

Зеркало (``RemoteBookingProxy``) — копия канона, и копия **односнимочная**: у неё
нет даже ``updated_at``, строка пишется при заведении и последующие канонические
переходы в неё не приезжают. Замер 25.09 (read-only, обе базы): из десяти строк со
штампом зеркало расходится с каноном на **пяти**, и всегда в одну сторону —
зеркало стоит на ``confirmed``, канон ушёл дальше (``completed`` у трёх,
``awaiting_payment`` у двух).

Что это значит для чисел: **по зеркалу ложных 3, по канону 5.** Два неоплаченных
визита зеркало прячет, показывая ``confirmed``; оба штампа поставлены НОВЫМ
детектором после #2072 — он спросил копию, а копия не знала. Это не отменяет
#2072 (тот убрал ключевание на локальном ``status``), но его презумпция «канон не
спорил» на деле означает «копия, снятая при заведении, не спорит».

Отсюда порядок работы:

* ``--canon`` — **живой опрос канона** по каждой строке: ``GET appointments/{id}/``
  через :meth:`…booking_client.AylaBookingHTTPClient.get_appointment_version`.
  Ключ запроса — ``appointment_id`` зеркала: **идентификатор не стухает**, стухает
  только статус (находка окна DRF-2519). Канон не ответил → строка
  ``unprovable:canon_unavailable``, и зеркало вместо него НЕ подставляется:
  направление его ошибки уже известно;
* ``--canon-file`` — тот же ответ, но снимком: сверка из
  ``docs/drf2462_canon_crosscheck.sql`` (read-only в базе каталога), для случая,
  когда REST недоступен. Живой опрос сильнее файла и перекрывает его;
* без файла решает зеркало, и отчёт об этом **говорит строкой** ``решает: ЗЕРКАЛО``
  с предупреждением: неоплаченные визиты в ложные не попадут;
* ``false_by_canon`` по зеркалу не бывает лишним (там копия говорит ПРОТИВ
  штампа) — он бывает **неполным**, и именно поэтому одно число без другого
  показывать оператору нельзя;
* **``--apply`` снимает штамп только по свидетельству КАНОНА.** Строка, признанная
  ложной одним зеркалом, печатается и **пропускается**. Это не осторожность, а
  число: замер 25.09 нашёл шесть визитов, которые канон завершил, а зеркало держит
  на ``confirmed``, потому что их письма ``booking.completed`` мертвы (500, бюджет
  попыток исчерпан). Правило «зеркало не ``completed`` → штамп ложный» сняло бы до
  **шести законных** штампов. И отдельно: ``confirmed`` в зеркале бот местами
  пишет **сам константой** (``skills/booking/tools.py::_upsert_remote_booking_proxy``
  при подтверждении и переносе), так что это вообще не свидетельство канона.

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


#: Канонические состояния, при которых визит НЕ состоялся (написание каталога).
CANON_FALSE: tuple[str, ...] = ("cancelled", "awaiting_payment", "no_show", "pending_payment")
#: Канонические состояния, подтверждающие визит.
CANON_TRUE: tuple[str, ...] = ("completed",)


def _classify(booking: Any, canon: dict[str, str] | None = None) -> tuple[str, str]:
    """(корзина, причина) для одной строки со штампом.

    ``canon`` — сверка с ИСТОЧНИКОМ: ``{booking_request_id: канонический статус}``
    из ``docs/drf2462_canon_crosscheck.sql``. Когда канон известен, он **сильнее
    зеркала**, и вот почему это не украшение: замер 25.09 показал, что зеркало
    расходится с каноном на пяти строках из десяти и всегда в одну сторону —
    зеркало стоит на ``confirmed``, снятом при заведении строки, а канон ушёл
    дальше (``completed`` либо ``awaiting_payment``). У ``RemoteBookingProxy`` нет
    даже ``updated_at``: строка пишется один раз (DRF-2519).

    Порядок доказательств, от сильного к слабому:

    1. **канон** (если передан) — источник по ADR-0009;
    2. **подпись человека** в ``completed_by`` — сильнее отставшей копии;
    3. **зеркало** — копия, и её ``confirmed`` НЕ доказательство: он значит лишь
       «на момент заведения канон не спорил». Поэтому ``mirror_confirmed``
       попадает в ``unprovable``, а не в ``legitimate``. Прежняя редакция этого
       докстринга обещала ``legitimate`` — код так не делал ни дня (ветка была
       недостижима), и прав был код: по канону из семи таких строк ``completed``
       оказался только у трёх.
    """

    from apps.bookings.completion_evidence import mirror_evidence

    if canon:
        canon_status = canon.get(str(booking.pk))
        if canon_status == REASON_CANON_UNAVAILABLE:
            # Канон спрошен и не ответил: «не знаем» — не повод чистить и не
            # повод молчать. Зеркало здесь НЕ подставляется: оно стухает, а мы
            # уже знаем, в какую сторону.
            return (BUCKET_UNPROVABLE, REASON_CANON_UNAVAILABLE)
        if canon_status in CANON_TRUE:
            return (BUCKET_LEGITIMATE, f"canon_{canon_status}")
        if canon_status in CANON_FALSE:
            return (BUCKET_FALSE, f"canon_{canon_status}")
        if canon_status:
            # Канон знает строку, но его состояние не говорит ни «было», ни «не
            # было» (``confirmed``, незнакомое): решать нечем.
            return (BUCKET_UNPROVABLE, f"canon_{canon_status}")
        # Канона по этой строке в файле нет — падаем к слабым доказательствам,
        # и причина это назовёт.

    if booking.completed_by and booking.completed_by != SYSTEM_CLOSER:
        return (BUCKET_LEGITIMATE, "human_closer")

    may_stamp, reason = mirror_evidence(booking)
    if reason == "mirror_completed":
        return (BUCKET_LEGITIMATE, reason)
    if reason.startswith("mirror_"):
        state = reason.removeprefix("mirror_")
        if state in MIRROR_REFUSES:
            return (BUCKET_FALSE, reason)
        # ``confirmed`` и незнакомые состояния: не ложный и не законный.
        return (BUCKET_UNPROVABLE, reason)
    return (BUCKET_UNPROVABLE, reason)


#: Причина, когда канон спросили и он не ответил. Это НЕ «состояния нет» —
#: это «мы не знаем», и потому строка уходит в ``unprovable``, а не чистится.
REASON_CANON_UNAVAILABLE = "canon_unavailable"


def _read_canon_live(rows: list[Any]) -> dict[str, str]:
    """Спросить КАНОН по каждой строке: ``GET appointments/{id}/`` (DRF-2519).

    Ключ запроса — ``appointment_id`` **зеркала**: идентификатор, в отличие от
    статуса, не стухает (зеркало пишется один раз, статусные переходы в него не
    приезжают). Вызов уже есть в репозитории и уже отдаёт статус:
    :meth:`apps.integrations.ayla.booking_client.AylaBookingHTTPClient.get_appointment_version`.

    Канон недоступен или строка ему неизвестна → в словаре появляется
    :data:`REASON_CANON_UNAVAILABLE`, и такая строка НЕ становится кандидатом на
    уборку. Асимметрия та же: не снять штамп обратимо, снять не тот — нет.
    """

    from apps.booking.models import RemoteBookingProxy
    from apps.integrations.ayla.booking_client import (
        BookingAPIError,
        BookingUnavailableError,
        get_ayla_booking_client,
    )
    from apps.integrations.ayla.user_proxy import external_user_id_for

    client = get_ayla_booking_client()
    canon: dict[str, str] = {}
    for row in rows:
        if row.bot_user_id is None or row.visit_at is None:
            continue
        appointment_ids = list(
            RemoteBookingProxy.all_tenants.filter(
                tenant_id=row.tenant_id,
                bot_user_id=row.bot_user_id,
                start_at=row.visit_at,
            ).values_list("appointment_id", flat=True)[:2]
        )
        if len(appointment_ids) != 1:
            # Нет пары или их две — спрашивать канон не о чем (ключ неточен).
            continue
        try:
            record = client.get_appointment_version(
                external_user_id=external_user_id_for(row.bot_user),
                booking_id=str(appointment_ids[0]),
            )
        except (BookingAPIError, BookingUnavailableError) as exc:
            logger.warning(
                "booking.false_completion.canon_unavailable booking=%s appointment=%s error=%s",
                row.pk,
                appointment_ids[0],
                type(exc).__name__,
            )
            canon[str(row.pk)] = REASON_CANON_UNAVAILABLE
            continue
        canon[str(row.pk)] = str(record.status)
    return canon


def _read_canon(path: str) -> dict[str, str]:
    """Прочитать сверку с каноном: «<booking_request_id> <статус>» по строке.

    Пустой путь — пустой словарь, и это НЕ «канон подтвердил»: без файла решает
    зеркало, а оно стухает. Битая строка — отказ целиком: половина сверки хуже
    её отсутствия, потому что выглядит как сверка.
    """

    from pathlib import Path

    from django.core.management.base import CommandError

    if not path:
        return {}
    source = Path(path)
    if not source.exists():
        raise CommandError(f"файла сверки с каноном нет: {path}")
    canon: dict[str, str] = {}
    for number, raw in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            raise CommandError(
                f"{path}:{number}: ожидались два поля «<booking_request_id> <статус>», "
                f"получено {len(parts)}"
            )
        canon[parts[0]] = parts[1]
    if not canon:
        raise CommandError(f"{path}: ни одной строки сверки — пустой файл не сверка")
    return canon


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
            "--canon",
            action="store_true",
            help=(
                "спросить КАНОН по каждой строке (GET appointments/{id}/ по "
                "appointment_id зеркала) — предпочтительный способ: статус зеркала "
                "стухает, идентификатор нет (DRF-2519)"
            ),
        )
        parser.add_argument(
            "--canon-file",
            default="",
            help=(
                "файл сверки с каноном: строки «<booking_request_id> <канонический "
                "статус>». Канон сильнее зеркала; без файла считается по зеркалу, и "
                "тогда неоплаченные визиты в ложные НЕ попадут (DRF-2519)"
            ),
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
        canon = _read_canon(options["canon_file"])
        rows = BookingRequest.all_tenants.filter(completed_at__isnull=False)
        if slug:
            rows = rows.filter(tenant__slug=slug)
        rows = rows.select_related("tenant", "bot_user").order_by("visit_at")

        stamped = list(rows)
        if options["canon"]:
            # Живой канон сильнее файла: файл — снимок, сделанный когда-то.
            canon = {**canon, **_read_canon_live(stamped)}
        buckets: Counter[str] = Counter()
        reasons: Counter[str] = Counter()
        false_rows: list[Any] = []
        for booking in stamped:
            bucket, reason = _classify(booking, canon)
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
        w("режим:   " + ("ЗАПИСЬ (--apply)" if options["apply"] else "только чтение"))
        # Чем решалось — сильным доказательством или слабым. Без этой строки
        # «ложных 3» и «ложных 5» выглядят одним числом о одном предмете.
        if canon:
            source = "живой опрос" if options["canon"] else "сверка из файла"
            unknown = sum(1 for v in canon.values() if v == REASON_CANON_UNAVAILABLE)
            w(
                f"решает:  КАНОН ({source}, строк {len(canon)}"
                + (f", не ответил по {unknown}" if unknown else "")
                + ") · зеркало вторично"
            )
        else:
            w(
                "решает:  ЗЕРКАЛО (файла сверки нет) — ВНИМАНИЕ: зеркало стухает "
                "(DRF-2519), неоплаченные визиты в ложные не попадут"
            )
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
        cleared, skipped = self._clear(false_rows, w, canon)
        w("")
        w(f"снято штампов: {cleared} · пропущено (строка изменилась): {skipped}")
        w(
            "производные показатели (счётчик визитов мастера, «последняя услуга») "
            "пересчитываются из источника сами: они читают completed_at запросом, "
            "а не хранят копию."
        )

    @staticmethod
    def _clear(
        false_rows: list[Any], w: Any, canon: dict[str, str] | None = None
    ) -> tuple[int, int]:
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
                bucket, reason = _classify(fresh, canon)
                if bucket != BUCKET_FALSE:
                    w(f"  ПРОПУСК {fresh.pk}: стало {bucket}:{reason}")
                    skipped += 1
                    continue
                if not reason.startswith("canon_"):
                    # Снимаем ТОЛЬКО по свидетельству источника. Зеркало сюда не
                    # допускается даже когда говорит против штампа: его
                    # ``confirmed`` бот пишет себе сам константой
                    # (``skills/booking/tools.py::_upsert_remote_booking_proxy``), а
                    # замер 25.09 показал шесть визитов, завершённых каноном, чьи
                    # письма мертвы — наивное правило «зеркало не completed →
                    # ложный» сняло бы до шести ЗАКОННЫХ штампов.
                    w(
                        f"  ПРОПУСК {fresh.pk}: свидетельство не от канона ({reason}) — "
                        "нужен --canon либо --canon-file"
                    )
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
