"""Бот-половина удаления аккаунта — по просьбе исполнителя каталога (§7, D3, DRF-1725).

Каталог (``users/deletion_executor.py``) стирает свою половину в одной
транзакции и затем спрашивает бота: ``POST /api/v1/internal/privacy/account-deletion/``.
``COMPLETED`` в каталоге ставится **только** после нашего ``all_ok``;
любой другой ответ оставляет заявку ``PROCESSING`` и каталог спросит снова.
Поэтому здесь ничего не «пробуется»: каждый шаг либо сделан, либо назван
как несделанный.

Что делается для человека ``ayla_user_id`` / его оболочек ``external_user_ids``:

1. :func:`~apps.identity.services.privacy.delete_personal_data` на одной
   из оболочек (каскад C5 — person-level: память, согласия, PII оболочек,
   нити ассистента, диалог → ``ArchivedMessage``). Шаг 1 каскада в каталог
   НЕ ходит (DRF-2639): каталог к этому моменту уже стёр свою половину и
   переименовал прокси, и ``DELETE …/personal-data/`` получил бы не «стирать
   нечего», а ``unknown_actor`` 403 — заявка оставалась бы ``PROCESSING``
   и повторялась каждым тиком (900 с) без конца.
2. :func:`~apps.identity.services.deletion_gate.clear_deletion_flag` —
   флаг D2 снимается тем же ходом, что и ``COMPLETED`` в каталоге.

Оболочек нет вовсе (человек в боте не был) — ``all_ok=True`` с пустыми
шагами: бот-половины у такого человека нет, и это правда, а не заглушка.

# Доступ не переживает аккаунт (DRF-2894)

Шаг ``staff_access_revoke`` идёт ПЕРВЫМ — пока оболочки ещё опознаются:

* роли человека в салонах (``TenantStaff``) деактивируются тем же сервисом,
  что и отзыв с экрана администратора (:func:`staff_revoke.revoke_staff_access`):
  строки не удаляются, след «кто что держал и до когда» остаётся;
* карточка мастера отвязывается от оболочки, и с неё снимается всё, что
  указывает на человека как на учётную запись: ``ayla_user_id``, ник
  (``max_handle``), телефон приглашения (``raw["invite_phone"]``).

Замер до правки (``1ec444b7``): бот-половина отчитывалась успехом, а у мастера
оставались роль ``admin``, связь с карточкой и ник; поиск мастера, которым
пользуется кабинет, его находил. Матрица «забудь всё» называла писателем
снятия роли «каскад удаления аккаунта» — такого кода не было.

Чего шаг НЕ делает, намеренно:

* **имя на карточке и сама карточка** остаются: это запись салона о
  сотруднике, и что с ней делать после удаления аккаунта — решение
  владельца (вопрос в листе). Мастер остаётся в расписании салона — как и
  при обычном отзыве доступа;
* **роль владельца салона** не снимается: у салона один владелец, и только
  он выдаёт код владельца; снять её — оставить салон, в который никто не
  войдёт. Шаг называет это в ``detail`` (``owner_role_kept``) и пишет в лог;
  передача салона — отдельная операция, которой ещё нет.

«Удалить мои данные» (отзыв хранения) этот шаг не зовёт: там человек остаётся
сотрудником, и роль — доступ, а не память (решение владельца 20.09, §58).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django.db.models import Q

from apps.identity.models import BotUser
from apps.identity.services.deletion_gate import clear_deletion_flag
from apps.identity.services.privacy import delete_personal_data
from apps.integrations.ayla.user_proxy import parse_external_user_id
from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccountDeletionOutcome:
    all_ok: bool
    steps: list[dict] = field(default_factory=list)
    failed_steps: list[str] = field(default_factory=list)
    shells: int = 0
    flag_cleared: bool = False

    def as_payload(self) -> dict:
        return {
            "all_ok": self.all_ok,
            "steps": self.steps,
            "failed_steps": self.failed_steps,
            "shells": self.shells,
            "flag_cleared": self.flag_cleared,
        }


def shells_for(ayla_user_id: uuid.UUID, external_user_ids: list[str]) -> list[BotUser]:
    """Оболочки человека: по ``ayla_user_id`` и по каналу из ``bot:<channel>:<id>``."""
    ids: set[uuid.UUID] = set(
        BotUser.all_tenants.filter(ayla_user_id=ayla_user_id).values_list("id", flat=True)
    )
    for ext in external_user_ids:
        parsed = parse_external_user_id(ext)
        if parsed is None:
            continue
        channel, channel_user_id = parsed
        ids.update(
            BotUser.all_tenants.filter(
                channel=channel, channel_user_id=channel_user_id
            ).values_list("id", flat=True)
        )
    return list(BotUser.all_tenants.filter(id__in=ids).select_related("tenant").order_by("id"))


STAFF_ACCESS_STEP = "staff_access_revoke"
REVOKE_SURFACE = "account_deletion"

#: Что на карточке мастера указывает на человека как на учётную запись и
#: снимается вместе с аккаунтом. Имени здесь нет намеренно — см. докстринг.
CARD_INVITE_PHONE_KEY = "invite_phone"


def remove_staff_access(shells: list[BotUser], ayla_user_id: uuid.UUID, *, request_id: str) -> dict:
    """Снять роли и связь с карточкой мастера у удаляемого человека. Шаг каскада.

    Никогда не бросает: возвращает шаг ``{"step", "ok", "detail"}``. Сбой —
    ``ok=False``, и каталог спросит снова; повтор идемпотентен.
    """
    from apps.catalog.models import CatalogMaster
    from apps.identity.services.staff_revoke import OwnerRevokeRefused, revoke_staff_access
    from apps.tenancy.models import TenantStaff

    roles = cards = owner_kept = 0
    try:
        shell_ids = [shell.id for shell in shells]
        staffed = list(
            TenantStaff.all_tenants.filter(
                bot_user_id__in=shell_ids, deactivated_at__isnull=True
            ).select_related("tenant", "bot_user")
        )
        linked = list(
            CatalogMaster.all_tenants.filter(linked_bot_user_id__in=shell_ids).select_related(
                "tenant", "linked_bot_user"
            )
        )
        # Пара «салон, оболочка» — единица отзыва у ``revoke_staff_access``.
        pairs: dict[tuple[Any, Any], tuple[Tenant, BotUser]] = {}
        for row in staffed:
            pairs[(row.tenant_id, row.bot_user_id)] = (row.tenant, row.bot_user)
        for card in linked:
            if card.linked_bot_user is not None:
                pairs[(card.tenant_id, card.linked_bot_user_id)] = (
                    card.tenant,
                    card.linked_bot_user,
                )

        for tenant, bot_user in pairs.values():
            try:
                result = revoke_staff_access(
                    tenant=tenant,
                    bot_user=bot_user,
                    reason=f"account deletion {request_id}",
                    surface=REVOKE_SURFACE,
                )
            except OwnerRevokeRefused:
                owner_kept += 1
                logger.warning(
                    "identity.account_deletion.owner_role_kept request_id=%s tenant=%s — "
                    "владелец салона удалил аккаунт; роль не снята, нужна передача салона",
                    request_id,
                    tenant.slug,
                )
                continue
            roles += len(result.roles_revoked)

        # Карточки человека: те, что были привязаны к его оболочкам, и те, что
        # несут его ``ayla_user_id`` (связь могла быть снята раньше, а ключ
        # личности — остаться).
        with transaction.atomic():
            mine = CatalogMaster.all_tenants.select_for_update().filter(
                Q(pk__in=[card.pk for card in linked]) | Q(ayla_user_id=ayla_user_id)
            )
            for card in mine:
                if card.linked_bot_user_id in shell_ids:
                    # Связь осталась — это салон, где человек владелец, и роль
                    # не снята (см. выше). Карточка остаётся цельной: полу-
                    # отвязанная она не отвечала бы ни на один вопрос.
                    continue
                raw = dict(card.raw or {})
                had_phone = raw.pop(CARD_INVITE_PHONE_KEY, None) is not None
                if card.ayla_user_id is None and not card.max_handle and not had_phone:
                    continue
                card.ayla_user_id = None
                card.max_handle = ""
                card.raw = raw
                card.save(update_fields=["ayla_user_id", "max_handle", "raw"])
                cards += 1
    except Exception:  # noqa: BLE001 — шаг называет сбой, а не роняет каскад
        logger.exception(
            "identity.account_deletion.staff_access_revoke_failed request_id=%s", request_id
        )
        return {"step": STAFF_ACCESS_STEP, "ok": False, "detail": ""}

    detail = f"roles={roles} cards={cards}"
    if owner_kept:
        detail += f" owner_role_kept={owner_kept}"
    return {"step": STAFF_ACCESS_STEP, "ok": True, "detail": detail}


def execute_bot_half(
    *, ayla_user_id: uuid.UUID, external_user_ids: list[str], request_id: str
) -> AccountDeletionOutcome:
    shells = shells_for(ayla_user_id, external_user_ids)
    steps: list[dict] = []
    failed: list[str] = []
    access_step: dict | None = None

    if shells:
        # DRF-2894 — доступ снимается первым, пока оболочки ещё опознаются.
        access_step = remove_staff_access(shells, ayla_user_id, request_id=request_id)

    if shells:
        # Каскад — person-level (см. ``_person_shell_ids``): одной оболочки
        # достаточно, остальные он находит сам по каналу и ``ayla_user_id``.
        # DRF-2639: the catalog has already erased its half and renamed the
        # proxies — its own erasure is not asked for again (see the kwarg).
        result = delete_personal_data(
            shells[0], erased_by_catalog=True, catalog_request_id=request_id
        )
        steps = [{"step": s.step, "ok": s.ok, "detail": s.detail} for s in result.steps]
        failed = list(result.failed_steps)

    if access_step is not None:
        steps.insert(0, access_step)
        if not access_step["ok"]:
            failed.insert(0, STAFF_ACCESS_STEP)

    flag_cleared = False
    if not failed:
        flag_cleared = clear_deletion_flag(ayla_user_id)

    outcome = AccountDeletionOutcome(
        all_ok=not failed,
        steps=steps,
        failed_steps=failed,
        shells=len(shells),
        flag_cleared=flag_cleared,
    )
    logger.info(
        "identity.account_deletion.bot_half request_id=%s ayla_user_id=%s shells=%d all_ok=%s failed=%s",
        request_id,
        ayla_user_id,
        len(shells),
        outcome.all_ok,
        failed,
    )
    return outcome
