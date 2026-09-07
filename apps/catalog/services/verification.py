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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from django.contrib.admin.models import CHANGE, LogEntry

from apps.catalog.models import CatalogMaster

#: Текст следа в журнале. Один шаблон на оба экрана.
#:
#: Формат сохранён с DRF-1496 дословно: по подстроке «Верификация
#: вручную» его ищет ``test_verify_action_is_journaled_with_author``, и
#: он же читается человеком в ``AuditLog``.
JOURNAL_MESSAGE = "Верификация вручную: приглашение «{old}» → «принято»."


@dataclass(frozen=True)
class VerificationOutcome:
    """Сколько мастеров верифицировано и сколько пропущено как уже принятые."""

    verified: int
    skipped: int

    @property
    def total(self) -> int:
        return self.verified + self.skipped


def verify_masters(masters: Iterable[CatalogMaster], *, user) -> VerificationOutcome:  # type: ignore[no-untyped-def]
    """Перевести приглашение в «принято» вручную, оставив след с автором.

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

    ``user`` — автор следа (``request.user`` обоих экранов). Право на
    действие проверяет вызывающий: в админке каталога это
    ``permissions=["change"]``, на экране подключения — суперпользователь
    (OPEN_DECISIONS §27). Сервис прав не выдаёт и не расширяет.
    """

    verified = 0
    skipped = 0
    for master in masters:
        if master.invite_status == CatalogMaster.InviteStatus.ACCEPTED:
            skipped += 1
            continue
        old = master.get_invite_status_display()
        master.invite_status = CatalogMaster.InviteStatus.ACCEPTED
        master.save(update_fields=["invite_status"])
        _journal(master, user=user, message=JOURNAL_MESSAGE.format(old=old))
        verified += 1
    return VerificationOutcome(verified=verified, skipped=skipped)


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


__all__ = ["JOURNAL_MESSAGE", "VerificationOutcome", "verify_masters"]
