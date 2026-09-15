"""Durable-удаление персональных данных в Ayla (DRF-1950, решение владельца M3).

    запрос → durable job → идемпотентность → retry/backoff →
    authoritative readback → completed

До readback человеку «удалено» не говорится: пока каталог не подтвердил
стирание чтением (C5.3 ``…/personal-data/erasure-status/``, каталог DRF-1984,
контракт AMD-020), каскад отвечает «удаление запущено», а задание повторяет
DELETE и чтение по расписанию. После исчерпания повторов — операционный алерт
(:func:`apps.observability.alerting.page`; получатель — из конфигурации, пока
L1 не решён — без получателя это WARNING в журнале).

Каждая попытка — «DELETE, затем readback», а не одно чтение: состояние
``not_erased`` бывает временным (служебные поля вопросов поверх tombstone,
новое поле модели), и повторный DELETE переводит его в стёртое.

Стирание подтверждено, только если общий вердикт ``erased`` и КАЖДАЯ личность
субъекта ``erased`` в состоянии ``absent``/``tombstone`` — противоречивому
ответу бот не верит (fail-closed).

403 после удаления аккаунта (D3). Исполнитель каталога отвязывает прокси
раньше, чем бот подтвердил свою половину, и дальше каталог отвечает боту 403
на всей поверхности — неотличимо от сломанного заголовка. Задание закрывается
как ``superseded_by_account_deletion`` (с аудитом, без алерта) только при
ПРОЧНОМ факте удаления аккаунта у бота: флаг заявки на удаление
(``UserPersonalContext.deletion_requested_at``) или след бот-половины D3 в
журнале (``privacy.account_deletion_bot_half`` по ``ayla_user_id``) — след
переживает снятие флага. Без факта 403 — обычная ошибка повтора.

Что НЕ закрыто здесь, названо:

* синхронная первая попытка в каскаде не берёт блокировку строки задания —
  одновременный проход подметальщика может сделать вторую попытку того же
  задания; DELETE идемпотентен, лишняя попытка лишь сдвигает счётчик;
* readback «стёрто» может быть перезаписан ночным выводом каталога
  (DRF-2005, P0 privacy) — это гонка на стороне каталога.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.services import write_audit
from apps.integrations.ayla.personal_context_client import (
    PersonalContextAuthError,
    PersonalContextError,
    PersonalContextHttpClient,
    PersonalContextNotFoundError,
    PersonalContextTransportError,
)
from apps.observability.alerting import page

if TYPE_CHECKING:
    from apps.identity.models import AylaErasureJob, BotUser

logger = logging.getLogger(__name__)

#: Решение владельца M3, дословно. Показывается человеку до readback.
ERASURE_STARTED_TEXT = "Удаление запущено. Оно завершится в установленный срок."

#: Паузы после неудачной попытки N (1-based): 1м, 5м, 15м, 1ч, 3ч, 6ч, 12ч, 24ч, 24ч.
#: Десятая неудача — исчерпание; всего ≈ 3 суток, внутри срока «не позднее 30 дней».
RETRY_DELAYS: tuple[timedelta, ...] = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(hours=1),
    timedelta(hours=3),
    timedelta(hours=6),
    timedelta(hours=12),
    timedelta(hours=24),
    timedelta(hours=24),
)
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1

CONFIRMED = "confirmed"
STARTED = "started"
SUPERSEDED = "superseded"
FAILED = "failed"

#: Состояния строки, которые каталог называет стёртыми (AMD-020).
_ERASED_ROWS = frozenset({"absent", "tombstone"})

#: След бот-половины D3 в журнале (``apps/identity/account_deletion_views.py``).
ACCOUNT_DELETION_AUDIT_ACTION = "privacy.account_deletion_bot_half"
SUPERSEDED_AUDIT_ACTION = "identity.ayla_erasure.superseded"

_SWEEP_BATCH = 100


def retry_enabled() -> bool:
    """Открыт ли durable-повтор (``AYLA_ERASURE_RETRY_ENABLED`` в settings)."""
    return bool(settings.AYLA_ERASURE_RETRY_ENABLED)


@dataclass(frozen=True)
class ErasureOutcome:
    """Исход синхронной попытки: ``confirmed`` | ``started`` | ``superseded`` | ``failed``."""

    state: str
    job_id: uuid.UUID


def readback_confirms(status: Any) -> bool:
    """Подтвердил ли каталог стирание: общий вердикт И каждая личность."""
    if not isinstance(status, dict) or status.get("erased") is not True:
        return False
    identities = status.get("identities")
    if not isinstance(identities, list) or not identities:
        return False
    return all(
        isinstance(item, dict)
        and item.get("erased") is True
        and item.get("context_row") in _ERASED_ROWS
        for item in identities
    )


def account_deletion_fact(ayla_user_id: uuid.UUID) -> bool:
    """Прочный факт удаления аккаунта у бота: флаг заявки или след бот-половины D3."""
    from apps.audit.models import AuditLog
    from apps.identity.models import UserPersonalContext

    if UserPersonalContext.objects.filter(
        user_id=ayla_user_id, deletion_requested_at__isnull=False
    ).exists():
        return True
    return AuditLog.all_tenants.filter(
        action=ACCOUNT_DELETION_AUDIT_ACTION, target_id=str(ayla_user_id)
    ).exists()


def erase_with_readback(
    *,
    bot_user: BotUser,
    ayla_user_id: uuid.UUID,
    external_user_id: str,
    source: str,
    client: PersonalContextHttpClient,
) -> ErasureOutcome:
    """Первая попытка синхронно: открыть (или переиспользовать) задание, DELETE → readback."""
    job = _open_job(
        bot_user=bot_user,
        ayla_user_id=ayla_user_id,
        external_user_id=external_user_id,
        source=source,
    )
    state = _attempt(job, client=client)
    _persist(job, state)
    return ErasureOutcome(state=state, job_id=job.pk)


def sweep_due_jobs(
    *, client: PersonalContextHttpClient | None = None, now: Any = None
) -> dict[str, int]:
    """Повторить просроченные открытые задания; каждое — под ``select_for_update(skip_locked)``."""
    from apps.identity.models import AylaErasureJob

    now = now or timezone.now()
    ids = list(
        AylaErasureJob.objects.filter(
            status=AylaErasureJob.Status.PENDING, next_attempt_at__lte=now
        )
        .order_by("next_attempt_at")
        .values_list("id", flat=True)[:_SWEEP_BATCH]
    )
    summary = {
        "due": len(ids),
        CONFIRMED: 0,
        "completed": 0,
        "rescheduled": 0,
        FAILED: 0,
        SUPERSEDED: 0,
        "skipped_locked": 0,
    }
    if not ids:
        return summary
    owns = client is None
    client = client or PersonalContextHttpClient()
    try:
        for job_id in ids:
            with transaction.atomic():
                job = (
                    AylaErasureJob.objects.select_for_update(skip_locked=True)
                    .filter(pk=job_id, status=AylaErasureJob.Status.PENDING)
                    .first()
                )
                if job is None:
                    summary["skipped_locked"] += 1
                    continue
                state = _attempt(job, client=client)
                _persist(job, state)
            if state == CONFIRMED:
                summary["completed"] += 1
            elif state == STARTED:
                summary["rescheduled"] += 1
            else:
                summary[state] += 1
    finally:
        if owns:
            client.close()
    logger.info(
        "identity.ayla_erasure.sweep due=%s completed=%s rescheduled=%s failed=%s superseded=%s skipped=%s",
        summary["due"],
        summary["completed"],
        summary["rescheduled"],
        summary[FAILED],
        summary[SUPERSEDED],
        summary["skipped_locked"],
    )
    return summary


# ── internals ────────────────────────────────────────────────────────────────


def _open_job(
    *, bot_user: BotUser, ayla_user_id: uuid.UUID, external_user_id: str, source: str
) -> AylaErasureJob:
    from apps.identity.models import AylaErasureJob

    pending = AylaErasureJob.Status.PENDING
    existing = AylaErasureJob.objects.filter(ayla_user_id=ayla_user_id, status=pending).first()
    if existing is not None:
        if external_user_id and not existing.external_user_id:
            existing.external_user_id = external_user_id
        return existing
    try:
        with transaction.atomic():
            return AylaErasureJob.objects.create(
                bot_user=bot_user,
                ayla_user_id=ayla_user_id,
                external_user_id=external_user_id,
                source=source,
                status=pending,
            )
    except IntegrityError:
        # Одновременный запрос того же человека успел открыть задание первым.
        return AylaErasureJob.objects.get(ayla_user_id=ayla_user_id, status=pending)


def _attempt(job: AylaErasureJob, *, client: PersonalContextHttpClient) -> str:
    """Одна попытка: DELETE, затем readback. Меняет поля задания, не сохраняет."""
    from apps.identity.models import AylaErasureJob

    now = timezone.now()
    job.attempts += 1
    ayla_id = str(job.ayla_user_id)
    try:
        try:
            client.delete_personal_data(ayla_user_id=ayla_id, external_user_id=job.external_user_id)
        except PersonalContextNotFoundError:
            pass  # старые версии каталога: «уже нет» — решает readback
        status = client.get_erasure_status(
            ayla_user_id=ayla_id, external_user_id=job.external_user_id
        )
    except PersonalContextAuthError:
        kind = "auth"
    except PersonalContextTransportError:
        kind = "transport"
    except PersonalContextError:
        kind = "upstream"
    else:
        if readback_confirms(status):
            job.status = AylaErasureJob.Status.COMPLETED
            job.completed_at = now
            job.next_attempt_at = None
            job.last_error_kind = ""
            job.external_user_id = ""
            return CONFIRMED
        kind = "not_confirmed"

    job.last_error_kind = kind
    if kind == "auth" and account_deletion_fact(job.ayla_user_id):
        job.status = AylaErasureJob.Status.SUPERSEDED
        job.completed_at = now
        job.next_attempt_at = None
        job.external_user_id = ""
        return SUPERSEDED
    if job.attempts >= MAX_ATTEMPTS:
        job.status = AylaErasureJob.Status.FAILED
        job.next_attempt_at = None
        job.alerted_at = now
        return FAILED
    job.next_attempt_at = now + RETRY_DELAYS[job.attempts - 1]
    return STARTED


def _persist(job: AylaErasureJob, state: str) -> None:
    job.save()
    if state == SUPERSEDED:
        write_audit(
            SUPERSEDED_AUDIT_ACTION,
            target="AylaErasureJob",
            target_id=job.pk,
            payload={"reason": "account_deletion", "source": job.source, "attempts": job.attempts},
        )
        logger.info("identity.ayla_erasure.superseded job=%s attempts=%s", job.pk, job.attempts)
    elif state == FAILED:
        logger.error(
            "identity.ayla_erasure.exhausted job=%s attempts=%s last=%s",
            job.pk,
            job.attempts,
            job.last_error_kind,
        )
        page(
            "error",
            "Удаление в Ayla не подтверждено: повторы исчерпаны",
            (
                f"Задание: {job.pk}\n"
                f"Источник: {job.source}\n"
                f"Попыток: {job.attempts}\n"
                f"Последняя причина: {job.last_error_kind}\n"
                "Что сделать: проверить доступность каталога; повторная постановка — "
                f"manage.py ayla_erasure_requeue {job.pk} (сухой прогон по умолчанию; "
                "на пилоте --apply выполняет главное окно по слову владельца)."
            ),
            dedup_key=f"ayla_erasure:{job.pk}",
        )
