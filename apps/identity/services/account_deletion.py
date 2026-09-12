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
   нити ассистента, диалог → ``ArchivedMessage``). Шаг 1 каскада ходит в
   каталог за ``…/personal-data/`` — каталог к этому моменту уже обезличил
   строку, и это идемпотентный ответ «стирать нечего».
2. :func:`~apps.identity.services.deletion_gate.clear_deletion_flag` —
   флаг D2 снимается тем же ходом, что и ``COMPLETED`` в каталоге.

Оболочек нет вовсе (человек в боте не был) — ``all_ok=True`` с пустыми
шагами: бот-половины у такого человека нет, и это правда, а не заглушка.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from apps.identity.models import BotUser
from apps.identity.services.deletion_gate import clear_deletion_flag
from apps.identity.services.privacy import delete_personal_data
from apps.integrations.ayla.user_proxy import parse_external_user_id

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


def execute_bot_half(
    *, ayla_user_id: uuid.UUID, external_user_ids: list[str], request_id: str
) -> AccountDeletionOutcome:
    shells = shells_for(ayla_user_id, external_user_ids)
    steps: list[dict] = []
    failed: list[str] = []

    if shells:
        # Каскад — person-level (см. ``_person_shell_ids``): одной оболочки
        # достаточно, остальные он находит сам по каналу и ``ayla_user_id``.
        result = delete_personal_data(shells[0])
        steps = [{"step": s.step, "ok": s.ok, "detail": s.detail} for s in result.steps]
        failed = list(result.failed_steps)

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
