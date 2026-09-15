"""Два согласия питания — предикаты и запись для поверхностей (§92, 10.09.2026).

Тот же приём, что у :mod:`apps.consent.health`: поверхность спрашивает
ФУНКЦИЮ, а не таблицу и не колонку. Причина не в стиле.

``tools/lint/consent_column_guard.py`` заведён после того, как три
поверхности за один квартал независимо друг от друга вывели право писать
человеку из денормализованной колонки ``BotUser.consent_at``. На замере
23.08 из пяти строк с непустой колонкой **четыре были отозваны**. Колонка
отвечает на вопрос «случалось ли это когда-нибудь», а читали её как
«можно ли сейчас», и по синтаксису эти два чтения неразличимы.

У второй колонки, ``BotUser.food_scanner_consent_at``, дефект был зеркальный:
её отзыв ставил ``NULL`` и стирал сам факт выдачи — то есть она говорила
«ничего не было» там, где было. Первая никогда не забывает, вторая
забывала всё; ошибаются они в разные стороны, а корень один.

## M1 — один реестр вместо колонки сканера (DRF-1963)

Владелец 15.09 (`PROMPT_ORCHESTRATOR_AYLA_CONTROLLED_PILOT_NEXT_WAVE.md` §6):
согласие сканера и дневника — строка ``ConsentRecord`` со scope
``food_diary_processing``, версией документа, датой выдачи, отзывом и
источником; второй backend не создаётся, колонка как отдельная система
убирается. Поэтому запись и чтение живут здесь, и больше нигде: гейт
сканера, проактив питания, экран мини-приложения и ``/me`` спрашивают эти
функции. Салонный бот (M4) читает ту же строку — :func:`grant_diary` пишет
на все оболочки человека.

## Почему согласие глобальное, а не тенантное

:func:`has_global_consent` смотрит по строке без ``current_tenant()``, как
это делает :mod:`apps.consent.health`, а выдача пишет по строке на каждую
оболочку человека. Человек, давший согласие в чате, не обязан давать его
заново, открыв мини-приложение: это одна и та же воля одного и того же
человека, а разные строки ``BotUser`` — наша внутренняя механика, о которой
он не знает.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.consent.models import ConsentRecord
from apps.consent.services import (
    has_global_consent,
    record_person_consent,
    withdraw_person_consent,
)

if TYPE_CHECKING:
    from apps.identity.models import BotUser

#: Дневник: еда, напитки, фотографии, голосовые — scope M1.
DIARY = ConsentRecord.ConsentType.FOOD_DIARY_PROCESSING.value

#: Версия текста согласия, который показывает экран сканера. ``v0`` — текст,
#: действующий сейчас; новый текст M2 поднимает версию, и
#: :func:`diary_is_granted` с ``document_version`` вернёт ``False`` тем, кто
#: соглашался на старый (D3). Меняется ВМЕСТЕ с
#: ``FOOD_DIARY_CONSENT_DOCUMENT_VERSION`` в ``apps/miniapp/src/lib/food-scanner.ts``.
FOOD_DIARY_CONSENT_DOCUMENT_VERSION = "food-diary-v0"

#: Откуда пришла выдача / отзыв. Свободная форма по контракту модели.
GRANT_SOURCE = "miniapp:food_scanner_consent"
WITHDRAW_SOURCE = "miniapp:food_scanner_consent_withdraw"

#: Персональный расчёт: вес, рост, возраст, физиологический пол,
#: активность, цель. Обратите внимание на СОСТАВ — он шире, чем «вес»:
#: правило 1 §92 называет вес как самый острый случай, но согласие
#: покрывает все шесть параметров, и четыре из них анкета спрашивает
#: РАНЬШЕ веса.
CALCULATION = ConsentRecord.ConsentType.PERSONAL_CALCULATION.value


class UnknownDisclosureVersionError(ValueError):
    """Клиент прислал версию текста, которой сервер не знает.

    ``document_version`` — единственное доказательство того, ЧТО человеку
    показали в момент согласия. Принять чужую строку значит записать в
    юридический журнал непроверяемое утверждение.
    """


def grant_diary(
    bot_user: "BotUser", *, document_version: str, source: str = GRANT_SOURCE
) -> ConsentRecord | None:
    """Выдать согласие на дневник питания. Идемпотентно.

    Returns:
      Действующая строка согласия — созданная этим вызовом или уже стоявшая;
      ``None`` только если запись не доехала (проверяется читающим запросом,
      а не оптимистичным «мы же только что записали»).

    Raises:
      UnknownDisclosureVersionError: версия не совпала с текущей.
    """
    if document_version != FOOD_DIARY_CONSENT_DOCUMENT_VERSION:
        raise UnknownDisclosureVersionError(document_version)
    record_person_consent(
        bot_user,
        consent_type=DIARY,
        source=source,
        document_version=FOOD_DIARY_CONSENT_DOCUMENT_VERSION,
    )
    return diary_current_record(bot_user)


def withdraw_diary(bot_user: "BotUser", *, source: str = WITHDRAW_SOURCE) -> int:
    """Отозвать согласие на дневник по всем оболочкам человека.

    Строки не удаляются: ``withdrawn_at`` проставляется, отзыв остаётся в
    журнале вместе с выдачей. Идемпотентно.

    Returns:
      Сколько активных грантов снято (0 — согласия и не было).
    """
    return withdraw_person_consent(bot_user, consent_type=DIARY, source=source)


def diary_is_granted(bot_user: "BotUser") -> bool:
    """Действует ли согласие на дневник питания СЕЙЧАС."""
    return has_global_consent(bot_user, DIARY)


def diary_current_record(bot_user: "BotUser") -> ConsentRecord | None:
    """Действующая строка согласия — для даты выдачи на экране — либо ``None``.

    Та же строка, которую видит :func:`diary_is_granted`: экран не может
    показать дату, которой предикат не признаёт.
    """
    return (
        ConsentRecord.all_tenants.filter(
            bot_user=bot_user,
            consent_type=DIARY,
            granted=True,
            withdrawn_at__isnull=True,
        )
        .order_by("-captured_at")
        .first()
    )


def calculation_is_granted(bot_user: "BotUser") -> bool:
    """Действует ли согласие на персональный расчёт СЕЙЧАС.

    «Сейчас», а не «когда-нибудь»: отозванное согласие обязано закрывать
    поверхность в тот же миг, иначе предикат повторяет дефект колонки.
    """
    return has_global_consent(bot_user, CALCULATION)
