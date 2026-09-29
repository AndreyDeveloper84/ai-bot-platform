"""Старые согласия на медданные — отзыв и чтение; выдачи больше нет (DRF-1453 → DRF-2100).

DRF-1453 построил здесь путь выдачи ``ConsentType.HEALTH`` — до него тип был
объявлен в модели и нигде не выдавался. DRF-2100 (решение владельца 18.09,
§48 п.8б, дословно: «HEALTH пока не удалять из данных и схемы: перестать
создавать новые отдельные согласия, сохранить совместимость со старыми.
Удаление типа — отдельная миграция после пилота») этот путь закрыл:
согласие на данные о питании теперь одно — дневник ``food-diary-v1``
(:mod:`apps.consent.nutrition`), и ручка профиля выдаёт его. Строки HEALTH,
выданные до этого, продолжают открывать чтение
(:func:`apps.consent.nutrition.diary_or_health_granted`), отзываются здесь
же и не удаляются.

Ниже — история, ради которой модуль был устроен так, а не иначе; она
объясняет, почему совместимость обязана быть про чтение, а «одна галочка
на всё» — нет.

### Почему отдельно от ``PERSONAL_DATA``

Данные о питании — специальная категория по 152-ФЗ ст. 10. Ст. 10 ч. 1 п. 1
допускает обработку по согласию, но согласие на специальную категорию не
поглощается общим согласием ст. 6: «принимаю всё» одной галочкой здесь не
годится. Отсюда три свойства, которые модуль обязан удержать:

* **раздельность** — HEALTH никогда не выдаётся заодно с ``PERSONAL_DATA``
  (сравните: ``global_onboarding`` осознанно пишет ``personal_data`` +
  ``memory_green`` одним тапом, потому что текст S2 дословно описывает
  память; текста про медданные там нет и быть не должно);
* **осведомлённость** — выдавалась только та версия раскрытия, которую
  человеку показали (:data:`HEALTH_CONSENT_DOCUMENT_VERSION`); то же правило
  держит :func:`apps.consent.nutrition.grant_diary` для v1;
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
    withdraw_person_consent,
)

if TYPE_CHECKING:
    from apps.identity.models import BotUser

#: Версия раскрытия, под которой стоят СТАРЫЕ строки HEALTH. Новых под ней
#: не выдаётся (DRF-2100); константа остаётся, чтобы старые строки и их
#: тесты читались по имени, а не по строке-литералу.
HEALTH_CONSENT_DOCUMENT_VERSION = "health-data-v1"

#: Откуда пришла выдача. Свободная форма по контракту модели.
GRANT_SOURCE = "miniapp:profile_health_consent"
WITHDRAW_SOURCE = "miniapp:profile_health_consent_withdraw"

_TYPE = ConsentRecord.ConsentType.HEALTH.value


# DRF-2100 — выдачи HEALTH больше нет. Решение владельца 18.09 (§48 п.8б):
# «перестать создавать новые отдельные согласия, сохранить совместимость со
# старыми». Ручка профиля выдаёт согласие дневника v1
# (:func:`apps.consent.nutrition.grant_diary`); этот модуль остался для
# СТАРЫХ строк: отзыв, чтение, дата на экране. ``grant`` снят намеренно —
# перепись ``apps/consent/tests/test_health_writers_census_2100.py`` держит
# «писателей HEALTH вне тестов = 0», и функция-писатель, «которую никто не
# зовёт», это ноль не прошла бы. Сеять HEALTH в тестах —
# ``apps/consent/tests/legacy_health.py``.


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
