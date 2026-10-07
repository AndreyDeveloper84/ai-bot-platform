"""Момент, раньше которого переписка модели не отдаётся, — после «забудь X» (DRF-2700).

Решение владельца 07.10.2026 (лист решений, п.23):

    После «забудь X» исключать из будущего контекста сам факт и связанные с
    ним выводы, включая их следы в истории и выжимках. … Остальную переписку
    сохранять доступной пользователю. Видимая история и данные, которые
    получает модель, — разные вещи. Если пока нельзя надёжно отделить
    запрещённые фрагменты, временно не передавать модели затронутую историю.
    Простая пометка «не учитывай» недостаточна.

Отделить «реплики про X» от остальных надёжно нельзя: человек и Ayla говорят
о факте разными словами. Поэтому действует последняя фраза решения — модели
не отдаётся вся переписка до момента стирания. Строки переписки не меняются:
человек видит их как прежде.

# Что хранится

Одно время на человека — ``UserPersonalContext.model_history_cutoff_at``. В нём
нет содержания: по нему нельзя узнать, что было стёрто, только когда. Поэтому
оно может жить дольше надгробия стёртой записи — и обязано: надгробие уходит
через 30 дней, а старая реплика может остаться среди последних десяти и через
год.

Время только растёт (:func:`stamp`): более раннее стирание не открывает
историю, закрытую более поздним.

# Кто ставит

Двери поштучного стирания по просьбе человека:
``memory_deleter.soft_delete_green_entries`` (чат «забудь X», экран памяти) и
``RedZoneReader.soft_delete_for_subject`` (раздел «Здоровье»). Ход чата ставит
его ещё раз после записи своего ответа: «Готово — забыла, что ты …» повторяет
стёртое и сам обязан остаться до отсечки.

«Забудь всё», срок хранения и отзыв согласия сюда не входят: у первых свои
пути (обезличивание переписки), у отзыва время уже лежит в журнале согласий.
Сводит всё воедино :func:`apps.consent.services.model_history_cutoff`.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime

from django.db.models import Max, Q
from django.utils import timezone

from apps.identity.models import MemoryEntry, UserPersonalContext

#: Причины поштучного стирания по просьбе человека. ``forget_all`` и
#: ``withdrawal`` — не здесь: см. докстринг модуля.
PIECEWISE_REQUEST_REASONS: frozenset[str] = frozenset(
    {
        MemoryEntry.DELETION_REASON_USER_DELETE,
        MemoryEntry.DELETION_REASON_USER_REQUEST_MINIAPP,
    }
)


def stamp(user_id: uuid.UUID | None, *, at: datetime | None = None) -> bool:
    """Сдвинуть отсечку человека на ``at`` (по умолчанию — сейчас). Назад не двигает.

    Returns:
      Сдвинулась ли отсечка.
    """
    if not user_id:
        return False
    at = at or timezone.now()
    moved = (
        UserPersonalContext.objects.filter(user_id=user_id)
        .filter(Q(model_history_cutoff_at__isnull=True) | Q(model_history_cutoff_at__lt=at))
        .update(model_history_cutoff_at=at)
    )
    return bool(moved)


def latest(user_ids: Iterable[uuid.UUID]) -> datetime | None:
    """Самая поздняя отсечка среди записей человека; ``None`` — её нет."""
    ids = [uid for uid in user_ids if uid]
    if not ids:
        return None
    found: datetime | None = UserPersonalContext.objects.filter(user_id__in=ids).aggregate(
        latest=Max("model_history_cutoff_at")
    )["latest"]
    return found


__all__ = ["PIECEWISE_REQUEST_REASONS", "latest", "stamp"]
