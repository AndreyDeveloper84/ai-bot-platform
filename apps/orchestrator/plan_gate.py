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

#: Версия текста у старых записей согласия бывает пустой. Каталогу уходит
#: явная метка, а не пустая строка: согласие человек дал, отсутствие версии —
#: не его вина и не отказ.
UNVERSIONED = "unversioned"


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


def plan_consent_basis(bot_user: Any) -> dict[str, str] | None:
    """Утверждение основания для каталога — или ``None``, когда утверждать нечего.

    Каталог — вторая линия того же правила. Реестра согласий у него нет,
    поэтому каждый вызов, который план обрабатывает, несёт вид согласия,
    версию текста и время выдачи ДЕЙСТВУЮЩЕЙ записи. Время нужно каталогу для
    сравнения с известным ему отзывом: отзыв побеждает, только если он позже.

    Запись — самая поздняя действующая у человека по всем оболочкам: та же,
    по которой открывает :func:`apps.consent.services.has_person_consent`.
    Сбой чтения — ``None``: без утверждения каталог откажет сам.
    """
    try:
        from apps.consent.models import ConsentRecord
        from apps.consent.services import person_channel_shells

        consent_type = ConsentRecord.ConsentType.PERSONAL_DATA.value
        row = (
            ConsentRecord.all_tenants.filter(
                bot_user__in=person_channel_shells(bot_user),
                consent_type=consent_type,
                granted=True,
                withdrawn_at__isnull=True,
            )
            .order_by("-captured_at")
            .values_list("captured_at", "document_version")
            .first()
        )
    except Exception:  # noqa: BLE001 — fail-closed: утверждать нечего
        logger.exception("orchestrator.plan_gate.basis_read_failed")
        return None
    if row is None or row[0] is None:
        return None
    return {
        "type": consent_type,
        "document_version": str(row[1] or "").strip() or UNVERSIONED,
        "granted_at": row[0].isoformat(),
    }


__all__ = [
    "PLAN_BASIS_UNAVAILABLE",
    "PLAN_CONSENT_REQUIRED",
    "PLAN_DELETION_REQUESTED",
    "REFUSALS",
    "UNVERSIONED",
    "plan_consent_basis",
    "plan_processing_refusal",
]
