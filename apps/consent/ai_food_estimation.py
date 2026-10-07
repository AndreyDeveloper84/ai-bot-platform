"""Согласие на ИИ-оценку еды — отдельное и добровольное (DRF-2845).

Решение владельца 07.10.2026: когда справочник блюда не знает, калории может
оценить внешняя модель — но только с отдельного согласия человека. Это НЕ
ограничение по здоровью (решение 04.10 «оценка доступна всем» в силе) и НЕ
часть согласия на дневник: это согласие на передачу названия блюда наружу.

## Что именно под согласием

Только ТЕКСТ-оценка калорий: в модель уходит название блюда словами человека
и больше ничего. Фото и голос — другие передачи, у них свои основания; это
согласие их не покрывает и не заменяет.

## Кто проверяет

Модель зовёт каталог, а согласие лежит здесь, в боте: каталог его не видит.
Поэтому проверяет бот — :func:`estimate_permitted` читается заново перед
КАЖДЫМ вызовом оценки и записи и едет каталогу полем ``ai_estimate_allowed``.
Перепись ``tests/test_ai_food_estimation_gate_2845.py`` требует, чтобы каждое
место вызова несло прямой вызов предиката, а не значение, прочитанное раньше:
между чтением и отправкой человек мог согласие отозвать.

## Что модуль обязан держать

* **добровольность** — отказ не закрывает ни справочник, ни дневник; тип не
  выдаётся вместе с ``personal_data``, приветствием или переносом данных и
  никому не выдаётся автоматически. Обратное есть: это надстройка над
  основанием, и отзыв ``personal_data`` снимает и её
  (``services._PERSONAL_DATA_CASCADE``);
* **осведомлённость** — выдача только под той версией текста, которую
  показали (:data:`AI_FOOD_ESTIMATION_DOCUMENT_VERSION`); согласие под
  прежней версией предикат не признаёт;
* **обратимость** — :func:`withdraw` снимает согласие по всем оболочкам
  человека, строки остаются в журнале.

## Что ещё не включено

Механизм лежит за ``AI_FOOD_ESTIMATION_CONSENT_REQUIRED`` (выключен): пока
флаг выключен, :func:`estimate_permitted` отвечает «можно» всем, как было до
листа.

Поверхность выдачи и отзыва — ручка Mini App ``me/consents/ai-food-estimation/``
(DRF-2867). Отзыв работает всегда. Выдача закрыта, пока нет утверждённого
текста (:data:`AI_FOOD_ESTIMATION_TEXT`): его утверждает владелец, и до тех
пор включать флаг по-прежнему нечем.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apps.consent.models import ConsentRecord

if TYPE_CHECKING:  # pragma: no cover
    from apps.identity.models import BotUser

logger = logging.getLogger(__name__)

AI_FOOD_ESTIMATION = ConsentRecord.ConsentType.AI_FOOD_ESTIMATION.value

#: Версия текста, под которой выдаётся согласие. ``draft`` — до утверждения
#: текста владельцем и юридической проверки.
AI_FOOD_ESTIMATION_DOCUMENT_VERSION = "ai-food-estimation-draft-v1"

#: Текст не утверждён. Снимается вместе с подъёмом версии.
PENDING_LEGAL = True

#: Полный текст согласия, который человек читает перед «Разрешить» (DRF-2867).
#: ``None`` — текста нет: его утверждает владелец (получатель и страна
#: обработки ещё не подтверждены). Сюда кладётся ТОЛЬКО утверждённый текст,
#: вместе с подъёмом версии; черновик с пробелами человеку не показывают.
AI_FOOD_ESTIMATION_TEXT: str | None = None

#: Откуда пришли выдача и отзыв с экрана Mini App.
MINIAPP_SOURCE = "miniapp"


class TextNotApprovedError(RuntimeError):
    """Выдать согласие нельзя: текста, под которым его дают, ещё нет."""


def text_approved() -> bool:
    """Есть ли текст, который можно показать человеку перед выдачей."""
    return bool(AI_FOOD_ESTIMATION_TEXT)


def grant_from_screen(bot_user: "BotUser", *, document_version: str) -> bool:
    """Выдача с экрана согласия Mini App (DRF-2867).

    Экран обязан показать человеку полный текст. Пока текста нет, показать
    нечего — и выдача закрыта здесь, на сервере, а не только спрятанной
    кнопкой: согласие «под ничем» не было бы осведомлённым.

    Raises:
      TextNotApprovedError: текст не утверждён.
      UnknownDisclosureVersionError: версия не та, что показывали.
    """
    if not text_approved():
        raise TextNotApprovedError(AI_FOOD_ESTIMATION_DOCUMENT_VERSION)
    return grant(bot_user, document_version=document_version, source=MINIAPP_SOURCE)


class UnknownDisclosureVersionError(ValueError):
    """Клиент прислал не ту версию текста, что показывает сервер."""


def consent_required() -> bool:
    """Включён ли механизм: требуется ли согласие для ИИ-оценки."""
    from django.conf import settings

    return bool(getattr(settings, "AI_FOOD_ESTIMATION_CONSENT_REQUIRED", False))


def grant(bot_user: "BotUser", *, document_version: str, source: str) -> bool:
    """Выдать согласие по всем оболочкам человека. Идемпотентно.

    Возвращает ``True``, если после вызова действующее согласие есть, —
    проверкой ЧТЕНИЕМ тем же предикатом, что стоит перед отправкой.

    Raises:
      UnknownDisclosureVersionError: версия не та, что показывали.
    """
    from apps.consent.services import record_person_consent

    if document_version != AI_FOOD_ESTIMATION_DOCUMENT_VERSION:
        raise UnknownDisclosureVersionError(document_version)
    record_person_consent(
        bot_user,
        consent_type=AI_FOOD_ESTIMATION,
        source=source,
        document_version=AI_FOOD_ESTIMATION_DOCUMENT_VERSION,
    )
    return is_granted(bot_user)


def withdraw(bot_user: "BotUser", *, source: str) -> int:
    """Отозвать согласие по всем оболочкам человека. Идемпотентно.

    Возвращает число снятых грантов. Только этот тип: дневник и
    ``personal_data`` отзыв не трогает.
    """
    from apps.consent.services import withdraw_person_consent

    return withdraw_person_consent(bot_user, consent_type=AI_FOOD_ESTIMATION, source=source)


def is_granted(bot_user: "BotUser") -> bool:
    """Действует ли согласие СЕЙЧАС — на текущий текст, по всем оболочкам.

    Правило то же, что у :func:`apps.consent.services.has_person_consent`
    (последняя выдача позже последнего отзыва на любой оболочке), плюс
    версия: выдача под прежним текстом не считается.
    """
    from apps.consent.services import person_channel_shells

    rows = list(
        ConsentRecord.all_tenants.filter(
            bot_user__in=person_channel_shells(bot_user),
            consent_type=AI_FOOD_ESTIMATION,
            granted=True,
        ).values_list("captured_at", "withdrawn_at", "document_version")
    )
    active = [
        captured
        for captured, withdrawn, version in rows
        if withdrawn is None and version == AI_FOOD_ESTIMATION_DOCUMENT_VERSION
    ]
    if not active:
        return False
    withdrawals = [withdrawn for _, withdrawn, _ in rows if withdrawn is not None]
    if not withdrawals:
        return True
    return max(active) > max(withdrawals)


def estimate_permitted(bot_user: "BotUser") -> bool:
    """Можно ли СЕЙЧАС отдать блюдо этого человека на ИИ-оценку.

    Единственный вопрос, который задают места вызова оценки и записи; ответ
    едет каталогу полем ``ai_estimate_allowed``. Читается перед каждым
    вызовом — не хранится и не передаётся между шагами.

    * механизм выключен — «можно» всем, как до листа;
    * «Без чисел» — «нельзя»: число человеку не покажут, значит и название
      наружу слать незачем;
    * иначе — действующее согласие.

    Fail-closed: согласие не удалось прочитать — оно не доказано.
    """
    if not consent_required():
        return True
    try:
        from apps.nutrition_proactive.prefs import numbers_hidden_for

        if numbers_hidden_for(bot_user):
            return False
        return is_granted(bot_user)
    except Exception:  # noqa: BLE001 — fail-closed: не доказано — наружу не шлём
        logger.exception(
            "consent.ai_food_estimation.check_failed person=%s", getattr(bot_user, "pk", None)
        )
        return False


__all__ = [
    "AI_FOOD_ESTIMATION",
    "AI_FOOD_ESTIMATION_DOCUMENT_VERSION",
    "AI_FOOD_ESTIMATION_TEXT",
    "MINIAPP_SOURCE",
    "TextNotApprovedError",
    "grant_from_screen",
    "text_approved",
    "PENDING_LEGAL",
    "UnknownDisclosureVersionError",
    "consent_required",
    "estimate_permitted",
    "grant",
    "is_granted",
    "withdraw",
]
