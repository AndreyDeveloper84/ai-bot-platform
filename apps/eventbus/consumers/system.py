"""Потребитель системных событий — ``system.module.health.degraded`` (DRF-2196).

Каталог публикует сигнал бюджета распознавания фото (80 / 100 % общего
суточного потолка) через outbox (#524 в beautygo_backend). Здесь — тонкий
адаптер: он берёт числа из события и отдаёт их ядру
:func:`apps.observability.scan_budget_alert.signal_budget`, которое решает,
звучать ли странице в MAX, и держит дедуп по суткам и порогу.

# Почему адаптер не доверяет ``severity`` каталога

Каталог присылает ``severity`` (``warning`` / ``error``), но ядро решает
уровень само по ``used`` / ``limit``. Это не недоверие, а одна точка решения
в боте: дедуп, уровень и «звучит старший порог» живут в ядре, и два решателя
на одной стороне разошлись бы при первой правке. ``severity`` каталога
остаётся в событии для наблюдаемости.

# Чужой модуль — принять, не звать

Имя события общее для всех модулей; сегодня понятны два —
``nutrition.food_scan`` и ``appointments.outbox`` (dead-letter outbox
каталога, DRF-2306, ядро :mod:`apps.observability.outbox_dead_alert`). Мёртвое письмо на каждый новый модуль было бы
шумом, а страница по данным, которых ядро не понимает, — ложью. Поэтому
чужой модуль принимается, пишется в лог и ничего не зовёт.

# Идемпотентность

Повторную доставку отсекает диспетчер по ``event_id`` (``IngestDedupe``,
ADR-0009 правило 7) — до этого обработчика она не доходит. Второй рубеж —
дедуп самого ядра по суткам и порогу.

# Недоставленная страница — отказ принять событие

Каталог шлёт событие ОДИН раз на сутки и порог (сам дедуплицирует). Если
страница не ушла (сток MAX отвалился ровно в момент 100 %), принять событие
значило бы потерять страницу до полуночи UTC — ровно то состояние, ради
которого лист и заведён. Поэтому на ``not_delivered`` обработчик бросает:
диспетчер откатывает строку дедупа в той же транзакции, вью отвечает 500,
outbox каталога повторяет доставку с отступом, а после порога — dead-letter,
видимый оператору.

Где стоков нет вовсе, это даст ограниченную серию 500-х и мёртвое письмо —
и это правильно: «страницу некуда послать» — неисправность, которую должно
быть видно, а не тишина с зелёным ответом.
"""

from __future__ import annotations

import datetime as dt
import logging

from apps.eventbus.ingest_dispatcher import register
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.eventbus.ingest_tenancy import assert_envelope_tenant_authorized
from apps.observability.outbox_dead_alert import signal_outbox_dead
from apps.observability.scan_budget_alert import signal_budget


class PageNotDeliveredError(RuntimeError):
    """Страница не ушла ни в один сток — событие не принято, пусть повторят."""


logger = logging.getLogger(__name__)

EVENT_NAME = "system.module.health.degraded"

#: Модули, которые этот потребитель понимает. Остальные — принять и молчать.
_FOOD_SCAN_MODULE = "nutrition.food_scan"
#: DRF-2306 — dead-letter outbox каталога (контракт §6.4).
_OUTBOX_MODULE = "appointments.outbox"

#: Поля метрики outbox — ровно те, что принимает ядро страницы.
_OUTBOX_FIELDS = ("topic", "failure", "http_status", "count", "hour", "reason")


def handle_system_health_degraded(envelope: IngestEnvelope) -> None:
    """Сигнал здоровья модуля → ядро страницы операторам."""
    # Третий путь авторизации (системное событие без субъекта) — тот же
    # вызов, что у прочих обработчиков: маршрут по имени решается внутри.
    assert_envelope_tenant_authorized(envelope)

    data = envelope.data
    module = data.get("module_name")
    if module == _OUTBOX_MODULE:
        _handle_outbox_dead(envelope)
        return
    if module != _FOOD_SCAN_MODULE:
        logger.info(
            "eventbus.system.unknown_module event_id=%s module=%r — принято, не звать",
            envelope.event_id,
            str(module)[:64],
        )
        return

    metric = data.get("metric")
    if not isinstance(metric, dict):
        # Ядро переварит мусор и промолчит, но это стоит строки в логе:
        # каталог обещал объект, и нарушение контракта должно быть видно.
        logger.warning(
            "eventbus.system.metric_not_object event_id=%s type=%s",
            envelope.event_id,
            type(metric).__name__,
        )
        return

    day = _iso_day_or_none(metric.get("day"))
    if day is None:
        # `day` уходит в ключ дедупа ядра И в текст страницы операторам. Не
        # ISO-дата — не звать: иначе каждое уникальное значение (перевод
        # строки, десять килобайт мусора) давало бы свой ключ дедупа, то
        # есть новую страницу, и попадало бы в сообщение как есть.
        logger.warning("eventbus.system.day_invalid event_id=%s", envelope.event_id)
        return

    cost = metric.get("cost_usd")
    outcome = signal_budget(
        used=metric.get("used"),
        limit=metric.get("limit"),
        day=day,
        # Строка суммы или «не посчитано»; число и объект — не строка
        # суммы, и в текст оператору как есть им идти незачем.
        cost_usd=cost if isinstance(cost, str) else None,
    )
    if outcome == "not_delivered":
        raise PageNotDeliveredError(
            f"scan budget page not delivered event_id={envelope.event_id} day={day}"
        )


def _handle_outbox_dead(envelope: IngestEnvelope) -> None:
    """DRF-2306 — dead-letter outbox каталога → страница операторам.

    Поля проверяет ядро (:func:`signal_outbox_dead`); здесь только форма
    метрики и отказ принять событие, если страница не ушла, — как у бюджета.
    """
    metric = envelope.data.get("metric")
    if not isinstance(metric, dict):
        logger.warning(
            "eventbus.system.outbox_metric_not_object event_id=%s type=%s",
            envelope.event_id,
            type(metric).__name__,
        )
        return
    outcome = signal_outbox_dead(**{field: metric.get(field) for field in _OUTBOX_FIELDS})
    if outcome == "not_delivered":
        raise PageNotDeliveredError(
            f"outbox dead-letter page not delivered event_id={envelope.event_id}"
        )


def _iso_day_or_none(value: object) -> str | None:
    """Сутки строкой ``YYYY-MM-DD`` — или ничего."""
    if not isinstance(value, str):
        return None
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def register_system_handlers() -> None:
    register(EVENT_NAME, 1, handle_system_health_degraded)


__all__ = ["PageNotDeliveredError", "handle_system_health_degraded", "register_system_handlers"]
