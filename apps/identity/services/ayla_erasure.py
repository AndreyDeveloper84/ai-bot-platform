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
    PersonalContextConfigError,
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

#: Синхронная попытка в каскаде и чате не ждёт всех повторов клиента (3 × 30 с на
#: DELETE и на чтение): человек ждёт ответа экрана, остальное повторит задание.
SYNC_RETRIES = 1
SYNC_TIMEOUT_SECONDS = 10

#: Аренда задания подметальщиком: пока идёт сеть, второй проход его не возьмёт.
#: Больше худшего времени одной попытки (2 вызова × 3 повтора × 30 с + паузы).
CLAIM_LEASE = timedelta(minutes=10)

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
    state = _persist(job, _attempt(job, client=client))
    return ErasureOutcome(state=state, job_id=job.pk)


def sweep_due_jobs(
    *, client: PersonalContextHttpClient | None = None, now: Any = None
) -> dict[str, int]:
    """Повторить просроченные открытые задания.

    Каждое берётся арендой в короткой транзакции (``select_for_update(skip_locked)``,
    срок перепроверяется под блокировкой, ``next_attempt_at`` сдвигается на
    :data:`CLAIM_LEASE`), сеть и запись итога — вне транзакции.
    """
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
                    .filter(
                        pk=job_id,
                        status=AylaErasureJob.Status.PENDING,
                        next_attempt_at__lte=timezone.now(),
                    )
                    .first()
                )
                if job is None:
                    summary["skipped_locked"] += 1
                    continue
                AylaErasureJob.objects.filter(pk=job.pk).update(
                    next_attempt_at=timezone.now() + CLAIM_LEASE
                )
            state = _persist(job, _attempt(job, client=client))
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
    for _ in range(2):
        existing = AylaErasureJob.objects.filter(ayla_user_id=ayla_user_id, status=pending).first()
        if existing is not None:
            # Новый законный запрос человека — новые попытки, а не последняя из
            # старых: иначе одна неудача сразу исчерпала бы задание (ревью S1).
            existing.attempts = 0
            existing.alerted_at = None
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
            # Одновременный запрос того же человека успел открыть задание первым —
            # перечитать; если его уже закрыли, открыть своё на втором круге.
            continue
    raise RuntimeError(f"AylaErasureJob: не удалось открыть задание для {ayla_user_id}")


def _follow_rebind(job: AylaErasureJob) -> None:
    """Перевесить задание на актуальный ключ субъекта, если прокси привязан (DRF-2309).

    Задание хранит ключ со времени создания. Если тогда это был ключ прокси, а
    каталог с тех пор привязал прокси к аккаунту, каждая попытка с ним получает
    403 (заголовок ``bot:…`` каталог разрешает в аккаунт) — задание не
    выздоравливает никогда. Поэтому перед попыткой:

    * оболочка уже держит другой ключ (перепривязана другим действием,
      DRF-1790) — он и берётся, без сети;
    * оболочка держит известный ключ прокси — переспрос ``ensure_ayla_link``;
    * иначе (реальный ключ, ключ неизвестного вида, оболочки нет) — как было.

    Смена ключа — условный UPDATE по открытому заданию. Если для нового ключа
    уже открыто своё задание (одно открытое на ключ), старое не перевешивается:
    стирание по новому ключу идёт тем заданием. Предел — старое задание само
    не закрывается.
    """
    from apps.identity.models import AylaErasureJob

    bot_user = job.bot_user
    if bot_user is None:
        return
    current = getattr(bot_user, "ayla_user_id", None)
    fresh = None
    if current and str(current) != str(job.ayla_user_id):
        fresh = current
    elif getattr(bot_user, "ayla_user_id_is_proxy", None) is True:
        from apps.identity.services.ayla_link import ensure_ayla_link

        fresh = ensure_ayla_link(bot_user, trigger="erasure_retry")
    if not fresh or str(fresh) == str(job.ayla_user_id):
        return
    try:
        with transaction.atomic():
            moved = AylaErasureJob.objects.filter(
                pk=job.pk, status=AylaErasureJob.Status.PENDING
            ).update(ayla_user_id=fresh)
    except IntegrityError:
        logger.warning(
            "identity.ayla_erasure.rebind_skipped job=%s reason=open_job_for_new_key", job.pk
        )
        return
    if moved:
        logger.info("identity.ayla_erasure.rebound job=%s", job.pk)
        job.ayla_user_id = fresh


def _attempt(job: AylaErasureJob, *, client: PersonalContextHttpClient) -> str:
    """Одна попытка: DELETE, затем readback. Меняет поля задания, не сохраняет."""
    from apps.identity.models import AylaErasureJob

    _follow_rebind(job)
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
    except PersonalContextConfigError:
        kind = "config"
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
        return FAILED
    job.next_attempt_at = now + RETRY_DELAYS[job.attempts - 1]
    return STARTED


_PERSISTED_FIELDS = (
    "status",
    "attempts",
    "next_attempt_at",
    "last_error_kind",
    "external_user_id",
    "completed_at",
    "alerted_at",
)
_CLOSED_TO_STATE = {
    "completed": CONFIRMED,
    "superseded_by_account_deletion": SUPERSEDED,
    "failed": FAILED,
}


def _persist(job: AylaErasureJob, state: str) -> str:
    """Записать итог попытки — только поверх ещё открытого задания (ревью S2).

    Если задание закрыли, пока шла сеть (подметальщик подтвердил стирание или
    исчерпал повторы), итог этой попытки его не переоткроет и второй алерт не
    уйдёт; возвращается состояние из базы.
    """
    from apps.identity.models import AylaErasureJob

    fields = {name: getattr(job, name) for name in _PERSISTED_FIELDS}
    fields["updated_at"] = timezone.now()
    written = AylaErasureJob.objects.filter(pk=job.pk, status=AylaErasureJob.Status.PENDING).update(
        **fields
    )
    if not written:
        job.refresh_from_db()
        logger.warning(
            "identity.ayla_erasure.closed_meanwhile job=%s status=%s", job.pk, job.status
        )
        return _CLOSED_TO_STATE.get(job.status, STARTED)
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
        sent = page(
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
        if sent:
            # «Алерт отправлен» — только если он ушёл (ревью S4). Иначе поле пустое,
            # и исчерпанное задание без алерта видно запросом по alerted_at IS NULL.
            job.alerted_at = timezone.now()
            AylaErasureJob.objects.filter(pk=job.pk).update(alerted_at=job.alerted_at)
    return state
