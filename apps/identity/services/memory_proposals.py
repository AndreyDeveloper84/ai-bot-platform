"""Предположения о предпочтениях — подтвердить, исправить, показать (DRF-2781, Ф4a-3).

Решение владельца 05.10.2026 (``Ayla/docs/OWNER_DECISIONS_MEMORY_AND_CONSENT_2026-10-05.md``
§«Умная память Ф4»):

    «Предположение НЕ становится фактом и НЕ ограничивает выбор клиента без
    подтверждения. … В разделе памяти разделить „Вы сообщили“ vs „Ayla
    предлагает запомнить“. Действия: подтвердить / исправить / удалить.»

Состояния зелёной записи для человека (:func:`fact_state`):

* ``said`` — сказал сам (``source='explicit'``, ``provenance='user_stated'``);
* ``proposed`` — Ayla предположила, человек ещё не ответил
  (``source='inferred'``, ``provenance`` NULL, ``status='active'``);
* ``confirmed`` — Ayla предположила, человек подтвердил
  (``provenance='user_confirmed_inference'``, ``status='active'``);
* ``reconfirm`` — подтверждённое, у которого истёк срок 180 дней: свип
  DRF-2782 ставит ``status='expired'``, и оно ждёт повторного подтверждения.

**Поверхность** — промпт консьержа и чатовое «покажи» — получает только
``said`` и ``confirmed`` (:func:`is_surfaceable`). Это и есть «не становится
фактом без подтверждения»: неподтверждённое и просроченное в разговор не
идут. Экран памяти показывает все состояния — он и есть место, где человек
отвечает на предложение.

Подтверждение меняет ТУ ЖЕ строку (``provenance``, срок, ``status``,
``updated_at``) — так договорено со свипом DRF-2782. Исправление — новая
сказанная строка с тем же ключом и вытеснение предложения
(``supersession_reason='corrected'``). Удаление — существующий путь экрана.

Сроки — решение владельца, PENDING ратификации: неподтверждённое 30 дней
(:data:`memory_writer.INFERRED_UNCONFIRMED_TERM_DAYS`), подтверждённое — 180.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.identity.models import MemoryEntry

logger = logging.getLogger(__name__)

#: Срок подтверждённого предположения (решение владельца 05.10, PENDING).
INFERRED_CONFIRMED_TERM_DAYS = 180

STATE_SAID = "said"
STATE_PROPOSED = "proposed"
STATE_CONFIRMED = "confirmed"
STATE_RECONFIRM = "reconfirm"

_PURPOSE_CORRECT = "miniapp_memory_proposal_correct"


class ProposalError(Exception):
    """Отказ с именем — ручка переводит ``code`` в ответ."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def fact_state(entry: MemoryEntry) -> str | None:
    """Состояние строки для человека, или ``None`` — её не показывают вовсе.

    ``signal`` (писателя нет) и вытесненные строки состояния не имеют.
    """
    if entry.status == MemoryEntry.STATUS_SUPERSEDED:
        return None
    if entry.source == MemoryEntry.SOURCE_EXPLICIT:
        return STATE_SAID
    if entry.source != MemoryEntry.SOURCE_INFERRED:
        return None
    if entry.provenance == MemoryEntry.PROVENANCE_USER_CONFIRMED_INFERENCE:
        return STATE_RECONFIRM if entry.status == MemoryEntry.STATUS_EXPIRED else STATE_CONFIRMED
    if entry.provenance is None and entry.status in (MemoryEntry.STATUS_ACTIVE, None):
        return STATE_PROPOSED
    return None


def is_surfaceable(entry: MemoryEntry, *, inference_allowed: bool) -> bool:
    """Может ли строка попасть в разговор.

    Сказанное — всегда (у него своё основание). Подтверждённое предположение —
    только пока действует согласие на предположения: решение владельца «при
    отзыве — сразу прекратить использование», и этот стоп не должен зависеть
    от того, успело ли стирание производной (DRF-2783) отработать.
    """
    state = fact_state(entry)
    if state == STATE_SAID:
        return True
    return state == STATE_CONFIRMED and inference_allowed


