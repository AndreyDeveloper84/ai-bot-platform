"""Пре-чек входа в салонный бот — пять проверок и три исхода (DRF-2113, §50 п.1–3).

Салонный бот — только для персонала (владелец, администратор, мастер).
Слово владельца 19.09: «в салонный бот должны писать только мастера».
До того, как человеку показать рабочее меню, бот проверяет по порядку:

1. **кто вошёл через MAX** — личность события (``channel_user_id``);
2. **связана ли личность с учётной записью** — у рабочей строки владельца /
   администратора / ресепшена — ``BotUser.ayla_user_id`` реального сорта
   (``ayla_user_id_is_proxy is False``; прокси и NULL — не связь), у мастера —
   его карточка связана с Ayla (``master_state.LINKED_TO_AYLA``: ключ на
   карточке и связь не в ожидании — то же условие, без которого карточка
   не продаётся, DRF-1540);
3. **к какому салону есть доступ** и 4. **какая роль** — рабочая строка
   человека (:func:`~apps.identity.services.bot_user_resolver.resolve_working_bot_user`:
   активный ``TenantStaff`` или живая карточка мастера; несколько салонов —
   «какой салон?», как раньше);
5. **активен ли салон** — ``Tenant.is_active``.

Проверки 3 и 4 — одно чтение (строку выбирает роль), поэтому связь с
каталогом (2) проверяется у уже найденной рабочей строки: у человека без
строки связи нет по построению, и он — незнакомец. Активность салона —
последней, как у владельца.

Исходы для человека: ``STAFF`` (рабочее меню по роли), ``NOT_LINKED``
(«Доступ к салону ещё не подключён…» + «Повторить проверку» / «Обратиться в
поддержку» — не админка, которая ответит 403/500), ``STRANGER`` («Это рабочий
бот салона…» + ссылка на клиентского бота). ``SALON_INACTIVE`` и
``NO_IDENTITY`` — именованные отказы с тем же честным состоянием: код у
вердикта свой, текст — рядом с исходом в ``salon_handler``.

Пре-чек ничего не создаёт: у незнакомца строки ``BotUser`` не появляется
(владелец D2 12.09 → б), у рабочей строки может обновиться только ключ
личности — тем же писателем, что и на первом зависимом действии
(:func:`~apps.identity.services.ayla_link.ensure_ayla_link`, DRF-1035).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

STAFF = "STAFF"
NO_IDENTITY = "NO_IDENTITY"
STRANGER = "STRANGER"
NOT_LINKED = "NOT_LINKED"
SALON_INACTIVE = "SALON_INACTIVE"

#: Порядок проверок — тот, в котором вердикты выносятся.
CHECK_ORDER: tuple[str, ...] = ("identity", "salon_and_role", "catalog_link", "salon_active")

#: Почему ``NOT_LINKED`` — для лога и для «Повторить проверку».
REASON_NO_AYLA_USER = "no_ayla_user_id"
REASON_PROXY_ONLY = "proxy_identity"
REASON_MASTER_CARD_UNLINKED = "master_card_unlinked"

#: Чем пре-чек назвал себя каталогу, когда спрашивал личность.
LINK_TRIGGER = "salon_entry"


@dataclass(frozen=True)
class EntryVerdict:
    """Результат пре-чека: код + то, что нашлось по дороге."""

    code: str
    tenant: Any = None
    bot_user: Any = None
    role_ctx: Any = None
    reason: str = ""

    @property
    def is_staff(self) -> bool:
        return self.code == STAFF


def precheck(event: Any, *, chosen_slug: str | None = None) -> EntryVerdict:
    """Пять проверок по порядку; ``SalonChoiceRequired`` пробрасывается наверх.

    ``chosen_slug`` — салон, который человек выбрал кнопкой «какой салон?»
    (DRF-1766); пробрасывается в резолвер рабочей строки как раньше.
    """

    from apps.identity.services.bot_user_resolver import resolve_working_bot_user
    from apps.identity.services.role_resolver import resolve_role

    # 1. Личность.
    channel_user_id = str(getattr(event, "channel_user_id", "") or "").strip()
    if not channel_user_id:
        return EntryVerdict(code=NO_IDENTITY)

    # 3 + 4. Салон и роль — рабочая строка; нет строки — незнакомец.
    working = resolve_working_bot_user(
        channel_user_id, surface="salon_bot", chosen_slug=chosen_slug
    )
    if working is None:
        return EntryVerdict(code=STRANGER)
    role_ctx = resolve_role(working)
    if role_ctx.primary_role == "customer":
        # Роль ушла между резолвом и здесь (отзыв наперегонки с сообщением).
        return EntryVerdict(code=STRANGER)

    # 2. Связь с каталогом — у найденной строки.
    reason = unlinked_reason(working, role_ctx, tenant=working.tenant)
    if reason:
        logger.info(
            "channels.max.salon.entry.not_linked reason=%s role=%s tenant=%s",
            reason,
            role_ctx.primary_role,
            str(working.tenant_id),
        )
        return EntryVerdict(
            code=NOT_LINKED,
            tenant=working.tenant,
            bot_user=working,
            role_ctx=role_ctx,
            reason=reason,
        )

    # 5. Активен ли салон.
    if not getattr(working.tenant, "is_active", True):
        return EntryVerdict(
            code=SALON_INACTIVE, tenant=working.tenant, bot_user=working, role_ctx=role_ctx
        )

    return EntryVerdict(code=STAFF, tenant=working.tenant, bot_user=working, role_ctx=role_ctx)


def unlinked_reason(bot_user: Any, role_ctx: Any, *, tenant: Any) -> str:
    """Пусто — связь есть; иначе именованная причина ``NOT_LINKED``."""

    if getattr(role_ctx, "is_master", False) and not (
        getattr(role_ctx, "is_admin", False)
        or getattr(role_ctx, "is_owner", False)
        or getattr(role_ctx, "is_receptionist", False)
    ):
        return _master_unlinked_reason(role_ctx, tenant=tenant)
    return _account_unlinked_reason(bot_user)


def _account_unlinked_reason(bot_user: Any) -> str:
    """Владелец / администратор / ресепшен: реальная привязка ``ayla_user_id``.

    Спрашивает каталог тем же писателем, что и первое зависимое действие
    (DRF-1035); реальная привязка кэшируется им же, прокси и «неизвестно»
    переспрашиваются — ровно то, что делает «Повторить проверку».
    """

    from apps.identity.services.ayla_link import ensure_ayla_link

    try:
        ensure_ayla_link(bot_user, trigger=LINK_TRIGGER)
    except Exception:  # noqa: BLE001 — сбой резолва = «связи не доказано», не 500
        logger.exception("channels.max.salon.entry.link_resolve_failed")
    if getattr(bot_user, "ayla_user_id", None) is None:
        return REASON_NO_AYLA_USER
    if getattr(bot_user, "ayla_user_id_is_proxy", None) is not False:
        return REASON_PROXY_ONLY
    return ""


def _master_unlinked_reason(role_ctx: Any, *, tenant: Any) -> str:
    """Мастер: карточка связана с Ayla — тем же предикатом, что и продажа (DRF-1540).

    Читается в тенанте карточки (граница MKT1: сквозное чтение каталога —
    только у витрины).
    """

    master_id = getattr(role_ctx, "master_id", None)
    if master_id is None:
        return REASON_MASTER_CARD_UNLINKED
    from apps.catalog.master_state import LINKED_TO_AYLA
    from apps.catalog.models import CatalogMaster
    from apps.tenancy.context import tenant_scope

    with tenant_scope(tenant):
        linked = CatalogMaster.objects.filter(pk=master_id).filter(LINKED_TO_AYLA).exists()
    return "" if linked else REASON_MASTER_CARD_UNLINKED


__all__ = [
    "CHECK_ORDER",
    "EntryVerdict",
    "NOT_LINKED",
    "NO_IDENTITY",
    "SALON_INACTIVE",
    "STAFF",
    "STRANGER",
    "precheck",
    "unlinked_reason",
]
