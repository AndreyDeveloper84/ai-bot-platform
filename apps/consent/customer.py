"""Согласия человека как один читаемый и управляемый ресурс (DRF-1520).

До этого модуля согласие можно было **дать** из мини-приложения и нельзя
было **посмотреть** целиком или **отозвать**. Согласие — юридический факт;
кто его дал, должен уметь отозвать тем же способом, каким давал. Ручки
отзыва не существовало ни одной, а тумблер проактивных подсказок
(``BotUser.proactive_messages_opt_out``) уже определял, напишет ли бот
человеку первым, но по HTTP не отдавался ни на чтение, ни на запись:
состоянием, которым человек управлять не может, управлялось его молчание.

### Что здесь есть

* :func:`read_consents` — состояние **всех** типов согласий, а не выборки.
  Список типов берётся из ``ConsentRecord.ConsentType``, поэтому новый тип
  появляется в ответе сам: выборочное чтение здесь невозможно по построению.
* :func:`set_proactive_hints` — «Подсказки Ayla». Это не ``ConsentRecord``, а
  колонка на ``BotUser``; она пишется по всем оболочкам человека и оставляет
  audit-строку.
* :func:`set_marketing` — маркетинговое согласие. **Один** источник правды,
  см. ниже.
* :func:`revoke_data_storage` — отзыв согласия на хранение данных: остановка
  дальнейшего необязательного хранения плюс предусмотренная процедура по уже
  накопленному.

### Главный источник правды для маркетингового согласия

Их было два, и они не были связаны ничем:

* ``UserPreferences.notify_promo`` — булева колонка, пишется ``PATCH /me``
  молча, без следа кто и когда;
* ``ConsentRecord(MARKETING)`` — append-only реестр с ``captured_at``,
  ``withdrawn_at``, источником, версией документа, событием и audit-строкой.
  До DRF-1520 в него не писал никто и не читал никто.

Главный — **реестр**. Колонка не умеет ответить на вопрос, ради которого
согласие вообще существует: кто и когда его дал и когда отозвал. Булев флаг
этого не хранит и хранить не может, а требование аудита выдачи и отзыва —
не украшение, а условие доказуемости.

``notify_promo`` остаётся, но перестаёт быть самостоятельной записью: он
**зеркало**, которое пишет только :func:`set_marketing`, в той же
транзакции, по всем оболочкам человека. ``update_profile`` больше не
присваивает его напрямую (см. ``apps.identity.services.profile``). Двух
пишущих путей нет — значит расходиться нечему; тест
``test_marketing_single_source_of_truth`` держит это утверждение.

### Почему по человеку, а не по строке

В пилоте у человека несколько ``BotUser``: мини-приложение резолвит строку
под ``MAX_BOT_TENANT_SLUG``, чат — под сентинелом ``global_bot``. Согласие
или опт-аут, записанные только на спросившую строку, невидимы поверхности,
которая читает. Поэтому запись идёт по всем оболочкам канала
(:func:`_person_shells`) — тем же, которые связывает каскад стирания (§8.4).
Чтение, наоборот, остаётся построчным и берёт ровно тот
предикат, которым ходит сторож рассылок
(:func:`apps.notifications.proactive.consent_blocker`): экран не может
показать «разрешено», пока поверхность отказывает.

### Чего здесь нет

* Выдачи согласия за человека. Ни одна функция модуля не принимает «чьё
  согласие»: субъект берётся из проверенной initData вызывающей ручкой.
* Содержимого медданных. Отдаётся факт наличия ``HEALTH``-согласия и его
  дата — сами данные особой категории (152-ФЗ ст. 10) наружу не идут.
* Телефона и других идентификаторов (DRF-1039).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.db import transaction

from apps.audit.services import write_audit
from apps.consent.models import ConsentRecord
from apps.consent.services import (
    has_global_consent,
    record_global_consent,
    withdraw,
)

if TYPE_CHECKING:
    from apps.identity.models import BotUser
    from apps.identity.services.privacy import DeleteCascadeResult

logger = logging.getLogger(__name__)

#: Версия раскрытия последствий отзыва согласия на хранение данных.
#: Поднимается ВМЕСТЕ с текстом последствий в мини-приложении. Отзыв,
#: присланный под неизвестной версией, отвергается: последствия должны быть
#: показаны ДО действия, и единственное, чем сервер может это проверить, —
#: версия текста, под которым человек нажал.
DATA_STORAGE_REVOCATION_DISCLOSURE_VERSION = "data-storage-revocation-v1"

#: Что именно делает отзыв с уже накопленным. Слаги совпадают с именами
#: шагов :func:`apps.identity.services.privacy.delete_personal_data` — чтобы
#: обещание на экране и код не могли разойтись; расхождение ловит
#: ``test_consequences_match_the_actual_cascade``.
DATA_STORAGE_REVOCATION_CONSEQUENCES = (
    "ayla_delete",
    "memory_delete",
    "consent_withdraw",
    "profile_pii_erase",
    "staff_assistant_erase",
    "dialogue_anonymize",
)

#: Что отзыв НЕ трогает и почему. Транзакционные записи хранятся по
#: обязанности оператора, а не по согласию, поэтому отзыв согласия их не
#: удаляет — и человеку это должно быть сказано до нажатия, а не после.
DATA_STORAGE_REVOCATION_RETAINED = ("bookings", "payments")

MARKETING_GRANT_SOURCE = "miniapp:profile_marketing_consent"
MARKETING_WITHDRAW_SOURCE = "miniapp:profile_marketing_consent_withdraw"
DATA_STORAGE_WITHDRAW_SOURCE = "miniapp:profile_data_storage_revoke"

_MARKETING = ConsentRecord.ConsentType.MARKETING.value
_PERSONAL_DATA = ConsentRecord.ConsentType.PERSONAL_DATA.value


def _person_shells(bot_user: "BotUser") -> list["BotUser"]:
    """Оболочки человека по его каналу. Fail-closed до самой строки.

    Ключ — ``(channel, channel_user_id)``, тот же, которым связывает
    оболочки каскад стирания: в пилоте мини-приложение резолвит строку под
    ``MAX_BOT_TENANT_SLUG``, а чат — под сентинелом ``global_bot``, и это
    две строки по ``unique_together (tenant, channel, channel_user_id)``.
    Опт-аут, поставленный на одной, не остановил бы планировщик, читающий
    другую.

    Намеренно **не** ``privacy.person_shell_ids``, хотя множество почти то
    же. Тот резолв, не найдя связки с Ayla, вызывает ``ensure_ayla_link`` и
    при необходимости заводит upstream-прокси — уместно для реализации
    права по 152-ФЗ, дико для переключения тумблера подсказок: обычный тап
    по настройке не должен ходить в сеть и тем более создавать учётную
    запись на той стороне. Отзыв согласия на хранение данных полное
    множество всё равно получает — его считает сам
    :func:`~apps.identity.services.privacy.delete_personal_data`.

    Пустой ``channel_user_id`` идентичностью не является: совпадение по
    нему собрало бы посторонних людей. Тогда — только сама строка.
    """
    from apps.identity.models import BotUser as BotUserModel

    channel = (bot_user.channel or "").strip()
    channel_user_id = (bot_user.channel_user_id or "").strip()
    if not channel or not channel_user_id:
        logger.warning(
            "consent.customer.no_channel_identity bot_user=%s — narrowing to the row",
            bot_user.id,
        )
        return [bot_user]
    shells = list(BotUserModel.all_tenants.filter(channel=channel, channel_user_id=channel_user_id))
    return shells or [bot_user]


def _active_record(bot_user: "BotUser", consent_type: str) -> ConsentRecord | None:
    """Действующая строка согласия аутентифицированной оболочки, либо None."""
    return (
        ConsentRecord.all_tenants.filter(
            bot_user=bot_user,
            consent_type=consent_type,
            granted=True,
            withdrawn_at__isnull=True,
        )
        .order_by("-captured_at")
        .first()
    )


def _consent_state(bot_user: "BotUser", consent_type: str) -> dict[str, Any]:
    """Состояние одного типа согласия так, как его видит читающая сторона."""
    granted = has_global_consent(bot_user, consent_type)
    record = _active_record(bot_user, consent_type) if granted else None
    return {
        "granted": granted,
        "granted_at": record.captured_at.isoformat() if record else None,
        "document_version": record.document_version if record else "",
    }


def read_consents(bot_user: "BotUser") -> dict[str, Any]:
    """Полное состояние согласий человека — все типы, без выборки.

    ``consents`` строится обходом ``ConsentRecord.ConsentType``: новый тип
    появляется в ответе сам, забыть его нельзя.

    ``data_storage`` — тот же ``personal_data``, но с двумя добавками,
    которые нужны экрану отзыва: денормализованная отметка
    ``BotUser.consent_at`` и раскрытие последствий отзыва.

    ``consent_at`` отдаётся **отдельным** полем, а не вместо ``granted``,
    и это не дублирование. Колонка ставится приветственным потоком, и
    ``withdraw()`` её не снимает, поэтому у отозвавшего она остаётся
    заполненной. На пилоте 2026-08-23 четыре из пяти строк с непустым
    ``consent_at`` уже отозвали ``personal_data``. Поведение решает реестр
    (его читает ``consent_blocker``) — он и стоит в ``granted``; колонка
    показана как есть, чтобы расхождение было видно, а не замазано.
    """
    consent_at = getattr(bot_user, "consent_at", None)
    return {
        "consents": {
            choice.value: _consent_state(bot_user, choice.value)
            for choice in ConsentRecord.ConsentType
        },
        "proactive_hints": {
            "enabled": not bool(getattr(bot_user, "proactive_messages_opt_out", False)),
        },
        "data_storage": {
            **_consent_state(bot_user, _PERSONAL_DATA),
            "consent_at": consent_at.isoformat() if consent_at else None,
            "revocation": {
                "disclosure_version": DATA_STORAGE_REVOCATION_DISCLOSURE_VERSION,
                "consequences": list(DATA_STORAGE_REVOCATION_CONSEQUENCES),
                "retained": list(DATA_STORAGE_REVOCATION_RETAINED),
            },
        },
    }


def set_proactive_hints(bot_user: "BotUser", *, enabled: bool) -> None:
    """Включить или выключить проактивные подсказки. Идемпотентно.

    Пишет ``proactive_messages_opt_out`` по всем оболочкам человека: чат и
    мини-приложение — разные строки, и опт-аут, поставленный на одной, не
    остановил бы планировщик, читающий другую.

    Оставляет audit-строку: тумблер решает, будет ли бот писать первым, и
    «кто и когда это переключил» — вопрос, на который придётся отвечать.
    """
    from apps.identity.models import BotUser as BotUserModel

    shells = _person_shells(bot_user)
    opt_out = not enabled
    with transaction.atomic():
        BotUserModel.all_tenants.filter(id__in=[s.id for s in shells]).update(
            proactive_messages_opt_out=opt_out
        )
    # Экземпляр вызывающего должен совпасть со строкой — ответ не имеет
    # права показать значение, которого в базе уже нет.
    bot_user.proactive_messages_opt_out = opt_out

    write_audit(
        "consent.proactive_hints_changed",
        target="BotUser",
        target_id=bot_user.id,
        actor_id=bot_user.id,
        payload={
            "actor": "customer",
            "enabled": enabled,
            "shells": len(shells),
        },
    )
    logger.info(
        "consent.customer.proactive_hints bot_user=%s enabled=%s shells=%d",
        bot_user.id,
        enabled,
        len(shells),
    )


def _mirror_notify_promo(shells: list["BotUser"], *, granted: bool) -> None:
    """Свести зеркало ``UserPreferences.notify_promo`` к состоянию реестра.

    Единственное место в платформе, которое присваивает эту колонку по
    воле человека. ``update_profile`` делегирует сюда; прямых присваиваний
    больше нет — иначе снова появилось бы два пишущих пути на один факт.
    """
    from apps.identity.models import UserPreferences

    for shell in shells:
        prefs, _ = UserPreferences.all_tenants.get_or_create(
            bot_user=shell,
            defaults={"tenant": shell.tenant},
        )
        if prefs.notify_promo != granted:
            prefs.notify_promo = granted
            prefs.save(update_fields=["notify_promo", "updated_at"])


def set_marketing(bot_user: "BotUser", *, granted: bool) -> None:
    """Выдать или отозвать маркетинговое согласие. Идемпотентно.

    Реестр — главный источник; ``notify_promo`` приводится к нему в той же
    транзакции. Порядок именно такой: сначала юридический факт, потом
    зеркало. Если зеркало упадёт, транзакция откатит оба — расхождение
    невозможно даже на секунду.
    """
    from apps.tenancy.context import tenant_scope

    shells = _person_shells(bot_user)
    with transaction.atomic():
        for shell in shells:
            if granted:
                # Идемпотентно: ``get_or_create`` по действующему гранту,
                # повторный тап не плодит строк.
                record_global_consent(
                    shell,
                    consent_type=_MARKETING,
                    source=MARKETING_GRANT_SOURCE,
                )
            else:
                # ``withdraw`` требует тенанта в scope и не удаляет строку —
                # проставляет ``withdrawn_at``, audit-trail остаётся.
                with tenant_scope(shell.tenant):
                    withdraw(
                        shell,
                        consent_type=_MARKETING,
                        source=MARKETING_WITHDRAW_SOURCE,
                    )
        _mirror_notify_promo(shells, granted=granted)
    logger.info(
        "consent.customer.marketing bot_user=%s granted=%s shells=%d",
        bot_user.id,
        granted,
        len(shells),
    )


def revoke_data_storage(bot_user: "BotUser") -> "DeleteCascadeResult":
    """Отозвать согласие на хранение данных — и сделать это отзывом.

    Порядок шагов нагружен смыслом:

    1. **Сначала снимается согласие.** ``withdraw_personal_data_for_bot_users``
       отзывает ``personal_data`` вместе с каскадом §8.4 (``health`` и все
       зоны памяти) по каждой оболочке человека. С этой секунды
       ``consent_blocker`` возвращает ``consent_withdrawn``, а сторож памяти
       и нутриционной поверхности отказывают: дальнейшее необязательное
       хранение и проактивные сообщения останавливаются немедленно.
       Маркетинговое согласие снимается тем же движением — вместе с
       зеркалом ``notify_promo``.
    2. **Потом запускается процедура по уже накопленному** —
       :func:`apps.identity.services.privacy.delete_personal_data`, та самая
       предусмотренная процедура C5.2: удаление персональных данных в Ayla,
       стирание памяти (green + tombstone ``forget_all``), стирание
       идентификаторов на оболочках, стирание диалога с ассистентом и
       анонимизация собственного диалога человека. Транзакционные записи
       (записи на визиты, платежи) хранятся по обязанности оператора и
       остаются — об этом сказано в ``DATA_STORAGE_REVOCATION_RETAINED``
       до нажатия, а не после.

    Порядок «сначала отзыв, потом процедура» — не косметика. Если Ayla
    недоступна, процедура вернёт частичный результат, но согласие уже
    снято и поверхности уже отказывают. Обратный порядок оставил бы
    человека с действующим согласием после неудачной попытки отозвать.

    Отзыв **не** закрывает человеку вход в приложение: ``deleted_at``
    здесь не ставится (в отличие от ``soft_delete_user``). Согласие можно
    дать заново — иначе «отзыв» был бы удалением аккаунта под другим
    именем.

    Идемпотентно: повторный отзыв снимает 0 грантов, каскад повторно
    отрабатывает вхолостую, состояние то же.

    Returns:
      Результат каскада: по шагу на строку, честно про неудачи.
    """
    from apps.consent.services import withdraw_personal_data_for_bot_users
    from apps.identity.models import BotUser as BotUserModel
    from apps.identity.services.privacy import delete_personal_data

    shells = _person_shells(bot_user)
    shell_ids = [s.id for s in shells]

    # 1 — остановка дальнейшего необязательного хранения.
    withdraw_personal_data_for_bot_users(
        BotUserModel.all_tenants.filter(id__in=shell_ids).select_related("tenant"),
        source=DATA_STORAGE_WITHDRAW_SOURCE,
    )
    set_marketing(bot_user, granted=False)

    write_audit(
        "consent.data_storage_revoked",
        target="BotUser",
        target_id=bot_user.id,
        actor_id=bot_user.id,
        payload={
            "actor": "customer",
            "shells": len(shells),
            "disclosure_version": DATA_STORAGE_REVOCATION_DISCLOSURE_VERSION,
        },
    )

    # 2 — предусмотренная процедура по уже накопленному. Пишет собственную
    # audit-строку ``privacy.personal_data_deleted`` со списком шагов.
    result = delete_personal_data(bot_user)
    logger.info(
        "consent.customer.data_storage_revoked bot_user=%s all_ok=%s failed=%s",
        bot_user.id,
        result.all_ok,
        result.failed_steps,
    )
    return result


__all__ = [
    "DATA_STORAGE_REVOCATION_CONSEQUENCES",
    "DATA_STORAGE_REVOCATION_DISCLOSURE_VERSION",
    "DATA_STORAGE_REVOCATION_RETAINED",
    "read_consents",
    "revoke_data_storage",
    "set_marketing",
    "set_proactive_hints",
]
