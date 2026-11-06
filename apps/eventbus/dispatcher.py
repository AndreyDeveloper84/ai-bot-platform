"""Outbox dispatcher — Q-EV-IMPL3 Celery beat + Phase 2.2 DLQ.

Periodically claims pending DomainEvent rows and hands the envelopes
to registered subscribers. Subscribers are pluggable (Phase 2.2 PR-B —
not yet); Phase 2.1 ships with a hard-coded NoopSubscriber so the
polling + claim mechanics are exercised end-to-end.

### Concurrency model

``SELECT FOR UPDATE SKIP LOCKED`` so multiple workers can run the
beat task concurrently without lock contention. Each worker grabs a
disjoint batch.

### Failure model

Subscriber raises → ``dispatch_attempts++`` + ``last_error`` set, row
left ``is_dispatched=False`` for retry on next tick.

### Dead-letter (Phase 2.2 — supersedes §18.5 deviation)

When ``dispatch_attempts`` crosses ``MAX_ATTEMPTS``, the dispatcher
sets ``dead_lettered_at = now()`` on the row. The claim query
explicitly excludes rows where ``dead_lettered_at IS NOT NULL`` —
cleaner semantics than the Phase 2.1 «forever-pending» workaround.

Ops surface: admin shows dead-letter rows in their own filter +
provides a bulk «Replay» action that resets ``dead_lettered_at`` and
``dispatch_attempts`` so the next tick re-claims them.

Alerting: when the dispatcher quarantines one or more rows in a
single run, it emits ``system.module.health.degraded`` (tenant-less,
per taxonomy §8) with ``metric=dlq_count_in_run``. Ops paging is
wired to that event by the observability stack.
"""

from __future__ import annotations

import logging
from importlib import import_module
from typing import Any, Protocol, runtime_checkable

from celery import shared_task  # type: ignore[import-untyped]
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.eventbus.envelope import Envelope
from apps.eventbus.models import DomainEvent

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3  # taxonomy §5
BATCH_SIZE = 100


@runtime_checkable
class Subscriber(Protocol):
    """Sink for outbound domain envelopes."""

    def handle(self, envelope: Envelope) -> None:  # pragma: no cover - protocol
        ...


class NoopSubscriber:
    """Phase 2.1 default — never makes a network call.

    Exists so the dispatcher has something to call against in tests
    and dev, and so settings can override with real subscribers once
    they exist (PR-B — pluggable registry).
    """

    def handle(self, envelope: Envelope) -> None:
        logger.debug("eventbus.noop_subscriber.handled event=%s", envelope.event_name)


_REGISTRY_CACHE: list[Subscriber] | None = None


def _resolve(path: str) -> Subscriber:
    """Resolve a dotted path like ``apps.eventbus.dispatcher.NoopSubscriber`` to an instance.

    Mirrors :func:`apps.events.fanout._resolve` (kept independent to
    avoid cross-bus coupling).
    """

    module_path, _, class_name = path.rpartition(".")
    module = import_module(module_path)
    cls = getattr(module, class_name)
    return cls()


def _subscribers() -> list[Subscriber]:
    """Return configured subscribers from ``settings.DOMAIN_EVENT_SUBSCRIBERS``.

    Default = ``["apps.eventbus.dispatcher.NoopSubscriber"]`` (Phase 2.1
    behavior preserved when settings is silent). Cached after first
    resolution; tests + ``setting_changed`` reset via
    :func:`reset_registry_cache`.

    Why a list of dotted paths instead of class objects in settings:
    - Settings files are loaded before app modules — keeping class
      references textual avoids import-order traps.
    - Per-environment overrides work via environment variable parsing
      in settings/base.py (same idiom as ``EVENT_FANOUTS``).
    """

    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is None:
        paths: list[str] = list(
            getattr(
                settings,
                "DOMAIN_EVENT_SUBSCRIBERS",
                ["apps.eventbus.dispatcher.NoopSubscriber"],
            )
        )
        _REGISTRY_CACHE = [_resolve(p) for p in paths]
    return _REGISTRY_CACHE


def reset_registry_cache() -> None:
    """Drop the cached subscriber list. Tests + Django ``setting_changed`` use this."""

    global _REGISTRY_CACHE
    _REGISTRY_CACHE = None