def inference_use_allowed(user_id: uuid.UUID) -> bool:
    """Действует ли согласие на предположения у человека с этим ``ayla_user_id``.

    Память ключуется ``ayla_user_id``, согласие висит на оболочках ``BotUser``;
    достаточно действующего согласия на любой из них (выдача и отзыв идут по
    всем оболочкам человека). Сбой чтения — запрещено.
    """
    try:
        from apps.consent.preference_inference import is_granted
        from apps.identity.models import BotUser

        shell = BotUser.all_tenants.filter(ayla_user_id=user_id).first()
        return shell is not None and is_granted(shell)
    except Exception:  # noqa: BLE001 — a failed consent read must not open the gate
        logger.exception("identity.memory.inference_consent_read_failed user=%s", user_id)
        return False


def _live_inferred(user_id: uuid.UUID, entry_id: uuid.UUID) -> MemoryEntry:
    from apps.identity.services.memory_reader import read_green_entries

    entry = next((e for e in read_green_entries(user_id) if e.id == entry_id), None)
    if entry is None or entry.source != MemoryEntry.SOURCE_INFERRED:
        raise ProposalError("not_found")
    return entry


def confirm_proposal(*, bot_user: Any, user_id: uuid.UUID, entry_id: uuid.UUID) -> MemoryEntry:
    """Подтвердить предположение: оно становится ``user_confirmed_inference`` на 180 дней.

    Принимает предложенное и подтверждённое-просроченное (повторное
    подтверждение). Требует действующего согласия на предположения: после
    отзыва предположения не используются (решение владельца), и
    подтверждение их в оборот не возвращает.

    Raises:
      ProposalError: ``not_found`` — не своё, не живое, не предположение;
        ``not_a_proposal`` — уже подтверждено и не просрочено;
        ``consent_required`` — согласия на предположения нет.
    """
    from apps.consent.preference_inference import is_granted

    entry = _live_inferred(user_id, entry_id)
    state = fact_state(entry)
    if state not in (STATE_PROPOSED, STATE_RECONFIRM):
        raise ProposalError("not_a_proposal")
    if not is_granted(bot_user):
        raise ProposalError("consent_required")

    now = timezone.now()
    with transaction.atomic():
        MemoryEntry.objects.filter(pk=entry.pk).update(
            provenance=MemoryEntry.PROVENANCE_USER_CONFIRMED_INFERENCE,
            status=MemoryEntry.STATUS_ACTIVE,
            expires_at=now + timedelta(days=INFERRED_CONFIRMED_TERM_DAYS),
            updated_at=now,
        )
    entry.refresh_from_db()
    logger.info(
        "identity.memory.proposal_confirmed entry=%s reconfirm=%s",
        entry.pk,
        state == STATE_RECONFIRM,
    )
    return entry


def correct_proposal(
    *, bot_user: Any, user_id: uuid.UUID, entry_id: uuid.UUID, value: str
) -> MemoryEntry:
    """Исправить предположение: человек называет своё значение.

    Новое значение — его слова, поэтому пишется сказанным фактом
    (``explicit`` / ``user_stated``) под основанием зелёной памяти, а
    предложение вытесняется с причиной ``corrected``. Согласие на
    предположения здесь не нужно: человек ничего не подтверждает, он говорит.

    Raises:
      ProposalError: ``not_found``; ``not_a_proposal`` — исправлять можно
        только неподтверждённое; ``bad_value``; ``consent_required`` — нет
        основания хранить сказанное (зелёная зона).
    """
    from apps.consent.memory import can_store_green_memory
    from apps.identity.services.memory_reader import get_or_create_personal_context
    from apps.identity.services.memory_writer import supersede_entries, write_entry

    cleaned = value.strip() if isinstance(value, str) else ""
    if not cleaned:
        raise ProposalError("bad_value")
    entry = _live_inferred(user_id, entry_id)
    if fact_state(entry) != STATE_PROPOSED:
        raise ProposalError("not_a_proposal")
    if not can_store_green_memory(bot_user):
        raise ProposalError("consent_required")

    content = dict(entry.content) if isinstance(entry.content, dict) else {}
    content["value"] = cleaned
    with transaction.atomic():
        said = write_entry(
            user_id=user_id,
            personal_context=get_or_create_personal_context(user_id),
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            source=MemoryEntry.SOURCE_EXPLICIT,
            kind=entry.kind,
            content=content,
            request_id=uuid.uuid4(),
            purpose=_PURPOSE_CORRECT,
        )
        if said is None:
            raise ProposalError("consent_required")
        supersede_entries(
            replaced_by=said, entries=[entry], reason=MemoryEntry.SUPERSESSION_CORRECTED
        )
    logger.info("identity.memory.proposal_corrected entry=%s said=%s", entry.pk, said.pk)
    return said
