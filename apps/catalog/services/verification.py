"""Ручная верификация приглашения мастера — одно место на два экрана.

DRF-1553. До этой задачи верификация жила ТЕЛОМ действия админки
каталога (``apps/catalog/admin.py``, ``verify_masters``): смена
``invite_status``, запись в журнал и подсчёт пропущенных были написаны
внутри метода, принимающего ``request`` и ``queryset``. Позвать это с
другого экрана было нельзя — только скопировать.

Копировать здесь запрещено предметно, а не из вкуса. 07.09.2026 в
PR #1396 нашлась ЧЕТВЁРТАЯ копия лестницы причин невидимости, и копия
успела начать врать: несвязанный с Ayla мастер подписывался «неактивна
по данным синхронизации», и владелица салона шла чинить активность,
которая была в порядке. Вторая копия верификации разошлась бы так же —
но уже в журнале, то есть в единственном следе, по которому потом
разбирают, кто и что изменил.

Поэтому логика переехала сюда целиком, а действие админки стало тонкой
обёрткой: оно решает, ЧТО выбрано и КОМУ сказать результат, а КАК
верифицируют — знает только этот модуль.

Журнал пишет тоже сервис, а не вызывающий
-----------------------------------------
Запись ``LogEntry`` — часть верификации, а не украшение вокруг неё:
именно её ``apps.adminconsole.journal`` разворачивает в ``AuditLog``, и
именно на неё смотрит DRF-1496, требуя «след с автором». Оставить её
вызывающему значило бы разрешить второму экрану верифицировать молча
или другими словами — ровно то расхождение, которого задача избегает.

Вызов повторяет ``ModelAdmin.log_change`` дословно (``log_actions`` с
``single_object=True``), поэтому строка журнала от кнопки на экране
подключения и строка от действия в админке каталога неотличимы:
одинаковый автор, тип объекта, ``action_flag`` и текст. Паритет
проверяется по факту — сравнением самих строк журнала, а не тем, что
«зовётся тот же код» (``test_verification_parity.py``).

Третий экран — владелица салона, и автор у него другого типа
--------------------------------------------------------------
DRF-1597, решение владельца 08.09.2026: «надо дать возможность
пользователю самому выставлять статусы». До этой задачи оба экрана
верификации жили в Django-админке, то есть подтвердить мастера мог
только человек с доступом к серверу. Владелица салона, заведшая мастера,
не могла ничего — и не узнавала, что нужен второй шаг.

Третий вход добавлен здесь же, а не рядом, потому что менять
``invite_status`` по-прежнему обязано одно место. Разошлись только
АВТОРЫ, и разошлись по-настоящему: у админки автор — ``auth.User``
(``LogEntry.user_id`` это FK на него), у Mini App — ``BotUser`` с UUID,
который в ``LogEntry`` не ложится вовсе. Поэтому запись следа вынесена в
параметр :func:`_verify`, а текст следа остался один на все три экрана —
:data:`JOURNAL_MESSAGE`. Обе ветки в итоге пишут в ``AuditLog``: одна
через ``LogEntry`` и ``apps.adminconsole.journal``, вторая напрямую
``write_audit``. Хранилище общее, автор честный.

Живой токен — граница между двумя путями, а не перестраховка
---------------------------------------------------------------
DRF-1597, решение владельца 08.09.2026, дословно:

    «Согласие остаётся обязательным только для приглашённых извне, тем,
    кому реально выписывают токен».

Отсюда два пути, и различает их ровно одно поле.

**Салонный мастер.** Заведена владелицей в админке Ayla, приезжает
синхронизацией. Токена ей никто не выписывал и приглашения не отправлял:
сторона Ayla не шлёт ничего (замер 08.09.2026 — ни письма, ни SMS, ни
токена), а ``admin_api/views_invite.py`` перестал слать личное сообщение
решением владельца §44.4. Принимать ей нечего — и подтверждение
владелицей не обходит ничьего согласия, а является ШТАТНЫМ путём:
владелица отвечает за своих людей.

**Приглашённая извне.** Путь ``masters/invite/`` выписывает НАСТОЯЩИЙ
токен настоящему человеку. У такой строки согласие существует как
механизм, она ждёт именно её нажатия, и подтвердить его за неё — обход
согласия, тот же класс, что обход HEALTH в §25 п.6.

Поэтому :func:`has_live_invite` — не осторожность, а сама граница. Такие
строки :func:`_verify` пересчитывает в ``blocked`` и не трогает на всех
трёх экранах: граница проходит по СТРОКЕ, а не по тому, кто нажал
кнопку.

**Автопроставление при синхронизации по-прежнему запрещено** и решением
владельца не ослабляется. Оно про осознанное действие владелицы, а не
про статус, проставленный молча: «человек согласился» и «за него решили»
— разное, а «за него решили молча» — третье и худшее.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from django.contrib.admin.models import CHANGE, LogEntry
from django.db.models import Q
from django.utils import timezone

from apps.audit.services import write_audit
from apps.catalog.models import CatalogMaster

#: Текст следа в журнале. Один шаблон на оба экрана.
#:
#: Формат сохранён с DRF-1496 дословно: по подстроке «Верификация
#: вручную» его ищет ``test_verify_action_is_journaled_with_author``, и
#: он же читается человеком в ``AuditLog``.
JOURNAL_MESSAGE = "Верификация вручную: приглашение «{old}» → «принято»."


#: Действие аудита для следа от владелицы салона (DRF-1597).
#:
#: Отдельный глагол, а не общий ``master.verified``: по нему потом
#: отличают подтверждение из Mini App от верификации оператором в
#: Django-админке, не сверяя типы автора. Текст следа при этом один —
#: :data:`JOURNAL_MESSAGE`.
AUDIT_ACTION = "master.invite_verified_by_salon"


@dataclass(frozen=True)
class VerificationOutcome:
    """Сколько мастеров верифицировано, пропущено и не отдано вовсе.

    ``blocked`` — строки с живым персональным приглашением (DRF-1597).
    Отдельное число, а не молчание и не ``skipped``: «уже принято» и «за
    неё нельзя» — два разных ответа человеку, и слить их значило бы
    сказать владелице, что мастер уже подтверждён, когда он ждёт
    СОБСТВЕННОГО нажатия.
    """

    verified: int
    skipped: int
    blocked: int = 0

    @property
    def total(self) -> int:
        return self.verified + self.skipped + self.blocked


def has_live_invite(master: CatalogMaster) -> bool:
    """Ждёт ли эта строка нажатия САМОГО мастера (DRF-1597).

    Три условия вместе, а не одно из них:

    * ``invite_status == PENDING`` — приглашение ещё в игре;
    * ``invite_token IS NOT NULL`` — оно кому-то выписано. Мастер,
      приехавший синхронизацией, токена не имеет вовсе: ``upserter``
      платформенных полей не трогает, и принимать ему нечего;
    * срок не истёк — протухшее приглашение уже никто не примет, и
      держать по нему строку заблокированной значило бы запереть мастера
      навсегда.

    Это ГРАНИЦА МЕЖДУ ДВУМЯ ПУТЯМИ, а не перестраховка: по решению
    владельца 08.09.2026 согласие обязательно ровно для приглашённых
    извне — «тем, кому реально выписывают токен». Токен и есть признак,
    по которому строка относится ко второму пути.

    Ровно эти строки :func:`_verify` не трогает НИ НА ОДНОМ экране.
    Граница проходит по строке, а не по тому, кто нажал кнопку: обход
    согласия не перестаёт им быть оттого, что кнопку нажал суперюзер.
    """

    if master.invite_status != CatalogMaster.InviteStatus.PENDING:
        return False
    if master.invite_token is None:
        return False
    if master.invite_expires_at is None:
        # Токен без срока — приглашение бессрочное, а не истёкшее.
        return True
    return master.invite_expires_at > timezone.now()


def live_invite_q() -> Q:
    """То же условие для выборки в базе — и оно обязано совпадать.

    Очередь на подтверждение считает строки этим ``Q``, а кнопка
    пропускает их :func:`has_live_invite`. Разойдись эти два определения
    — и очередь пообещала бы владелице мастеров, которых потом не
    подтвердит. Совпадение держится тестом
    ``test_live_invite_query_matches_predicate``: два определения одного
    условия — ровно та пятёрка расхождений, из-за которой появился
    ``apps/catalog/master_state.py``.
    """

    now = timezone.now()
    return (
        Q(invite_status=CatalogMaster.InviteStatus.PENDING)
        & Q(invite_token__isnull=False)
        & (Q(invite_expires_at__isnull=True) | Q(invite_expires_at__gt=now))
    )


def _verify(
    masters: Iterable[CatalogMaster],
    *,
    journal: Callable[[CatalogMaster, str], None],
) -> VerificationOutcome:
    """Один переход, три экрана. Расходится только запись следа.

    Идёт через ``save(update_fields=["invite_status"])``, а не через
    ``queryset.update()``: массовое обновление не зовёт ``save`` модели,
    а :meth:`CatalogMaster.save` штампует ``accepted_at`` в момент, когда
    строка впервые оказывается связанной и принятой (см. докстринг
    ``apps/catalog/master_state.py``). ``update()`` оставил бы
    ``accepted_at IS NULL`` у принятой строки — по докстрингу гейта это
    сигнал «кто-то записал состояние в обход модели».

    Уже принятые пропускаются, а не переписываются: повторный проход по
    той же выборке не обязан плодить строки журнала о несостоявшемся
    изменении.
    """

    verified = 0
    skipped = 0
    blocked = 0
    for master in masters:
        if master.invite_status == CatalogMaster.InviteStatus.ACCEPTED:
            skipped += 1
            continue
        if has_live_invite(master):
            blocked += 1
            continue
        old = master.get_invite_status_display()
        master.invite_status = CatalogMaster.InviteStatus.ACCEPTED
        master.save(update_fields=["invite_status"])
        journal(master, JOURNAL_MESSAGE.format(old=old))
        verified += 1
    return VerificationOutcome(verified=verified, skipped=skipped, blocked=blocked)


def verify_masters(masters: Iterable[CatalogMaster], *, user) -> VerificationOutcome:  # type: ignore[no-untyped-def]
    """Верификация с админского экрана: автор следа — ``auth.User``.

    ``user`` — автор следа (``request.user`` обоих админских экранов).
    Право на действие проверяет вызывающий: в админке каталога это
    ``permissions=["change"]``, на экране подключения — суперпользователь
    (OPEN_DECISIONS §27). Сервис прав не выдаёт и не расширяет.
    """

    return _verify(masters, journal=lambda m, message: _journal(m, user=user, message=message))


def verify_masters_by_salon(masters: Iterable[CatalogMaster], *, actor) -> VerificationOutcome:  # type: ignore[no-untyped-def]
    """Подтверждение владелицей салона из Mini App (DRF-1597).

    Тот же переход и тот же текст следа, что у админских экранов, —
    расходится только автор: здесь это ``BotUser``, у которого UUID, а
    ``LogEntry.user_id`` это FK на ``auth.User``, и UUID туда не ложится
    вовсе. Поэтому след пишется прямо в ``AuditLog`` через
    ``write_audit`` — то самое хранилище, в которое
    ``apps.adminconsole.journal`` разворачивает ``LogEntry`` админских
    экранов. Хранилище общее, автор честный.

    Право на действие проверяет вызывающий: эндпойнт отдаёт его только
    владелице салона. Сервис прав не выдаёт и не расширяет — но, в
    отличие от прав, границу согласия держит сам: строку с живым
    персональным приглашением не подтвердит и владелица.
    """

    return _verify(masters, journal=lambda m, message: _audit(m, actor=actor, message=message))


def _journal(master: CatalogMaster, *, user, message: str) -> None:  # type: ignore[no-untyped-def]
    """Тот же вызов, что делает ``ModelAdmin.log_change``.

    Повторён здесь, а не вызван через ``ModelAdmin``, потому что второй
    экран живёт не в ``CatalogMasterAdmin`` и инстанса под рукой не
    имеет. Что именно повторено — видно построчно и держится тестом
    паритета: подмена ``log_change`` в Django изменит одну сторону, и
    сравнение строк журнала это покажет.
    """

    LogEntry.objects.log_actions(
        user_id=user.pk,
        queryset=[master],
        action_flag=CHANGE,
        change_message=message,
        single_object=True,
    )


def _audit(master: CatalogMaster, *, actor, message: str) -> None:  # type: ignore[no-untyped-def]
    """След владелицы салона — сразу в ``AuditLog``, минуя ``LogEntry``.

    ``change_message`` кладётся тем же текстом, что пишет админский
    журнал: строка следа читается человеком, и два разных текста об одном
    переходе — это два разных перехода в глазах того, кто потом
    разбирается.
    """

    write_audit(
        AUDIT_ACTION,
        target="catalog.catalogmaster",
        target_id=master.pk,
        actor_id=getattr(actor, "pk", None),
        payload={"change_message": message, "master_name": master.name},
    )


__all__ = [
    "AUDIT_ACTION",
    "JOURNAL_MESSAGE",
    "VerificationOutcome",
    "has_live_invite",
    "live_invite_q",
    "verify_masters",
    "verify_masters_by_salon",
]
