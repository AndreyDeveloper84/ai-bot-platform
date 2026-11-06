"""Срок жёлтой и красной памяти — гибрид «использование продлевает до предела» (DRF-2774).

Решение владельца 06.10 (передача оркестратора от 06.10, §6):

    Фактическое использование записи в ответе/рекомендации продлевает её
    текущий срок только до жёсткого предела от создания. Фоновое чтение,
    обслуживание и простое включение в контекст не считаются использованием.
    Использование не подтверждает истинность вывода. Проверки актуальности
    сохраняются. Нельзя воскресить истёкшую запись или пересоздать её ради
    обхода предела. Удаление/отзыв согласия имеют приоритет.

## Каркас без сроков

Владелец утвердил подготовку таблицы категорий, но не сроки. Поэтому
:data:`TERM_POLICIES` несёт состав, цель, условие подтверждения и действие при
истечении — а все числа ``None``. Политика без чисел не утверждена
(:attr:`MemoryTermPolicy.approved`), и запись её категории ведёт себя как до
этого листа: срок от писателя, продления нет. Сроки придут одной правкой
таблицы.

## Единственная дверь

:func:`record_memory_use` — и только для поверхностей :data:`USE_SURFACES`
(факт процитирован в ответе / стал основанием рекомендации). Поверхности
:data:`NON_USE_SURFACES` — включение в промпт, экран памяти, выгрузка, свипы —
явно ничего не продлевают. Незнакомая поверхность — ошибка: «использование»
не должно появляться молча.

## Что дверь не продлевает никогда

* запись с надгробием или живой заявкой на удаление, человека с «забудь всё»
  или с живой заявкой, без действующего согласия зоны — удаление и отзыв
  важнее;
* **истёкшую** запись (``expires_at <= now``) — её не воскрешают;
* зелёную зону (срока нет) и выводы Ф4 (``consent_scope=preference_inference``,
  свой абсолютный срок 30/180 — DRF-2782): граница — по пайплайну-писателю;
* категории с ``extend_by != use``: ментальное здоровье и особые категории
  152-ФЗ продлеваются только явным подтверждением (решение главного окна
  06.10, Q3);
* ответы «Проверки перед процедурой» Body Care — другая модель
  (``ScreeningAnswer``), их пригодность = версия анкеты + ``expires_at`` (D-7);
  дверь принимает только ``MemoryEntry``.

Срок только растёт и не выходит за предел:
``new = max(expires_at, min(now + extension, created_at + hard_cap))``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.identity.models import MemoryEntry, RedZoneAccessLog, UserPersonalContext

#: Поверхности, где использование — факт процитирован в ответе или стал
#: основанием рекомендации. Только они продлевают срок.
USE_ANSWER_CITED = "answer_cited"
USE_RECOMMENDATION_REASON = "recommendation_reason"
USE_SURFACES = frozenset({USE_ANSWER_CITED, USE_RECOMMENDATION_REASON})

#: Явно НЕ использование (решение владельца: «фоновое чтение, обслуживание и
#: простое включение в контекст»). Дверь на них — тихое «ничего».
NON_USE_SURFACES = frozenset(
    {
        "prompt_context",  # memory_block / memory_surface — включение в контекст
        "memory_screen",  # экран памяти Mini App — человек смотрит своё
        "export_152fz",  # выгрузка субъекту
        "sweep",  # свипы и обслуживание
        "admin",  # просмотр оператором
    }
)

#: Как категория продлевается.
EXTEND_BY_USE = "use"
EXTEND_BY_CONFIRMATION_ONLY = "confirmation_only"
EXTEND_BY_NONE = "none"

#: Ф4 — выводы пишет свой пайплайн с этой областью согласия (Ф4a-2); их срок
#: абсолютный и продлению не подлежит (DRF-2782).
F4_CONSENT_SCOPE = "preference_inference"

USE_ACCESS_PURPOSE = "memory_use_extension — факт использован в ответе или рекомендации"
USE_ACCESS_ACTOR = "memory_term"


@dataclass(frozen=True)
class MemoryTermPolicy:
    """Строка таблицы категорий. Числа — дни; ``None`` — ждёт владельца."""

    composition: str
    purpose: str
    initial_days: int | None
    extension_days: int | None
    hard_cap_days: int | None
    extend_by: str
    confirmation: str
    on_expiry: str

    @property
    def approved(self) -> bool:
        """Сроки утверждены — без них ни продления, ни свипа по одной дате."""
        return None not in (self.initial_days, self.extension_days, self.hard_cap_days)


_ON_EXPIRY_YELLOW = "надгробие ttl_purge → физическая очистка DRF-2775"
_ON_EXPIRY_RED = "надгробие ttl_purge + RedZoneAccessLog → физическая очистка DRF-2775"

Y, R = MemoryEntry.SENSITIVITY_YELLOW, MemoryEntry.SENSITIVITY_RED

#: Таблица по реальным категориям (ADR-0011 §4.2–4.3 × ``MemoryEntry.KIND_CHOICES``).
#: Сроки — ``None``: владелец утвердил таблицу, не значения.
#:
#: Ключ — ``(зона, kind)``. Красный ``symptom`` несёт и хронические состояния,
#: и ментальное здоровье: ``kind`` их не различает, поэтому строка берёт
#: строгость самого чувствительного — только подтверждением.
TERM_POLICIES: dict[tuple[str, str], MemoryTermPolicy] = {
    (Y, "relationship"): MemoryTermPolicy(
        "дети, их возраст, чувствительности партнёра — только сказанное",
        "подбор услуг и времени",
        None,
        None,
        None,
        EXTEND_BY_USE,
        "при использовании не нужно; уточнять при противоречии новому заявлению",
        _ON_EXPIRY_YELLOW,
    ),
    (Y, "financial"): MemoryTermPolicy(
        "ценовой порог, отказы «дорого»",
        "подбор по бюджету",
        None,
        None,
        None,
        EXTEND_BY_USE,
        "не требует; вывод показывается как предположение",
        _ON_EXPIRY_YELLOW,
    ),
    (Y, "preference"): MemoryTermPolicy(
        "чувствительность кожи, тип питания — сказанные человеком",
        "безопасность и подбор",
        None,
        None,
        None,
        EXTEND_BY_USE,
        "перед процедурой — уточнить",
        _ON_EXPIRY_YELLOW,
    ),
    (Y, "contraindication"): MemoryTermPolicy(
        "аллергии, сказанные человеком (извлечение — DRF-1290/DRF-2132)",
        "безопасность и подбор",
        None,
        None,
        None,
        EXTEND_BY_USE,
        "перед процедурой — уточнить",
        _ON_EXPIRY_YELLOW,
    ),
    (Y, "lifestyle"): MemoryTermPolicy(
        "поведенческие паттерны из записей (не Ф4)",
        "удобное время",
        None,
        None,
        None,
        EXTEND_BY_USE,
        "не требует",
        _ON_EXPIRY_YELLOW,
    ),
    (R, "contraindication"): MemoryTermPolicy(
        "беременность, хронические состояния, лекарства, значимые для процедур",
        "противопоказания к процедурам",
        None,
        None,
        None,
        EXTEND_BY_USE,
        "перед процедурой — обязательно (проверки актуальности, D-7)",
        _ON_EXPIRY_RED,
    ),
    (R, "symptom"): MemoryTermPolicy(
        "симптомы и ментальное здоровье — сказанные человеком",
        "безопасность общения и противопоказания",
        None,
        None,
        None,
        EXTEND_BY_CONFIRMATION_ONLY,
        "продление только явным подтверждением человека",
        _ON_EXPIRY_RED,
    ),
    (R, "other"): MemoryTermPolicy(
        "особые категории 152-ФЗ ст. 10 — только сказанное и значимое для противопоказаний",
        "услуга / противопоказание",
        None,
        None,
        None,
        EXTEND_BY_CONFIRMATION_ONLY,
        "продление только явным подтверждением человека",
        _ON_EXPIRY_RED,
    ),
}

#: Категория вне таблицы — без продления.
NO_POLICY = MemoryTermPolicy(
    "вне таблицы", "—", None, None, None, EXTEND_BY_NONE, "—", "по сроку писателя"
)


def policy_for(zone: str, kind: str) -> MemoryTermPolicy:
    return TERM_POLICIES.get((zone, kind), NO_POLICY)


def approved_categories() -> list[tuple[str, str]]:
    """Категории с утверждёнными сроками — для свипа по одной дате."""
    return [key for key, policy in TERM_POLICIES.items() if policy.approved]


def person_holds_back(user_id: uuid.UUID, zone: str) -> bool:
    """Удаление и отзыв важнее: «забудь всё», живая заявка, нет согласия зоны.

    Одно правило на продление срока и на чтение красной зоны ради использования
    (``RedZoneReader.read``, DRF-2132): «хранится» не значит «можно использовать».
    """
    from apps.consent.services import has_memory_consent

    upc = UserPersonalContext.objects.filter(user_id=user_id).first()
    if upc is not None and (upc.forget_all_requested_at or upc.deletion_requested_at):
        return True
    return not has_memory_consent(user_id, zone)


def record_memory_use(
    entries: Iterable[MemoryEntry],
    *,
    surface: str,
    now: datetime | None = None,
) -> int:
    """Отметить использование записей и продлить срок там, где это разрешено.

    Returns:
      Сколько записей продлено.

    Raises:
      ValueError: незнакомая поверхность.
      TypeError: не ``MemoryEntry`` (например, ответ проверки Body Care).
    """
    if surface in NON_USE_SURFACES:
        return 0
    if surface not in USE_SURFACES:
        raise ValueError(f"unknown memory use surface: {surface!r}")
    entries = list(entries)
    for entry in entries:
        if not isinstance(entry, MemoryEntry):
            raise TypeError(f"record_memory_use takes MemoryEntry only, got {type(entry).__name__}")
    now = now or timezone.now()
    extended = 0
    for entry in entries:
        new_expiry = _extended_expiry(entry, now)
        if new_expiry is None:
            continue
        if _extend(entry, new_expiry=new_expiry, now=now):
            extended += 1
    return extended


def _extended_expiry(entry: MemoryEntry, now: datetime) -> datetime | None:
    """Новый срок записи — или ``None``, если продлевать нельзя."""
    if entry.sensitivity_zone not in (Y, R):
        return None
    if entry.consent_scope == F4_CONSENT_SCOPE:
        return None
    if entry.soft_deleted_at is not None or entry.delete_requested_at is not None:
        return None
    if entry.expires_at is None or entry.expires_at <= now:
        return None
    policy = policy_for(entry.sensitivity_zone, entry.kind)
    if not policy.approved or policy.extend_by != EXTEND_BY_USE:
        return None
    if person_holds_back(entry.user_id, entry.sensitivity_zone):
        return None
    assert policy.extension_days is not None and policy.hard_cap_days is not None
    cap = entry.created_at + timedelta(days=policy.hard_cap_days)
    return max(entry.expires_at, min(now + timedelta(days=policy.extension_days), cap))


def _extend(entry: MemoryEntry, *, new_expiry: datetime, now: datetime) -> bool:
    """Записать продление; красная — под GUC и со строкой журнала ``use``."""
    from apps.identity.services.red_zone_guc import (
        _reset_red_zone_guc,
        _set_red_zone_guc,
        red_zone_principal,
    )

    red = entry.sensitivity_zone == R
    request_id = uuid.uuid4()
    with transaction.atomic():
        if red:
            _set_red_zone_guc(request_id)
        try:
            # Условие повторено в UPDATE: строку могли снять или она истекла
            # между чтением и записью — тогда продления нет.
            updated = MemoryEntry.objects.filter(
                pk=entry.pk,
                soft_deleted_at__isnull=True,
                delete_requested_at__isnull=True,
                expires_at__gt=now,
            ).update(
                expires_at=new_expiry,
                last_used_at=now,
                last_used_count=F("last_used_count") + 1,
            )
            if updated and red:
                RedZoneAccessLog.objects.create(
                    memory_entry_id=entry.pk,
                    user_id=entry.user_id,
                    accessor_role=RedZoneAccessLog.ACCESSOR_SYSTEM_JOB,
                    accessor_principal=red_zone_principal(
                        RedZoneAccessLog.ACCESSOR_SYSTEM_JOB, USE_ACCESS_ACTOR
                    ),
                    access_type=RedZoneAccessLog.ACCESS_USE,
                    request_id=request_id,
                    purpose=USE_ACCESS_PURPOSE,
                )
        finally:
            if red:
                _reset_red_zone_guc()
    return bool(updated)
