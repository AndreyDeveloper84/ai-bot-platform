"""Согласие на предположения о предпочтениях — умная память Ф4 (DRF-2779).

Решение владельца 05.10.2026 (``Ayla/docs/OWNER_DECISIONS_MEMORY_AND_CONSENT_2026-10-05.md``
§«Умная память Ф4»): Ayla может предлагать запомнить предпочтения, которые
она заметила по обращениям и действиям человека, — «Вы несколько раз
выбирали вечер. Запомнить, что вам удобнее после 18:00?». Предположение не
становится фактом и не ограничивает выбор без подтверждения.

Порядок владельца: СНАЧАЛА согласие + просмотр + удаление, ЗАТЕМ включение
выводов. Этот модуль — первое: тип согласия, выдача, отзыв, проверка.
Производителя выводов ещё нет (Ф4b); единственный писатель выводимой памяти
(:mod:`apps.identity.services.memory_inferred`) производственных вызывающих
не имеет и с этого листа требует этого согласия.

Три свойства, которые модуль обязан держать:

* **добровольность** — отказ не блокирует ни запись, ни что-либо ещё: у
  них свои основания. Тип не выдаётся вместе с ``personal_data`` (ни
  приветствием, ни повторной выдачей хранения), и его отзыв ``personal_data``
  не трогает. Обратное — есть: это надстройка над основанием, поэтому отзыв
  хранения и удаление данных снимают и её (``services._PERSONAL_DATA_CASCADE``,
  как согласие дневника);
* **осведомлённость** — выдаётся только под той версией текста, которую
  показали (:data:`PREFERENCE_INFERENCE_DOCUMENT_VERSION`); клиент обязан
  прислать её обратно, как у персонального расчёта;
* **обратимость** — :func:`withdraw` снимает согласие по всем оболочкам
  человека; строки не удаляются, ``withdrawn_at`` проставляется. Что отзыв
  делает с уже накопленной производной памятью — DRF-2783, после
  физической очистки DRF-2775.

**Текст — черновик владельца на юридической проверке (#947).** Пока проверка
не пройдена, версия несёт пометку ``draft`` и :data:`PENDING_LEGAL` —
``True``. Когда юрист утвердит текст (или правку), поднимается версия, и
согласия под черновиком остаются в журнале под своей версией.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.consent.models import ConsentRecord

if TYPE_CHECKING:  # pragma: no cover
    from apps.identity.models import BotUser

PREFERENCE_INFERENCE = ConsentRecord.ConsentType.PREFERENCE_INFERENCE.value

#: Версия текста, под которой выдаётся согласие. ``draft`` — до юр-проверки.
PREFERENCE_INFERENCE_DOCUMENT_VERSION = "preference-inference-draft-v1"

#: Текст ещё на юридической проверке (#947). Снимается вместе с подъёмом версии.
PENDING_LEGAL = True

#: Черновик владельца 05.10.2026, дословно. Экран показывает его под версией
#: выше; сервер хранит его здесь, чтобы версия и слова не могли разойтись.
PREFERENCE_INFERENCE_TEXT = (
    "Разрешаю Ayla анализировать мои обращения и действия в сервисе, чтобы "
    "предлагать запомнить мои предпочтения для персонализации. Предположения "
    "будут показаны мне для подтверждения. Я могу исправлять и удалять "
    "сохранённые предпочтения и отключить эту функцию в настройках."
)

GRANT_SOURCE = "miniapp:preference_inference_consent"
WITHDRAW_SOURCE = "miniapp:preference_inference_consent_withdraw"


class UnknownDisclosureVersionError(ValueError):
    """Клиент прислал не ту версию текста, что показывает сервер."""


def grant(bot_user: "BotUser", *, document_version: str) -> bool:
    """Выдать согласие по всем оболочкам человека. Идемпотентно.

    Возвращает ``True``, если после вызова действующее согласие есть, —
    проверкой ЧТЕНИЕМ тем же предикатом, что у писателя памяти.

    Raises:
      UnknownDisclosureVersionError: версия не та, что показывали.
    """
    from apps.consent.services import record_person_consent

    if document_version != PREFERENCE_INFERENCE_DOCUMENT_VERSION:
        raise UnknownDisclosureVersionError(document_version)
    record_person_consent(
        bot_user,
        consent_type=PREFERENCE_INFERENCE,
        source=GRANT_SOURCE,
        document_version=PREFERENCE_INFERENCE_DOCUMENT_VERSION,
    )
    return is_granted(bot_user)


def withdraw(bot_user: "BotUser") -> int:
    """Отозвать согласие по всем оболочкам человека. Идемпотентно.

    Возвращает число снятых грантов. Только этот тип: ``personal_data`` и
    всё, что на нём держится, отзыв Ф4 не трогает.

    DRF-2783: вместе с согласием уходит и производная память — решение
    владельца «при отзыве согласия Ф4 … удалить производную память по
    процедуре». Стирание идёт и при повторном отзыве (0 снятых грантов):
    оно идемпотентно, и так повтор дочищает то, что не стёрлось в первый раз.
    """
    from django.db import transaction

    from apps.consent.services import withdraw_person_consent

    with transaction.atomic():
        withdrawn = withdraw_person_consent(
            bot_user,
            consent_type=PREFERENCE_INFERENCE,
            source=WITHDRAW_SOURCE,
        )
        _erase_derived_memory(bot_user)
    return withdrawn


def _erase_derived_memory(bot_user: "BotUser") -> int:
    """Надгробие всем выводам Ф4 человека — по всем его оболочкам (DRF-2783).

    Память ключуется ``ayla_user_id``, а согласие — оболочкой; у человека их
    несколько (Mini App и чат), и выводы могли лечь под любой из них. Оболочки
    без связки с Ayla памяти не имеют — пропускаются.

    Сбой стирания НЕ откатывает отзыв (своя точка сохранения): человек,
    нажавший «отключить», обязан остаться без согласия, даже если стереть не
    вышло, — использование выводов уже остановлено гейтом разговора (Ф4a-3,
    согласие проверяется в точке использования), а повторный отзыв дочистит.
    Сбой пишется в лог с исключением, не глотается молча.
    """
    import logging

    from django.db import transaction

    from apps.identity.models import BotUser
    from apps.identity.services.memory_deleter import soft_delete_inferences_for_withdrawal

    logger = logging.getLogger(__name__)
    try:
        with transaction.atomic():
            try:
                from apps.identity.services.privacy import person_shell_ids

                shell_ids = person_shell_ids(bot_user)
            except Exception:  # noqa: BLE001 — резолв личности сужается до самой строки
                logger.exception("consent.preference_inference.shell_resolution_failed")
                shell_ids = {bot_user.pk}
            ayla_user_ids = set(
                BotUser.all_tenants.filter(
                    pk__in=shell_ids, ayla_user_id__isnull=False
                ).values_list("ayla_user_id", flat=True)
            )
            return sum(soft_delete_inferences_for_withdrawal(uid) for uid in ayla_user_ids)
    except Exception:  # noqa: BLE001 — стирание не откатывает отзыв; см. докстринг
        logger.exception(
            "consent.preference_inference.derived_memory_erase_failed bot_user=%s",
            bot_user.pk,
        )
        return 0


def is_granted(bot_user: "BotUser") -> bool:
    """Действует ли согласие у человека сейчас — по всем его оболочкам."""
    from apps.consent.services import has_person_consent

    return has_person_consent(bot_user, PREFERENCE_INFERENCE)
