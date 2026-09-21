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

Имя события общее для всех модулей; сегодня понятен один —
``nutrition.food_scan``. Мёртвое письмо на каждый новый модуль было бы
шумом, а страница по данным, которых ядро не понимает, — ложью. Поэтому
чужой модуль принимается, пишется в лог и ничего не зовёт.

# Идемпотентность

Повторную доставку отсекает диспетчер по ``event_id`` (``IngestDedupe``,
ADR-0009 правило 7) — до этого обработчика она не доходит. Второй рубеж —
дедуп самого ядра по суткам и порогу.
"""

from __future__ import annotations

import logging

from apps.eventbus.ingest_dispatcher import register
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.eventbus.ingest_tenancy import assert_envelope_tenant_authorized
from apps.observability.scan_budget_alert import signal_budget

logger = logging.getLogger(__name__)

EVENT_NAME = "system.module.health.degraded"

#: Модули, которые этот потребитель понимает. Остальные — принять и молчать.
_FOOD_SCAN_MODULE = "nutrition.food_scan"


def handle_system_health_degraded(envelope: IngestEnvelope) -> None:
    """Сигнал здоровья модуля → ядро страницы операторам."""
    # Третий путь авторизации (системное событие без субъекта) — тот же
    # вызов, что у прочих обработчиков: маршрут по имени решается внутри.
    assert_envelope_tenant_authorized(envelope)

    data = envelope.data
    module = data.get("module_name")
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

    day = metric.get("day")
    if not isinstance(day, str) or not day:
        # `day` уходит в ключ дедупа ядра: без него получился бы ключ «сутки
        # None», и все такие сигналы слиплись бы в одни сутки навсегда.
        logger.warning("eventbus.system.day_missing event_id=%s", envelope.event_id)
        return

    signal_budget(
        used=metric.get("used"),
        limit=metric.get("limit"),
        day=day,
        cost_usd=metric.get("cost_usd"),
    )


def register_system_handlers() -> None:
    register(EVENT_NAME, 1, handle_system_health_degraded)


__all__ = ["handle_system_health_degraded", "register_system_handlers"]
