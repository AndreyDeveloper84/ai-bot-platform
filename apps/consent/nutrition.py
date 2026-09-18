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

import logging
from typing import TYPE_CHECKING

from apps.consent import services
from apps.consent.models import ConsentRecord
from apps.consent.services import (
    record_person_consent,
    withdraw_person_consent,
)

if TYPE_CHECKING:
    from apps.identity.models import BotUser

logger = logging.getLogger(__name__)

#: Дневник: еда, напитки, фотографии, голосовые — scope M1.
DIARY = ConsentRecord.ConsentType.FOOD_DIARY_PROCESSING.value

#: Версия текста согласия на дневник питания. ЕДИНСТВЕННОЕ определение:
#: ``apps/consent/food_diary_disclosure.py`` берёт её отсюда псевдонимом, а
#: ``apps/miniapp/src/lib/food-scanner.ts`` несёт копию, сверяемую тестом
#: паритета (``test_food_diary_disclosure.py::test_the_version_matches_on_both_surfaces``).
#:
#: ``food-diary-v1`` — решение владельца 17.09: текст v1 = каноническое
#: раскрытие Z9 из #1812 (``food_diary_disclosure.DISCLOSURE_BODY``), с этого
#: момента НЕИЗМЕНЯЕМ. Любое содержательное изменение текста = ``food-diary-v2``
#: с новой константой, без перезаписи v1: человек согласился на текст, который
#: ему показали, и запись согласия обязана указывать ровно на него.
#: ``food-diary-v0`` — черновой контракт, не используется: строки под ним
#: :func:`diary_is_granted` не признаёт (D3, механизм включён 17.09).
FOOD_DIARY_CONSENT_DOCUMENT_VERSION = "food-diary-v1"

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
    """Действует ли согласие на дневник питания СЕЙЧАС — на ТЕКУЩИЙ текст.

    Версия передаётся (D3, включено 17.09 вместе с ``food-diary-v1``): строка,
    выданная под прежним текстом, предикат не признаёт — человека спросят
    заново на новом тексте. Без этого поднятие версии было бы обещанием без
    механизма: строка v0 продолжала бы открывать дневник, а 409 на выдаче
    никогда бы не случился, потому что экран согласия не показали бы.
    """
    return services.has_global_consent(
        bot_user, DIARY, document_version=FOOD_DIARY_CONSENT_DOCUMENT_VERSION
    )


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


# --- DRF-2100 — одно согласие дневника; старый HEALTH — совместимость -----
#
# Решение владельца 18.09 (§48 п.8б), дословно: «HEALTH пока не удалять из
# данных и схемы: перестать создавать новые отдельные согласия, сохранить
# совместимость со старыми. Удаление типа — отдельная миграция после пилота».
#
# Совместимость — про ЧТЕНИЕ и показ: блок питания в консьерже, подсказки,
# строка диетолога, wellness-проактив и пункт меню открываются человеку,
# который дал старый HEALTH и не давал v1. Запись в дневник — ворота
# :mod:`apps.consent.diary_gate` (DRF-2093) — остаётся v1-only: она и до
# этого листа требовала реестр дневника (решение главного окна 18.09, по
# слову владельца: «сохранить совместимость», не «расширить»).
#
# Старая строка HEALTH признаётся под ЛЮБОЙ версией: её текст больше не
# показывается и не поднимается, сверять его не с чем. Строка дневника —
# только на текущий текст (:func:`diary_is_granted`), как и была.

_LEGACY_HEALTH = ConsentRecord.ConsentType.HEALTH.value


def diary_or_health_granted(bot_user: "BotUser") -> bool:
    """Есть ли у человека основание на данные о питании: дневник v1 ИЛИ старый HEALTH.

    Fail-closed: любая ошибка чтения — «основания нет». Ошибиться в другую
    сторону значило бы открыть особую категорию по сбою БД.

    Чтение реестра — через ``services.has_global_consent`` по модулю, а не по
    имени, импортированному сюда: читатели и до этого листа подменяли
    реестр в тестах по адресу ``apps.consent.services.has_global_consent``,
    и подмена обязана доставать все шесть разом — иначе один из них тест
    проверял бы мимо.
    """
    try:
        if diary_is_granted(bot_user):
            return True
        return services.has_global_consent(bot_user, _LEGACY_HEALTH)
    except Exception:  # noqa: BLE001 — fail-closed: основание не доказано
        logger.exception("consent.nutrition.diary_or_health_check_failed")
        return False


def diary_or_health_current_record(bot_user: "BotUser") -> ConsentRecord | None:
    """Действующая строка, которую признаёт :func:`diary_or_health_granted`.

    Дневник v1 — первым: у человека с обеими строками экран показывает дату
    того согласия, которое сегодня выдаётся; старый HEALTH — когда v1 нет.
    """
    diary = diary_current_record(bot_user)
    if diary is not None:
        return diary
    return (
        ConsentRecord.all_tenants.filter(
            bot_user=bot_user,
            consent_type=_LEGACY_HEALTH,
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
    return services.has_global_consent(bot_user, CALCULATION)
