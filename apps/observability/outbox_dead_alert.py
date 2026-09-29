"""Dead-letter outbox каталога → страница операторам в MAX (DRF-2306).

Контракт §6.4 обещает оповещение, когда событие outbox уходит в dead-letter;
каталог писал только ``logger.warning``. Сигнал идёт тем же рельсом, что
бюджет сканера (DRF-2196): каталог публикует ``system.module.health.degraded``
с ``module_name="appointments.outbox"``, потребитель
``eventbus.consumers.system`` отдаёт метрику сюда, а :func:`signal_outbox_dead`
решает, звучать ли странице (``alerting.page``: MAX, маскирование PII).

# Одна страница на тему, класс и час

Каталог уже сводит мёртвые строки батча в одно событие на (тема, класс) и
дедуплицирует по UTC-часу; здесь второй рубеж — ключ того же часа в кэше.
Тысяча мёртвых событий за час простоя — одна страница с числом, а не тысяча.
Классы: ``rejected`` — бот отказал навсегда (4xx, DRF-2302), повтор только
вручную после починки; ``retries_exhausted`` — 5xx или сеть 9 попыток
подряд (~4,5 ч).

# Чужие поля не попадают в текст как есть

Всё приходит из JSON каталога. Тема — только формы имени события, иначе «?»;
причина — только slug; код и число — только целые; класс и час — только из
закрытого набора и ISO, иначе молчание: они уходят в ключ дедупа, и мусор
там давал бы новую страницу на каждое значение. Людей в метрике нет по
построению: у функции нет параметра, которым человека можно назвать.

# Недоставленная страница

Как у бюджета: час занимается ДО отправки, недоставленная страница час
возвращает, исход ``not_delivered`` — вызывающий отказывается принять событие,
и outbox каталога повторит доставку.

# Известный предел

Сигнал идёт тем же outbox к тому же боту. Если события умерли из-за того, что
бот лежал (5xx, сеть), сигнал тоже ждёт его подъёма, а при простое больше
~4,5 ч сам уходит в dead — молча: о смерти системного события каталог не
сигналит (обрыв круга). Независимого канала у каталога нет (DRF-2145).
"""

from __future__ import annotations

import logging
import re
from typing import Final, Literal

from django.core.cache import cache

from apps.observability.alerting import page

logger = logging.getLogger(__name__)

Outcome = Literal["delivered", "skipped", "not_delivered"]

#: Что значит класс оператору — последствие и что делать.
_FAILURES: Final[dict[str, str]] = {
    "rejected": (
        "Бот отказал навсегда (4xx) — outbox каталога положил события в dead-letter "
        "сразу. Повтор — вручную после починки причины: replay_dead_outbox_events."
    ),
    "retries_exhausted": (
        "Бот не принял события за 9 попыток (~4,5 ч: 5xx или сеть) — они в dead-letter. "
        "Проверить бота, затем повторить: replay_dead_outbox_events."
    ),
}

_TOPIC: Final = re.compile(r"[a-z][a-z_]*(?:\.[a-z][a-z_]*){1,3}")
_REASON: Final = re.compile(r"[a-z_]{1,64}")
_HOUR: Final = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}")

_DEDUP_KEY: Final = "outbox_dead_alert:{topic}:{failure}:{hour}"
#: Двое суток: час зашит в ключ, TTL нужен, чтобы пережить опоздавшую доставку.
_DEDUP_TTL_SECONDS: Final = 48 * 60 * 60


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _claim(key: str) -> bool:
    """Занять час. Потеря кэша — «уже звучало»: молчание, а не шквал."""
    try:
        return bool(cache.add(key, 1, timeout=_DEDUP_TTL_SECONDS))
    except Exception as exc:  # noqa: BLE001 — потеря кэша не роняет ingest
        logger.warning("observability.outbox_dead.dedup_unavailable err=%s", type(exc).__name__)
        return False


def _release(key: str) -> None:
    try:
        cache.delete(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("observability.outbox_dead.dedup_release_failed err=%s", type(exc).__name__)


def signal_outbox_dead(
    *,
    topic: object,
    failure: object,
    http_status: object,
    count: object,
    hour: object,
    reason: object,
) -> Outcome:
    """Поднять страницу о dead-letter outbox каталога. Никогда не бросает."""
    if not isinstance(failure, str) or failure not in _FAILURES:
        logger.warning("observability.outbox_dead.unknown_failure failure=%r", str(failure)[:32])
        return "skipped"
    if not isinstance(hour, str) or not _HOUR.fullmatch(hour):
        logger.warning("observability.outbox_dead.hour_invalid")
        return "skipped"

    topic_s = topic if isinstance(topic, str) and _TOPIC.fullmatch(topic) else "?"
    reason_s = reason if isinstance(reason, str) and _REASON.fullmatch(reason) else None
    status_i = _int_or_none(http_status)
    status_s = str(status_i) if status_i is not None and 100 <= status_i <= 599 else "нет ответа"
    count_i = _int_or_none(count)
    count_s = str(count_i) if count_i is not None and count_i > 0 else "?"

    key = _DEDUP_KEY.format(topic=topic_s, failure=failure, hour=hour)
    if not _claim(key):
        return "skipped"

    title = f"Каталог: события не доставлены боту — {topic_s}"
    body = (
        f"{_FAILURES[failure]}\n\n"
        f"тема: {topic_s}\n"
        f"час (UTC): {hour}\n"
        f"событий: {count_s}\n"
        f"код ответа бота: {status_s}\n"
        f"причина: {reason_s or 'не указана'}"
    )
    try:
        delivered = page("error", title, body, dedup_key=f"outbox_dead:{topic_s}:{failure}:{hour}")
    except Exception as exc:  # noqa: BLE001 — страж не роняет то, что стережёт
        logger.warning("observability.outbox_dead.page_failed err=%s", type(exc).__name__)
        delivered = False

    if not delivered:
        logger.warning(
            "observability.outbox_dead.page_not_delivered topic=%s failure=%s hour=%s",
            topic_s,
            failure,
            hour,
        )
        _release(key)
        return "not_delivered"
    return "delivered"