@shared_task(name="apps.eventbus.dispatch_pending_events")
def dispatch_pending_events(batch_size: int = BATCH_SIZE) -> dict[str, int]:
    """Claim and dispatch the oldest pending domain events.

    Returns counters: {claimed, dispatched, failed, dead_letter}.
    ``dead_letter`` counts rows that crossed the threshold in this run
    (NOT cumulative across all dead-letter rows in the table).

    Idempotency: each row is dispatched at-least-once. Subscribers must
    de-duplicate by ``event_id`` (taxonomy §4 — subscriber contract).
    """

    counters = {"claimed": 0, "dispatched": 0, "failed": 0, "dead_letter": 0}
    subs = _subscribers()

    with transaction.atomic():
        pending = list(
            DomainEvent.objects.select_for_update(skip_locked=True)
            .filter(is_dispatched=False, dead_lettered_at__isnull=True)
            .order_by("event_id")[:batch_size]
        )
        counters["claimed"] = len(pending)

        for row in pending:
            envelope = Envelope.from_row(row)
            ok = True
            error_msg = ""
            for sub in subs:
                try:
                    sub.handle(envelope)
                except Exception as exc:  # noqa: BLE001 — one sub never breaks others
                    ok = False
                    error_msg = f"{type(sub).__name__}: {exc}"[:500]
                    logger.exception(
                        "eventbus.dispatch.subscriber_failed event=%s sub=%s",
                        row.event_id,
                        type(sub).__name__,
                    )

            if ok:
                row.is_dispatched = True
                row.dispatched_at = timezone.now()
                row.save(update_fields=["is_dispatched", "dispatched_at"])
                counters["dispatched"] += 1
            else:
                row.dispatch_attempts += 1
                row.last_error = error_msg
                update_fields = ["dispatch_attempts", "last_error"]
                if row.dispatch_attempts >= MAX_ATTEMPTS:
                    row.dead_lettered_at = timezone.now()
                    update_fields.append("dead_lettered_at")
                    counters["dead_letter"] += 1
                    logger.error(
                        "eventbus.dispatch.dead_letter event=%s attempts=%s",
                        row.event_id,
                        row.dispatch_attempts,
                    )
                row.save(update_fields=update_fields)
                counters["failed"] += 1

    # Threshold alert — fire AFTER the transaction commits so subscribers
    # see the dead-letter rows when they react to the alert.
    if counters["dead_letter"] > 0:
        _emit_dlq_alert(counters["dead_letter"])

    return counters


def replay_dead_letter(event_ids: list[str]) -> int:
    """Reset DLQ state on the given event_ids so the dispatcher re-claims them.

    Args:
      event_ids: ULID strings of rows to replay.

    Returns:
      Number of rows actually reset.

    Idempotent: rows that are not dead-letter (or do not exist) are
    silently skipped — the SQL filter handles both cases.
    """

    return DomainEvent.objects.filter(
        event_id__in=event_ids,
        dead_lettered_at__isnull=False,
    ).update(
        dead_lettered_at=None,
        dispatch_attempts=0,
        last_error="",
    )


def _emit_dlq_alert(dlq_count: int) -> None:
    """Emit ``system.module.health.degraded`` for dispatcher quarantine.

    Lazy import avoids circular import — emit() imports the dispatcher's
    models. We don't want models -> dispatcher -> emit -> models at
    module load.
    """

    from apps.eventbus import services, vocabulary as V

    try:
        services.emit(
            V.SYSTEM_MODULE_HEALTH_DEGRADED,
            {
                "module_name": "eventbus.dispatcher",
                "severity": "warning",
                "metric": f"dlq_count_in_run={dlq_count}",
            },
            actor_type="system",
        )
    except Exception:  # noqa: BLE001 — alert never breaks dispatcher
        logger.exception("eventbus.dispatch.alert_emit_failed dlq_count=%s", dlq_count)


# ---------------------------------------------------------------------------
# DRF-1616, блокер B-7 (наблюдаемость). Beat-обёртка над dispatch_pending_events.
# ---------------------------------------------------------------------------
#
# До этого диспетчер ящика НЕ ЗВАЛ НИКТО: ни расписание, ни сигнал — только
# тесты. Тревога о застрявшем ящике (`_emit_dlq_alert`) живёт внутри него и
# потому молчала (правила исполнителя §23: «тревога внутри предмета — не
# тревога»). Расписание в config/settings/base.py указывает СЮДА, а не на
# `dispatch_pending_events` напрямую, по двум причинам:
#
#   * прямой вызов остаётся как был — операторский replay и тесты не должны
#     получать «skipped» от рубильника, который заведён для beat;
#   * рубильник и DRY_RUN — по образцу nutrition_proactive (DRF-1285): оба
#     закрыты по умолчанию, и запись в расписании безопасна до решения
#     владельца её включить. DRY_RUN не берёт строки под select_for_update и
#     ничего не помечает — считает и говорит, сколько бы взял.


def _dispatch_beat_enabled() -> bool:
    return bool(getattr(settings, "EVENTBUS_DISPATCH_BEAT_ENABLED", False))


def _dispatch_beat_dry_run() -> bool:
    return bool(getattr(settings, "EVENTBUS_DISPATCH_BEAT_DRY_RUN", True))


