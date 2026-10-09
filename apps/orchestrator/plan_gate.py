"""DRF-2967 — основание для обработки плана нового механизма.

Замер 09.10 (настоящий ход чата Mini App, каталог подменён): человек без
согласия на хранение, человек с отозванным согласием и человек с живой
заявкой на удаление собирали и сохраняли план — проверки не было ни в боте,
ни в каталоге.

Правило одно на все входы, где план ОБРАБАТЫВАЕТСЯ — сборка, правка,
обсуждение с моделью, сохранение (чат и Mini App):

* у человека нет живой заявки на удаление — ни на одной его оболочке;
* у человека действует согласие на хранение (``personal_data``).

Чтение своего сохранённого плана («мой план») сюда НЕ входит: решение
владельца — отзыв согласия закрывает обработку, сохранённый план не
уничтожает.

Fail-closed: сбой чтения согласия или заявки — отказ с именем
:data:`PLAN_BASIS_UNAVAILABLE`, а не «можно».
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

PLAN_CONSENT_REQUIRED = "PLAN_CONSENT_REQUIRED"
PLAN_DELETION_REQUESTED = "PLAN_DELETION_REQUESTED"
PLAN_BASIS_UNAVAILABLE = "PLAN_BASIS_UNAVAILABLE"

REFUSALS = (PLAN_DELETION_REQUESTED, PLAN_CONSENT_REQUIRED, PLAN_BASIS_UNAVAILABLE)


def plan_processing_refusal(bot_user: Any) -> str | None:
    """Имя отказа — или ``None``, когда план этому человеку обрабатывать можно.

    Заявка на удаление проверяется первой: она закрывает и того, у кого
    согласие ещё действует.
    """
    try:
        from apps.consent.models import ConsentRecord
        from apps.consent.services import has_person_consent, person_channel_shells
        from apps.identity.services.deletion_gate import deletion_gate

        for shell in person_channel_shells(bot_user):
            if deletion_gate(getattr(shell, "ayla_user_id", None)).blocked:
                return PLAN_DELETION_REQUESTED
        if not has_person_consent(bot_user, ConsentRecord.ConsentType.PERSONAL_DATA.value):
            return PLAN_CONSENT_REQUIRED
    except Exception:  # noqa: BLE001 — fail-closed: основание не доказано
        logger.exception("orchestrator.plan_gate.basis_check_failed")
        return PLAN_BASIS_UNAVAILABLE
    return None


__all__ = [
    "PLAN_BASIS_UNAVAILABLE",
    "PLAN_CONSENT_REQUIRED",
    "PLAN_DELETION_REQUESTED",
    "REFUSALS",
    "plan_processing_refusal",
]
