"""Согласие на обработку медданных — путь выдачи и отзыва (DRF-1453).

``ConsentType.HEALTH`` был объявлен в модели и в миграциях — и больше нигде.
Ни одного места выдачи не существовало, поэтому нутриционная поверхность
(:mod:`apps.orchestrator.nutrition_context`) отказывала независимо от флагов:
её сторож требует HEALTH, а выдать HEALTH было нечем. Этот модуль — та самая
недостающая половина.

### Почему отдельно от ``PERSONAL_DATA``

Данные о питании — специальная категория по 152-ФЗ ст. 10. Ст. 10 ч. 1 п. 1
допускает обработку по согласию, но согласие на специальную категорию не
поглощается общим согласием ст. 6: «принимаю всё» одной галочкой здесь не
годится. Отсюда три свойства, которые модуль обязан удержать:

* **раздельность** — HEALTH никогда не выдаётся заодно с ``PERSONAL_DATA``
  (сравните: ``global_onboarding`` осознанно пишет ``personal_data`` +
  ``memory_green`` одним тапом, потому что текст S2 дословно описывает
  память; текста про медданные там нет и быть не должно);
* **осведомлённость** — выдать можно только ту версию раскрытия, которую
  человеку показали: :func:`grant` принимает ``document_version`` и
  отвергает любую, кроме известной (:data:`HEALTH_CONSENT_DOCUMENT_VERSION`).
  Версия — снимок текста, а не украшение: при следующей редакции раскрытия
  константа поднимается, и старые согласия перестают проходить
  version-строгую проверку, то есть человека спросят заново;
* **обратимость** — :func:`withdraw` возвращает поверхность в отказ, а
  строки не удаляет: ``withdrawn_at`` проставляется, audit-trail целиком
  остаётся (append-only контракт ``ConsentRecord``).

### Почему по человеку, а не по строке

Выдача и отзыв идут через person-level примитивы
(:func:`apps.consent.services.record_person_consent` /
:func:`~apps.consent.services.withdraw_person_consent`): в пилоте у человека
несколько ``BotUser`` — Mini App резолвит свою строку, чат резолвит свою, —
и согласие, записанное только на ту, что спросила, было бы невидимо той
поверхности, которая читает. Человек нажал «разрешаю» в мини-приложении, а
консьерж продолжил отказывать: формально согласие есть, фактически ничего не
изменилось. Читающая сторона при этом не меняется вовсе — сторож
``has_global_consent`` в ``nutrition_context`` остаётся тем же.
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

#: Версия раскрытия о медданных, которую показывает мини-приложение.
#: Меняется ВМЕСТЕ с текстом раскрытия (``HEALTH_CONSENT_DOCUMENT_VERSION`` в
#: ``apps/miniapp/src/lib/health-consent.ts``); расхождение отвергается
#: :func:`grant` — сервер не записывает согласия на текст, которого не знает.
HEALTH_CONSENT_DOCUMENT_VERSION = "health-data-v1"

#: Откуда пришла выдача. Свободная форма по контракту модели.
GRANT_SOURCE = "miniapp:profile_health_consent"
WITHDRAW_SOURCE = "miniapp:profile_health_consent_withdraw"

_TYPE = ConsentRecord.ConsentType.HEALTH.value


class UnknownDisclosureVersionError(ValueError):
    """Клиент прислал версию раскрытия, которой сервер не знает.

    Это не педантизм: ``document_version`` — единственное доказательство
    того, ЧТО именно человеку показали в момент согласия. Принять чужую
    строку значит записать в юридический журнал непроверяемое утверждение.
    """


def grant(bot_user: "BotUser", *, document_version: str) -> ConsentRecord | None:
    """Выдать согласие на медданные. Идемпотентно.

    Args:
      bot_user: аутентифицированная строка того, кто согласился.
      document_version: версия раскрытия, показанного человеку. Должна
        совпасть с :data:`HEALTH_CONSENT_DOCUMENT_VERSION`.

    Returns:
      Действующая строка согласия — созданная этим вызовом или уже стоявшая.
      ``None`` только если запись не доехала (проверяется читающим предикатом,
      а не оптимистичным «мы же только что записали»).

    Raises:
      UnknownDisclosureVersionError: версия не совпала.
    """
    if document_version != HEALTH_CONSENT_DOCUMENT_VERSION:
        raise UnknownDisclosureVersionError(document_version)
    record_person_consent(
        bot_user,
        consent_type=_TYPE,
        source=GRANT_SOURCE,
        document_version=HEALTH_CONSENT_DOCUMENT_VERSION,
    )
    return current_record(bot_user)


def withdraw(bot_user: "BotUser") -> int:
    """Отозвать согласие на медданные. Идемпотентно; строки не удаляются.

    Returns:
      Сколько активных грантов снято (0 — согласия и не было).
    """
    return withdraw_person_consent(
        bot_user,
        consent_type=_TYPE,
        source=WITHDRAW_SOURCE,
    )


def is_granted(bot_user: "BotUser") -> bool:
    """Есть ли у человека действующее согласие на медданные СЕЙЧАС.

    Тот же предикат, которым ходит сторож нутриционной поверхности, — чтобы
    экран не мог показать «разрешено», пока поверхность отказывает.
    """
    return has_global_consent(bot_user, _TYPE)


def current_record(bot_user: "BotUser") -> ConsentRecord | None:
    """Действующая строка согласия (для даты выдачи на экране), либо None."""
    return (
        ConsentRecord.all_tenants.filter(
            bot_user=bot_user,
            consent_type=_TYPE,
            granted=True,
            withdrawn_at__isnull=True,
        )
        .order_by("-captured_at")
        .first()
    )