def registry_is_noop_only() -> bool:
    """Нет ни одного настоящего подписчика: реестр пуст или в нём только Noop.

    DRF-2434. Диспетчер считает строку доставленной, если ни один подписчик
    не бросил исключения. У `NoopSubscriber` исключений не бывает, у пустого
    реестра — тем более, поэтому живой прогон при таком реестре помечает
    `is_dispatched=True` всё, до чего дотянется, не доставив ничего никому.
    Обратного хода нет: `replay_dead_letter` отправленных не касается.
    """

    return all(isinstance(sub, NoopSubscriber) for sub in _subscribers())


#: События, которые нельзя пометить доставленными, пока в реестре нет
#: подписчика, который их ДОСТАВЛЯЕТ потребителю (DRF-2776).
#:
#: Решение владельца D от 05.10: смену согласия получают системы, чьё
#: поведение от неё зависит, — «одного журнала мало». Диспетчер же считает
#: строку доставленной, если ни один подписчик не бросил исключения; с
#: реестром «журнал + лояльность» смена согласия ушла бы в журнал и стала
#: «доставленной», так и не дойдя до каталога, — а владелец решил доставить
#: накопленное задним числом, и пометка закрыла бы этот путь навсегда.
#:
#: Набор узкий намеренно. События без единого потребителя в системе
#: (``booking.created``, ``booking.attribution.assigned``) сюда НЕ входят:
#: владелец требует, чтобы событие без потребителя не задерживало
#: независимые, и требование «доставщик для каждого имени» держало бы ящик
#: закрытым вечно.
DELIVERY_REQUIRED: frozenset[str] = frozenset({"customer.consent.changed"})


def undelivered_required_names() -> list[str]:
    """Имена из :data:`DELIVERY_REQUIRED`, что лежат в ящике без доставщика.

    Доставщик — подписчик реестра, назвавший имя в своём атрибуте
    ``delivers``. Журнал (:class:`~apps.eventbus.subscribers.AuditSubscriber`)
    его не объявляет и доставщиком не считается.
    """

    delivered: set[str] = set()
    for sub in _subscribers():
        delivered |= set(getattr(sub, "delivers", ()) or ())
    missing = DELIVERY_REQUIRED - delivered
    if not missing:
        return []
    pending = (
        DomainEvent.objects.filter(
            is_dispatched=False, dead_lettered_at__isnull=True, event_name__in=missing
        )
        .values_list("event_name", flat=True)
        .distinct()
    )
    return sorted(set(pending))


@shared_task(name="apps.eventbus.dispatch_pending_events_beat")
def dispatch_pending_events_beat() -> dict[str, Any]:
    """Расписание → сюда → `dispatch_pending_events`, если открыто.

    Пять исходов, и все различимы по ключу `mode` в ответе — чтобы
    «ничего не отправлено» никогда не читалось одинаково для «выключено»,
    «сухой прогон», «отказ» и «отправлять было нечего».

    `refused_undelivered` (DRF-2776): настоящие подписчики есть, но в ящике
    лежит событие из :data:`DELIVERY_REQUIRED`, а доставщика для него в
    реестре нет — живой прогон пометил бы его «доставленным» в журнал.

    `refused_noop_only` (DRF-2434): рубильник открыт, сухой прогон снят, а
    настоящих подписчиков нет. Порядок починки «счётчик → подписчик →
    расписание» до этого держался только на прозе — подсказке W012 и листе;
    одно открытие рубильника раньше подписчика пометило бы накопленное
    доставленным никому. Теперь его держит код: обёртка считает и отказывает.
    Прямой операторский вызов `dispatch_pending_events` не тронут.
    """

    if not _dispatch_beat_enabled():
        return {"mode": "disabled", "pending": None}
    if _dispatch_beat_dry_run():
        pending = DomainEvent.objects.filter(
            is_dispatched=False, dead_lettered_at__isnull=True
        ).count()
        logger.info("eventbus.dispatch.beat.dry_run pending=%d — ничего не помечено", pending)
        return {"mode": "dry_run", "pending": pending}
    if registry_is_noop_only():
        pending = DomainEvent.objects.filter(
            is_dispatched=False, dead_lettered_at__isnull=True
        ).count()
        logger.warning(
            "eventbus.dispatch.beat.refused_noop_only pending=%d — живой режим открыт, "
            "но в DOMAIN_EVENT_SUBSCRIBERS нет настоящего подписчика; ничего не помечено",
            pending,
        )
        return {"mode": "refused_noop_only", "pending": pending}
    undelivered = undelivered_required_names()
    if undelivered:
        pending = DomainEvent.objects.filter(
            is_dispatched=False, dead_lettered_at__isnull=True
        ).count()
        logger.warning(
            "eventbus.dispatch.beat.refused_undelivered pending=%d names=%s — "
            "в реестре нет доставщика для событий, которые журналом не закрываются; "
            "ничего не помечено",
            pending,
            ",".join(undelivered),
        )
        return {"mode": "refused_undelivered", "pending": pending, "names": undelivered}
    counters = dispatch_pending_events()
    return {"mode": "live", **counters}
