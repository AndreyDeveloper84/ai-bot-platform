"""Читатель знания на ход — теневой (DRF-2729, контракт DRF-2719).

Собирает :class:`~apps.orchestrator.knowledge_licence.KnowledgeLicence` для
хода консьержа и ничего больше. Лицензию читает теневой затвор
(:mod:`apps.orchestrator.safety.claim_gate`); модель её не видит, в промпт и в
ответ из неё ничего не попадает — слов утверждений в ней нет вовсе
(:mod:`apps.integrations.ayla.knowledge_client`).

### Предмет — только уже разрешённая услуга

Читаем не «о чём шла речь», а то, что ход уже разрешил сам: карточки мастеров
инструмента ``show_masters``, у которых заполнен ``service_id``. Это поле
означает ровно одну услугу, однозначно совпавшую с запросом (DRF-962: при
нескольких совпадениях оно пустое намеренно). Свободный текст, название
процедуры в реплике, цель человека предметом не являются: вывести их из слов
значило бы проверять по словам.

Бот шаблон и связь услуги с шаблоном не судит: зеркало несёт id услуги салона
в каталоге (``CatalogService.ayla_service_id``), а оба условия — «связь
подтверждена» и «утверждение подтверждено» — проверяет ручка каталога.

### Флаг

``KNOWLEDGE_READ_SHADOW_ENABLED``. Ключа по умолчанию нет, и по умолчанию
выключено. Отдельный от ``CLAIM_GATE_SHADOW_ENABLED`` и от будущего живого
моста: теневую оценку можно включить, не включая ничего, что видит человек.
Выключен — сетевого вызова нет, лицензия ``None``, ход прежний.

### Три исхода, и они различаются

* ``None`` — читателя в этом ходу не было (флаг выключен; читатель упал);
* лицензия без предметов — читатель был, разрешённой услуги в ходе нет;
* лицензия с предметами — по каждому своё состояние.

### Цена — допущения, не замер

Чтение синхронное: лицензия обязана успеть к исходящему хуку. Поэтому не
больше :data:`MAX_SUBJECTS` предметов и общий бюджет :data:`TURN_BUDGET_S` на
ход; что не успело — ``UNAVAILABLE`` без вызова. При включённом флаге и
молчащем каталоге ход удлиняется не больше чем на бюджет, а после трёх сбоев
предохранитель клиента минуту не даёт звонить вовсе.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from typing import Any, Final

from django.conf import settings
from django.utils import timezone

from apps.orchestrator.decision_readiness.shadow import FlagReading, FlagSource
from apps.orchestrator.knowledge_licence import KnowledgeLicence, LicensedSubject, SubjectState

logger = logging.getLogger(__name__)

#: Настройка, включающая теневое чтение. По умолчанию ключа нет.
READ_SETTING: Final[str] = "KNOWLEDGE_READ_SHADOW_ENABLED"

#: Тот же словарь, что у теневого затвора: значение вне него ничего не включает.
_TRUE_WORDS = frozenset({"true", "1", "yes", "on"})
_FALSE_WORDS = frozenset({"false", "0", "no", "off", ""})

#: Сколько разных услуг читаем за ход. Карточек на странице до пяти, но
#: разрешённая услуга у них почти всегда одна и та же.
MAX_SUBJECTS: Final[int] = 3

#: Общий бюджет чтения на ход, секунды. Покрывает одно холодное соединение
#: (см. бюджеты клиента); тёплые запросы — десятки миллисекунд.
TURN_BUDGET_S: Final[float] = 5.0


def read_flag() -> FlagReading:
    """Прочитать флаг и сказать, откуда значение. Не бросает."""
    if not hasattr(settings, READ_SETTING):
        return FlagReading(value=False, source=FlagSource.READ_DEFAULT)

    raw = getattr(settings, READ_SETTING)
    if isinstance(raw, bool):
        return FlagReading(value=raw, source=FlagSource.SETTINGS)
    if isinstance(raw, str):
        word = raw.strip().lower()
        if word in _TRUE_WORDS:
            return FlagReading(value=True, source=FlagSource.SETTINGS)
        if word in _FALSE_WORDS:
            return FlagReading(value=False, source=FlagSource.SETTINGS)

    logger.warning(
        "orchestrator.knowledge_reader.flag_unreadable setting=%s type=%s — resolved to off",
        READ_SETTING,
        type(raw).__name__,
    )
    return FlagReading(value=False, source=FlagSource.MALFORMED)


def _resolved_service_ids(cards: Iterable[Any]) -> list[Any]:
    """Разрешённые услуги карточек — без повторов, в порядке карточек."""
    seen: list[Any] = []
    for card in cards:
        service_id = getattr(card, "service_id", None)
        if service_id is not None and service_id not in seen:
            seen.append(service_id)
    return seen


def licence_for_cards(
    cards: Iterable[Any], *, trace_id: object | None = None
) -> KnowledgeLicence | None:
    """Лицензия знания для хода, показавшего эти карточки. Не бросает.

    ``None`` — флаг выключен или читатель упал; см. докстринг модуля.
    """
    try:
        if not read_flag().value:
            return None

        from apps.integrations.ayla.knowledge_client import read_subject
        from apps.marketplace.discovery import catalog_salon_service_ids

        # Момент чтения — по часам БОТА и до первого вызова: затвор сравнивает
        # его со своим «сейчас» на тех же часах, а момент начала заведомо не
        # позже любого ответа каталога.
        read_at = timezone.now()
        started = time.monotonic()

        resolved = _resolved_service_ids(cards)
        # Перевод id — в ``apps.marketplace.discovery``: чтение зеркала поперёк
        # салонов разрешено только там (MKT1).
        salon_service_ids = catalog_salon_service_ids(resolved[:MAX_SUBJECTS])

        subjects: list[LicensedSubject] = []
        for salon_service_id in salon_service_ids:
            if time.monotonic() - started >= TURN_BUDGET_S:
                subjects.append(
                    LicensedSubject(
                        template_id="",
                        state=SubjectState.UNAVAILABLE,
                        salon_service_id=salon_service_id,
                    )
                )
                continue
            subjects.append(read_subject(salon_service_id=salon_service_id))

        licence = KnowledgeLicence(read_at=read_at, subjects=tuple(subjects))
        logger.info(
            "orchestrator.knowledge_reader.read resolved=%d read=%d known=%d unknown=%d "
            "unavailable=%d claims=%d elapsed_ms=%d trace=%s",
            len(resolved),
            len(subjects),
            licence.subjects_in(SubjectState.KNOWN),
            licence.subjects_in(SubjectState.UNKNOWN),
            licence.subjects_in(SubjectState.UNAVAILABLE),
            len(licence.claims()),
            int((time.monotonic() - started) * 1000),
            trace_id,
        )
        return licence
    except Exception:  # noqa: BLE001 — a shadow reader must never cost the reply
        logger.exception("orchestrator.knowledge_reader.failed trace=%s", trace_id)
        return None


__all__ = [
    "MAX_SUBJECTS",
    "READ_SETTING",
    "TURN_BUDGET_S",
    "licence_for_cards",
    "read_flag",
]
