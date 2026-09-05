"""Одно определение того, где мастер стоит между «приглашена» и «в архиве».

DRF-1506. До этого модуля определений было пять, и каждое игнорировало
свой столбец:

======================================================  ==================
место                                                   игнорировало
======================================================  ==================
``_MasterManager.bookable()``                           ``archived_at``
``apps.master_api.auth.require_master_init_data``       ``invite_status``
``apps.identity.services.role_resolver.resolve_role``   ``is_active``
``apps.admin_api.services.staff_roster._master_state``  ``linked_bot_user``
``apps.admin_api.services.master_deactivation``         ``linked_bot_user``
======================================================  ==================

Расхождение уже стоило одного инцидента (DRF-1080: мастер, принявшая
приглашение, была мастером для ``resolve_role`` и «неактивна» для
``require_master_init_data``), и его залатали двумя точечными починками
в месте потребления, а не в определении.

Здесь определение одно, и оно двухслойное — потому что вопросов на
самом деле два, и путать их дороже, чем различать.

Продаётся ли она клиенту (:func:`is_available`)
-----------------------------------------------
Каталожный вопрос: ``is_active`` И не в архиве И приглашение принято.
**Про ``linked_bot_user`` он не спрашивает — и это не забывчивость.**
На боевом контуре 05.09.2026 девять активных мастеров, у всех
``linked_bot_user IS NULL``: они приехали синхронизацией, а она
платформенных полей не трогает (см. докстринг ``CatalogMaster``).
Добавить сюда требование бот-аккаунта — значит немедленно снять с
продажи всех девятерых.

Приземлилась ли она в боте (:func:`is_landed`)
----------------------------------------------
Вопрос онбординга: всё вышеперечисленное И у неё есть привязанный
``BotUser`` И проставлен ``accepted_at``. Это то, чего требует
``docs/design/policies/master-onboarding-m0-m7.md`` §4.1 от состояния M1.

Приземление строго сильнее доступности: приземлившийся мастер проходит
все пять ворот, неприземлившийся не проходит ни одних из тех, что
спрашивают про личность. Синхронизированный мастер — третий случай,
названный и защищённый тестом: продаётся, но в мастер-приложение не
входит, потому что входить ей нечем.

``accepted_at`` держит модель
-----------------------------
:meth:`CatalogMaster.save` штампует ``accepted_at`` в момент, когда
строка впервые оказывается связанной и принятой, и никогда не стирает
его. Поэтому ``accepted_at IS NULL`` при связанной и принятой строке —
не «легаси», а сигнал, что кто-то записал состояние в обход модели.
Держать столбец в предикате стоит одного ``AND`` и ловит ровно этот
случай.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Literal, Mapping

from django.db.models import Q

#: Значение ``CatalogMaster.InviteStatus.ACCEPTED`` строкой.
#:
#: Литерал, а не импорт: ``apps.catalog.models`` импортирует этот модуль,
#: чтобы собрать ``bookable()``, и обратный импорт замкнул бы цикл на
#: загрузке приложения. Литерал закреплён за перечислением тестом
#: ``test_accepted_literal_matches_the_enum`` — разъехаться молча он не
#: может.
ACCEPTED: Final[str] = "accepted"

#: Состояние строки для ростера: где она между «приглашена» и «в архиве».
RoleState = Literal["active", "pending", "revoked"]

#: Мастер продаётся клиенту.
AVAILABLE = Q(is_active=True, archived_at__isnull=True, invite_status=ACCEPTED)

#: Мастер приземлилась в боте: продаётся И у неё есть личность.
LANDED = AVAILABLE & Q(linked_bot_user__isnull=False, accepted_at__isnull=False)


def _field(row: Any, name: str) -> Any:
    """Значение поля у модели или у словаря из ``.values()``.

    Ростер читает строки через ``.values()`` — там нет атрибутов, и
    заставлять его собирать объект ради предиката было бы лишним
    запросом на человека.
    """

    if isinstance(row, Mapping):
        return row[name]
    return getattr(row, name)


def is_available(row: Any) -> bool:
    """Продаётся ли этот мастер клиенту — построчный двойник :data:`AVAILABLE`."""

    return (
        bool(_field(row, "is_active"))
        and _field(row, "archived_at") is None
        and _field(row, "invite_status") == ACCEPTED
    )


def is_landed(row: Any) -> bool:
    """Приземлилась ли она — построчный двойник :data:`LANDED`.

    Принимает и модель, и словарь. У модели читает ``linked_bot_user_id``,
    чтобы не дёргать связанный ``BotUser`` ради проверки на NULL.
    """

    if not is_available(row):
        return False
    if isinstance(row, Mapping):
        linked = row.get("linked_bot_user_id", row.get("linked_bot_user"))
    else:
        linked = row.linked_bot_user_id
    return linked is not None and _field(row, "accepted_at") is not None


def master_state(*, archived_at: datetime | None, is_active: bool, invite_status: str) -> RoleState:
    """Где строка каталога стоит между «приглашена» и «в архиве».

    Порядок проверок — не стилистика, это и была вторая половина
    DRF-1506. Прежний порядок спрашивал ``not is_active`` раньше
    ``invite_status``, а путь приглашения пишет ``is_active=False``
    вместе с ``PENDING`` (``apps/admin_api/views_invite.py``) и пишет
    так намеренно: приглашённая мастер не должна попасть на витрину,
    пока не ответила. В итоге владелице салона только что приглашённого
    мастера показывали как «доступ отозван» — слово, за которым она
    пойдёт искать, кто отозвал, вместо того чтобы повторить приглашение.

    Архив идёт первым и остаётся первым: мастер, которую заархивировали
    с непринятым приглашением, ушла, а не ждёт.

    Всё, что короче ACCEPTED, — ``pending``: EXPIRED и CANCELLED это
    люди, которые не пришли, и «приглашена» — честное слово для строки,
    за которой никого нет.

    ``active`` совпадает с :func:`is_available` ровно, по построению —
    ростер и витрина не могут разойтись в том, кто активен.
    """

    if archived_at is not None:
        return "revoked"
    if invite_status != ACCEPTED:
        return "pending"
    if not is_active:
        return "revoked"
    return "active"


__all__ = [
    "ACCEPTED",
    "AVAILABLE",
    "LANDED",
    "RoleState",
    "is_available",
    "is_landed",
    "master_state",
]
