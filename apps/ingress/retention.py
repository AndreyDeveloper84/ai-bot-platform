"""Срок хранения ``WebhookJournal`` и стирание по просьбе человека (DRF-2242).

``raw_payload`` — полный вебхук MAX: текст, имя, иногда телефон. До этого
листа он жил бессрочно, а «забудь всё» и удаление аккаунта его не знали.
Программно его не читает никто (показ в админке и объявление чувствительным в
``adminconsole.client_scope``) — поэтому срок выбран по смыслу, а не по
читателю.

# Два срока

* **Тело** — ``INGRESS_RAW_RETENTION_HOURS`` (W), тот же, что у потоков
  ``ingress:*`` (DRF-2220, #1952). Журнал держит ту же сырую копию, что и
  запись потока: короткий срок одной из двух копий ничего бы не защищал.
* **Строка без тела** — ``WEBHOOK_JOURNAL_ROW_RETENTION_DAYS`` (90). На ней
  держится дедуп повторов MAX по ``(channel, external_event_id)`` и служебный
  след (trace, tenant, время). 90 дней — решение главного окна по аналогии с
  ярусом аудита ``ArchivedMessage`` (стык с ``AuditLog`` по trace_id при
  разборе инцидента); владельцу вынесено, меняется одним числом.

# Строка без тела — всё ещё ПДн, псевдонимизированные

``trace_id`` ведёт к ``Message.trace_id`` → диалог → ``BotUser.channel_user_id``
(его ``privacy._PII_FIELDS`` не стирает), а ``external_event_id`` —
``callback_id`` / ``update_id`` / ``mid:seq`` — держатель токена бота может
запросить у MAX. Поэтому при «забудь всё» и удалении аккаунта у строк
человека связь рвётся сразу: ``trace_id`` → ``""``, event id → односторонний
хеш (:func:`erased_event_id`). Хеш сохраняет дедуп — ``record_webhook`` узнаёт
повтор стёртого события по нему, — а исходный id из него не восстановить.

# Кромка и накопленное

Периодическая чистка (:func:`sweep_expired`) берёт только строки, которые
перешли срок недавно — в окне :data:`SWEEP_LOOKBACK`. Всё, что накоплено до
выкладки, трогает только команда ``purge_webhook_journal``: сухой прогон по
умолчанию, ``--apply`` — по слову владельца. Иначе первый же запуск свипа
необратимо стёр бы историю, о которой владелец не решал.

# Ноль обхода обязан называть свой охват (DRF-2426)

Замер 24.09: часовой обход отдавал ``{'payloads': 0, 'rows': 0}`` — правду о
кромке — а за его окном лежали 581 тело старше объявленного срока. Каждый час
этот честный ноль подтверждал несуществующую чистоту: величина верна, предмет
у неё другой. Поэтому :func:`sweep_expired` считает и печатает ТРИ числа:

* ``payloads`` / ``rows`` — что обход взял, всегда внутри своей кромки;
* ``in_window_*`` — сколько строк лежало в его корзине ДО действия: ноль здесь
  значит «брать было нечего», а непустая корзина при нулевом действии — дефект,
  который иначе выглядел бы так же;
* ``beyond_window_*`` — что лежит ЗА окном и обходу структурно недоступно.
  Он это только измеряет и никогда не трогает: накопленное — по слову владельца
  и только командой.

Узел: **пока за окном есть строки, обход не отчитывается чистотой.** Строка
журнала пишется КАЖДЫЙ раз (прежняя писалась только при непустом действии, и
ноль уходил в тишину), и она называет либо ``backlog`` за окном, либо пустой
охват — ноль на пустом скане ничего не доказывает и так и подписан.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.ingress.models import WebhookJournal

logger = logging.getLogger(__name__)

#: Как далеко назад от срока смотрит периодическая чистка. Неделя покрывает
#: пропущенные запуски (простой воркера, выходные) с запасом; старше — только
#: команда (см. докстринг модуля).
SWEEP_LOOKBACK = timedelta(days=7)

#: Префикс хешированного event id — отличает стёртую строку от живой.
ERASED_PREFIX = "erased:"


def payload_hours() -> int:
    return int(getattr(settings, "INGRESS_RAW_RETENTION_HOURS", 72))


def row_days() -> int:
    return int(getattr(settings, "WEBHOOK_JOURNAL_ROW_RETENTION_DAYS", 90))


def erased_event_id(external_event_id: str) -> str:
    """Односторонний хеш event id стёртой строки (сохраняет дедуп)."""
    digest = hashlib.sha256(str(external_event_id).encode("utf-8")).hexdigest()
    return f"{ERASED_PREFIX}{digest}"


@dataclass(frozen=True)
class JournalPurge:
    """Сколько тел обнулено и строк удалено. Счётчики, никогда не значения.

    ``in_window_*`` и ``beyond_window_*`` — охват этих счётчиков (DRF-2426):
    что лежало в корзине обхода до действия и что лежит за его окном. Без них
    ноль в ``payloads`` / ``rows`` неотличим от чистоты, которой нет.
    """

    payloads: int = 0
    rows: int = 0
    in_window_payloads: int = 0
    in_window_rows: int = 0
    beyond_window_payloads: int = 0
    beyond_window_rows: int = 0
    #: Сколько строк было в журнале вообще — охват скана. Ноль здесь значит
    #: «мерить было не на чем», и такой ноль не выдаётся за чистоту.
    scanned: int = 0

    @property
    def beyond_window(self) -> int:
        """Сколько всего осталось за окном — то, о чём обход не вправе молчать."""

        return self.beyond_window_payloads + self.beyond_window_rows

    @property
    def took_its_basket(self) -> bool:
        """Взято ровно то, что лежало в корзине до действия."""

        return self.payloads == self.in_window_payloads and self.rows == self.in_window_rows

    @property
    def verdict(self) -> str:
        """Одно слово о состоянии журнала — не о старательности обхода.

        * ``backlog_outside_window`` — за окном осталось просроченное: чистоты
          нет, чем бы ни закончилась кромка;
        * ``scope_empty`` — журнал пуст: ноль верен и ничего не доказывает;
        * ``incomplete`` — корзина была непуста, а взято меньше: дефект, который
          без счёта корзины выглядел бы как обычный ноль;
        * ``clean`` — просроченного не осталось, и это сказано на непустом охвате.
        """

        if self.beyond_window:
            return "backlog_outside_window"
        if not self.took_its_basket:
            return "incomplete"
        if self.scanned == 0:
            return "scope_empty"
        return "clean"

    @property
    def clean(self) -> bool:
        """Правда ли «чисто» — только при непустом охвате (DRF-2426)."""

        return self.verdict == "clean"


def _with_body() -> Q:
    return ~Q(raw_payload={})


def beyond_window(now: datetime | None = None) -> tuple[int, int]:
    """Сколько тел и строк лежит ЗА окном обхода — измерение, не чистка.

    Тот же срок, что у :func:`sweep_expired`, минус :data:`SWEEP_LOOKBACK`:
    ровно то, до чего кромка структурно не достаёт. Тела считаются только у
    строк, которые сами ещё не просрочены (иначе один и тот же остаток попал
    бы в оба числа — как в ``backlog``).
    """

    now = now or timezone.now()
    body_edge = now - timedelta(hours=payload_hours()) - SWEEP_LOOKBACK
    row_edge = now - timedelta(days=row_days()) - SWEEP_LOOKBACK
    bodies = WebhookJournal.objects.filter(
        _with_body(),
        received_at__lt=body_edge,
        received_at__gte=now - timedelta(days=row_days()),
    ).count()
    rows = WebhookJournal.objects.filter(received_at__lt=row_edge).count()
    return bodies, rows


def sweep_expired(now: datetime | None = None) -> JournalPurge:
    """Периодическая чистка по кромке: тела старше W, строки старше срока.

    Только то, что перешло срок в последние :data:`SWEEP_LOOKBACK` —
    накопленное раньше остаётся команде.

    Отчитывается тремя числами (DRF-2426): взятое, лежавшее в корзине до
    действия и оставшееся за окном. Журнальная строка пишется всегда: ноль без
    названного охвата — не «чисто», а неизвестность.
    """

    now = now or timezone.now()
    body_cut = now - timedelta(hours=payload_hours())
    row_cut = now - timedelta(days=row_days())
    body_basket = WebhookJournal.objects.filter(
        _with_body(), received_at__lt=body_cut, received_at__gte=body_cut - SWEEP_LOOKBACK
    )
    row_basket = WebhookJournal.objects.filter(
        received_at__lt=row_cut, received_at__gte=row_cut - SWEEP_LOOKBACK
    )
    # Корзина считается ДО действия: после ``update`` / ``delete`` те же
    # запросы вернут ноль, и «брать было нечего» стало бы неотличимо от
    # «взяли всё» — ровно та подмена предмета, что и была дефектом.
    in_window_payloads = body_basket.count()
    in_window_rows = row_basket.count()
    payloads = body_basket.update(raw_payload={})
    rows, _ = row_basket.delete()
    beyond_payloads, beyond_rows = beyond_window(now)
    result = JournalPurge(
        payloads=payloads,
        rows=rows,
        in_window_payloads=in_window_payloads,
        in_window_rows=in_window_rows,
        beyond_window_payloads=beyond_payloads,
        beyond_window_rows=beyond_rows,
        # После действия: охват, на котором сказано «чисто». Пустой журнал
        # честнее назвать пустым, чем чистым.
        scanned=WebhookJournal.objects.count(),
    )
    # Всегда одна строка, и её последнее слово — про охват, а не про действие.
    logger.info(
        "ingress.retention.swept payloads=%d rows=%d in_window=%d/%d "
        "beyond_window=%d/%d scanned=%d verdict=%s",
        result.payloads,
        result.rows,
        result.in_window_payloads,
        result.in_window_rows,
        result.beyond_window_payloads,
        result.beyond_window_rows,
        result.scanned,
        result.verdict,
    )
    if result.beyond_window:
        # Накопленное за окном обход не трогает — оно ждёт слова владельца и
        # команды ``purge_webhook_journal``. Но молчать о нём он не вправе.
        logger.warning(
            "ingress.retention.backlog_outside_window payloads=%d rows=%d "
            "window_days=%d — not swept by design, purge_webhook_journal decides",
            result.beyond_window_payloads,
            result.beyond_window_rows,
            SWEEP_LOOKBACK.days,
        )
    return result


def backlog(now: datetime | None = None) -> tuple[Any, Any]:
    """Накопленное старше срока — без окна кромки. Для команды."""

    now = now or timezone.now()
    row_cut = now - timedelta(days=row_days())
    # Строки старше срока строки удаляются целиком — их тела не считаются
    # отдельно «обнулёнными», иначе счёт сухого прогона двоится.
    bodies = WebhookJournal.objects.filter(
        _with_body(),
        received_at__lt=now - timedelta(hours=payload_hours()),
        received_at__gte=row_cut,
    )
    rows = WebhookJournal.objects.filter(received_at__lt=row_cut)
    return bodies, rows


def _sender_filter(channel_user_ids: list[str]) -> Q:
    """Предфильтр в базе по трём местам, где MAX кладёт отправителя.

    Окончательное решение — :func:`apps.channels.max.parser.parse_max_webhook`,
    тот же разбор, что у потоков (#1952); предфильтр только сужает выборку.
    """

    values: list[Any] = []
    for cid in channel_user_ids:
        values.append(cid)
        if cid.isdigit():
            values.append(int(cid))
    q = Q()
    for value in values:
        q |= (
            Q(raw_payload__message__sender__user_id=value)
            | Q(raw_payload__callback__user__user_id=value)
            | Q(raw_payload__user__user_id=value)
        )
    return q


def _sender_of(payload: dict[str, Any]) -> str | None:
    from apps.channels.max.parser import parse_max_webhook

    try:
        return str(parse_max_webhook(payload).channel_user_id) or None
    except Exception:  # noqa: BLE001 — нечитаемое тело: не автор никого
        return None


def _sever(row: WebhookJournal) -> None:
    WebhookJournal.objects.filter(pk=row.pk).update(
        raw_payload={},
        trace_id="",
        external_event_id=erased_event_id(row.external_event_id),
    )


def erase_person_rows(
    channel_user_ids: Iterable[str],
    *,
    trace_ids: Iterable[str] = (),
    through: datetime,
) -> int:
    """«Забудь всё» / удаление аккаунта: строки человека до ``through``.

    Тело → ``{}``, ``trace_id`` → ``""``, event id → :func:`erased_event_id`.
    Строка остаётся: дедуп и обезличенный служебный след.

    Два пути, потому что у строки два способа указать на человека:

    * **по телу** — отправитель в ``raw_payload`` (пока тело живо, ≤ W);
    * **по трассе** — ``trace_ids`` его сообщений: после W тела нет, а
      ``trace_id`` связывает строку с ним все :func:`row_days` дней.

    Returns:
      число изменённых строк.
    """

    live = WebhookJournal.objects.exclude(external_event_id__startswith=ERASED_PREFIX)
    changed = 0

    wanted = sorted({str(i) for i in channel_user_ids if i})
    if wanted:
        by_body = live.filter(_sender_filter(wanted), channel="max", received_at__lte=through)
        for row in by_body.iterator(chunk_size=500):
            if _sender_of(row.raw_payload or {}) in wanted:
                _sever(row)
                changed += 1

    traces = sorted({str(t) for t in trace_ids if t})
    for start in range(0, len(traces), 500):
        by_trace = live.filter(trace_id__in=traces[start : start + 500], received_at__lte=through)
        for row in by_trace.iterator(chunk_size=500):
            _sever(row)
            changed += 1
    return changed


__all__ = [
    "ERASED_PREFIX",
    "JournalPurge",
    "SWEEP_LOOKBACK",
    "backlog",
    "beyond_window",
    "erase_person_rows",
    "erased_event_id",
    "payload_hours",
    "row_days",
    "sweep_expired",
]
