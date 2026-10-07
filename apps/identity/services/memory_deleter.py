"""152-ФЗ subject-rights deletion for GREEN memory (M-B4 / #1113).

The chat-side «forget {X}» and «forget everything» commands (ADR-0011 §8
erasure right; policy §8.2 / §8.3) land here. GREEN ONLY — yellow/red are out
of this stream's scope (their deletion has extra rules: RedZoneAccessLog,
contraindication warnings — policy §8.4).

Two operations, both append the 152-ФЗ audit trail:

- :func:`soft_delete_all_zones_for_forget_all` — mass erasure for the
  forget-all sweep, zone-agnostic (DRF-2180). Reports the red ids it
  buried so the caller can append the access-log rows.
- :func:`soft_delete_green_entries` — per-entry erasure. Sets
  ``delete_requested_at`` + ``soft_deleted_at`` + ``deletion_reason='user_delete'``
  + ``status='deleted'`` + ``updated_at`` in one UPDATE (ADR-0011 §11.3
  soft-delete tombstone; the async purge job hard-deletes after retention).
  The read-gate hides the row immediately.
- :func:`request_forget_all` — mass erasure INTENT. Sets
  ``UPC.forget_all_requested_at``. The read-gate already treats
  ``forget_all_requested_at`` as «forgotten» (memory_reader, #1111), so
  surfacing stops the instant this is set — even before the sweep runs. The
  sweep that then does the erasing is
  :mod:`apps.identity.services.forget_all_sweep`, beat-scheduled hourly. Until
  DRF-1370 this docstring named a job that did not exist: the intent was
  recorded, nothing was ever tombstoned, and the read gate alone stood between
  the person's memory and the prompt.
- :func:`soft_delete_expired_entries` — retention, not a request (DRF-2748):
  yellow/red rows whose storage term ran out, tombstoned with
  ``deletion_reason='ttl_purge'``. The nightly ``memory_ttl_sweep`` task.
- :func:`purge_expired_tombstones` — the physical half (DRF-2775). Every
  path above only tombstones; this deletes the row once the tombstone's
  retention ran out (ADR-0011 §5: 30 days, a consent withdrawal 24 h).
- :func:`sweep_expired_inferences` — the terms of Ф4 inferred green memory
  (DRF-2782): an unconfirmed inference is tombstoned after its 30 days, a
  confirmed one is flagged ``status='expired'`` for re-confirmation.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from apps.audit.services import write_audit
from apps.identity.models import MemoryEntry, RedZoneAccessLog, UserPersonalContext
from apps.identity.services.red_zone_guc import (
    _reset_red_zone_guc,
    _set_red_zone_guc,
    red_zone_principal,
)


def soft_delete_green_entries(
    user_id: uuid.UUID,
    entry_ids: Iterable[uuid.UUID],
    *,
    reason: str = MemoryEntry.DELETION_REASON_USER_DELETE,
) -> int:
    """Soft-delete the given user's live GREEN entries. Returns the count deleted.

    Scoped hard to ``(user_id, sensitivity_zone=green, live)`` — an id that is
    not the user's, not green, or already deleted is silently skipped (defence
    against a caller passing a stray id). Idempotent: a re-delete of an
    already-tombstoned row is a no-op.

    ``reason`` is the tombstone's ``deletion_reason``. It defaults to
    ``user_delete`` — «the person named this fact and asked for it gone» —
    because that is what the chat command does. The forget-all sweep passes
    ``forget_all`` instead: the two are different requests with different
    evidence behind them, and a tombstone that cannot tell them apart cannot
    answer «why is this row deleted» for an audit. ``DELETION_REASON_FORGET_ALL``
    had sat unused in the model since the schema was written, for the same
    reason the sweep itself did not exist (DRF-1370).
    """

    ids = list(entry_ids)
    if not ids:
        return 0

    now = timezone.now()
    with transaction.atomic():
        deleted = MemoryEntry.objects.filter(
            id__in=ids,
            user_id=user_id,
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            soft_deleted_at__isnull=True,
            delete_requested_at__isnull=True,
        ).update(
            delete_requested_at=now,
            soft_deleted_at=now,
            deletion_reason=reason,
            # DRF-1263 — `status` moves in the SAME UPDATE as the tombstone.
            # Without it every deletion after migration 0016 minted
            # `status='active' AND soft_deleted_at IS NOT NULL`: a state the
            # contract forbids, and one that puts the forgotten fact back in
            # front of the person the moment a read path filters by `status`
            # (Migration Plan step 5). `updated_at` moves too — deletion is a
            # state transition (MDC §3.1), and it is the only record of when
            # it happened. CHECK 4 (migration 0019) now enforces the pair.
            status=MemoryEntry.STATUS_DELETED,
            updated_at=now,
        )

    if deleted:
        write_audit(
            "memory.forget_entry",
            target="MemoryEntry",
            payload={
                "user_id": str(user_id),
                "count": deleted,
                "reason": reason,
            },
        )
    return deleted


def soft_delete_inferences_for_withdrawal(user_id: uuid.UUID) -> int:
    """Стереть производную память Ф4 человека, отозвавшего согласие (DRF-2783).

    Решение владельца 05.10: «при отзыве согласия Ф4 — сразу прекратить новые
    выводы и их использование, удалить производную память по процедуре».
    Производная — это зелёные строки ``source='inferred'``: и неподтверждённые
    предложения, и подтверждённые выводы — подтверждение не делает вывод
    сказанным самим человеком (``provenance`` остаётся
    ``user_confirmed_inference``, а не ``user_stated``).

    Не трогаются: явные факты («Вы сообщили») и сигнальные строки — у них
    своё основание, а Ф4 добровольна отдельно, и её отзыв не должен стоить
    человеку остальной памяти. Замещённые исправлением (``superseded``) —
    тоже производные и тоже уходят: история вывода — всё ещё вывод.

    Причина надгробия — ``withdrawal``: так её прочтёт аудит («почему строка
    снята»), и так её физически удалит :func:`purge_expired_tombstones` —
    через сутки, а не через 30 дней (ADR-0011 §5: отзыв — «physical purge
    within 24h»). Идемпотентно: уже снятые строки не трогаются.

    Returns:
      Сколько строк снято.
    """
    now = timezone.now()
    with transaction.atomic():
        deleted = MemoryEntry.objects.filter(
            user_id=user_id,
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            source=MemoryEntry.SOURCE_INFERRED,
            soft_deleted_at__isnull=True,
            delete_requested_at__isnull=True,
        ).update(
            delete_requested_at=now,
            soft_deleted_at=now,
            deletion_reason=MemoryEntry.DELETION_REASON_WITHDRAWAL,
            status=MemoryEntry.STATUS_DELETED,
            updated_at=now,
        )

    if deleted:
        write_audit(
            "memory.inferences_withdrawn",
            target="MemoryEntry",
            payload={
                "user_id": str(user_id),
                "count": deleted,
                "reason": MemoryEntry.DELETION_REASON_WITHDRAWAL,
            },
        )
    return deleted


def soft_delete_all_zones_for_forget_all(
    user_id: uuid.UUID,
    *,
    request_id: uuid.UUID,
) -> tuple[int, int]:
    """Снять ВСЕ живые строки субъекта, любой зоны. Только для «забудь всё».

    Отдельная функция, а не флаг у :func:`soft_delete_green_entries` — и это
    не вкусовщина. Зелёный удалитель зовут ещё три места: чатовая команда
    «забудь про веганство», экран памяти Mini App и путь стирания Ayla. Там
    строку называет ЧЕЛОВЕК по идентификатору, и жёсткая привязка к зелёной
    зоне — защита: назвать красную строку по id и снять её мимо журнала
    доступа нельзя. Расширить зону там значило бы открыть эту дверь всем
    четверым разом ради одного вызывающего.

    Здесь зона не сужается потому, что запрос другой: человек попросил
    забыть ВСЁ, и матрица удаления (DRF-2134) объявляет для красной строки
    ``DELETE`` с причиной «специальная категория (152-ФЗ ст. 10) не должна
    переживать „забудь всё"».

    # GUC обязателен, хотя сегодня работает и без него

    Политика ``memory_entry_non_red_visible`` (миграция 0008) прячет красные
    строки от любого SELECT без ``ayla.red_zone_access_context``, а **WHERE
    у UPDATE подчиняется той же политике SELECT** (это дословно сказано в
    ``red_zone_reader`` там, где он ставит GUC перед надгробием). Сегодня
    приложение ходит под ``platform`` — суперпользователем контейнера, он
    RLS обходит, — и без GUC всё работает. В день перехода на ``ayla_app``
    (ADR-0011 §16, фаза 2, шаг 5) отказ был бы худшего сорта: красные
    строки молча не попали бы в выборку, счёт занизился бы, журнал не
    написался бы, а свип вернул бы зелёный результат. Поэтому GUC ставится
    здесь, а не «когда понадобится».

    # Надгробие и журнал — одна транзакция

    Тот же инвариант, что у ``RedZoneReader`` («no orphan log»): либо есть
    и надгробие, и строка журнала, либо нет ни того, ни другого. Если
    писать журнал после коммита UPDATE, падение между ними даёт снятые
    красные строки без единой строки журнала — и **повторный прогон этого
    не чинит**: живых красных уже нет, второй свип их не увидит. Потеря
    доказательства по 152-ФЗ гл. 3 была бы молчаливой и навсегда.

    # Почему отбор строк живёт ЗДЕСЬ, а не у вызывающего

    Обречённые id выбирает эта функция, а не свип. Иначе GUC накрывал бы
    только половину пути: SELECT свипа шёл бы без него, красные id до
    делетера не доехали бы вовсе, и его собственный GUC оказался бы
    бесполезен. Граница «кто ставит GUC» должна совпадать с границей «кто
    трогает красное» — иначе она не граница.

    Args:
      request_id: один на прогон свипа — одна просьба «забудь всё» это одно
        обращение к зоне, разбитое на строки. Он же уходит в GUC, потому что
        политика требует канонический UUID.

    Returns:
      ``(сколько сняли всего, сколько из них красных)``.
    """
    now = timezone.now()
    red_count = 0
    deleted = 0
    with transaction.atomic():
        _set_red_zone_guc(request_id)
        try:
            live = MemoryEntry.objects.filter(
                user_id=user_id,
                soft_deleted_at__isnull=True,
                delete_requested_at__isnull=True,
            )
            # Красные id читаются ДО UPDATE: после него зона на месте, но
            # «живых» строк уже нет, и выборка по тому же условию вернёт пусто.
            red_ids = list(
                live.filter(sensitivity_zone=MemoryEntry.SENSITIVITY_RED).values_list(
                    "id", flat=True
                )
            )
            deleted = live.update(
                delete_requested_at=now,
                soft_deleted_at=now,
                deletion_reason=MemoryEntry.DELETION_REASON_FORGET_ALL,
                status=MemoryEntry.STATUS_DELETED,
                updated_at=now,
            )
            _log_red_zone_erasure(user_id, red_ids, request_id=request_id)
            red_count = len(red_ids)
        finally:
            _reset_red_zone_guc()

    if deleted:
        write_audit(
            "memory.forget_entry",
            target="MemoryEntry",
            payload={
                "user_id": str(user_id),
                "count": deleted,
                "red_count": red_count,
                "reason": MemoryEntry.DELETION_REASON_FORGET_ALL,
            },
        )
    return deleted, red_count


def _log_red_zone_erasure(
    user_id: uuid.UUID,
    red_ids: list[uuid.UUID],
    *,
    request_id: uuid.UUID,
) -> None:
    """Строка журнала на КАЖДУЮ снятую красную строку (DRF-2180).

    Правило красной зоны: доступ к строке — строка журнала. Свип снимает их
    в обход :class:`RedZoneReader` (он умеет по одной и под своим GUC),
    поэтому вести журнал обязан сам путь свипа — иначе массовое снятие
    специальной категории проходит без следа, а это ровно то, что 152-ФЗ
    гл. 3 просит доказывать.

    Зовётся ТОЛЬКО изнутри ``soft_delete_all_zones_for_forget_all``, внутри
    его транзакции: осиротевшее надгробие неисправимо (см. там же).
    """
    if not red_ids:
        return

    principal = red_zone_principal(RedZoneAccessLog.ACCESSOR_SYSTEM_JOB, "forget_all_sweep")
    RedZoneAccessLog.objects.bulk_create(
        [
            RedZoneAccessLog(
                memory_entry_id=entry_id,
                user_id=user_id,
                accessor_role=RedZoneAccessLog.ACCESSOR_SYSTEM_JOB,
                accessor_principal=principal,
                access_type=RedZoneAccessLog.ACCESS_DELETE,
                request_id=request_id,
                purpose="forget_all sweep — субъект попросил забыть всё",
            )
            for entry_id in red_ids
        ]
    )


ACCOUNT_RESET_ACTOR = "account_reset"


def hard_delete_memory_for_account_reset(user_ids: list[uuid.UUID]) -> dict[str, int]:
    """Физически снять память тестового аккаунта при сбросе — включая красную зону.

    DRF-2542 §7. Сброс (``account_reset.apply``, только аккаунты из allowlist)
    удалял память каскадом от ``UserPersonalContext`` без GUC красной зоны.
    Под суперпользователем это работает: RLS на него не действует. Под обычной
    ролью (замер 07.10 на ``d62e07bd``) политика прячет красные строки от
    сборщика каскада: он их не удаляет, сверка полноты их не видит и
    рапортует «чисто», а транзакция падает на внешнем ключе при фиксации —
    сброс откатывается целиком, с успешным отчётом на руках.

    Поэтому каскад идёт под GUC, а на каждую красную строку пишется строка
    журнала ``purge`` — то же правило, что у остальных путей этого модуля:
    доступ к красной строке оставляет след. Журнал и удаление — одна
    транзакция.

    Returns:
      Счётчики удалённого по моделям, как их отдаёт ``QuerySet.delete()``.
    """
    if not user_ids:
        return {}
    request_id = uuid.uuid4()
    with transaction.atomic():
        _set_red_zone_guc(request_id)
        try:
            # До удаления: после него спрашивать не у кого.
            red = list(
                MemoryEntry.objects.filter(
                    user_id__in=user_ids, sensitivity_zone=MemoryEntry.SENSITIVITY_RED
                ).values_list("id", "user_id")
            )
            _, per_model = UserPersonalContext.objects.filter(user_id__in=user_ids).delete()
            if red:
                principal = red_zone_principal(
                    RedZoneAccessLog.ACCESSOR_SYSTEM_JOB, ACCOUNT_RESET_ACTOR
                )
                RedZoneAccessLog.objects.bulk_create(
                    [
                        RedZoneAccessLog(
                            memory_entry_id=entry_id,
                            user_id=owner_id,
                            accessor_role=RedZoneAccessLog.ACCESSOR_SYSTEM_JOB,
                            accessor_principal=principal,
                            access_type=RedZoneAccessLog.ACCESS_PURGE,
                            request_id=request_id,
                            purpose="account_reset — сброс тестового аккаунта из allowlist",
                        )
                        for entry_id, owner_id in red
                    ]
                )
        finally:
            _reset_red_zone_guc()
    return dict(per_model)


def request_forget_all(user_id: uuid.UUID) -> bool:
    """Record the user's «forget everything» intent on their UPC.

    Sets ``forget_all_requested_at`` — the user-intent moment, and ONLY that.
    The erasure itself is :func:`apps.identity.services.forget_all_sweep.
    sweep_forget_all`, which the hourly beat job runs (ADR-0011 §8 mass
    erasure). Callers must not read this returning ``True`` as «erased»: it
    means «the request is recorded», which is what the read gate acts on
    immediately. Idempotent: returns
    ``True`` only when it was newly set (no live UPC → creates one so the intent
    is durably recorded; a user with no memory still has their request honoured
    if the sweep later finds nothing).
    """

    upc, _ = UserPersonalContext.objects.get_or_create(user_id=user_id)
    if upc.forget_all_requested_at is not None:
        return False

    now = timezone.now()
    with transaction.atomic():
        UserPersonalContext.objects.filter(
            user_id=user_id, forget_all_requested_at__isnull=True
        ).update(forget_all_requested_at=now)

    write_audit(
        "memory.forget_all_requested",
        target="UserPersonalContext",
        target_id=user_id,
        payload={"user_id": str(user_id)},
    )
    return True


#: DRF-2748 — зоны со сроком хранения. Зелёной здесь нет и быть не должно:
#: спека §5 даёт ей «no auto-TTL», и свип срока её не трогает, даже если у
#: строки по ошибке окажется ``expires_at``.
TTL_ZONES: tuple[str, ...] = (MemoryEntry.SENSITIVITY_YELLOW, MemoryEntry.SENSITIVITY_RED)

#: Сколько истёкших строк снимает один прогон. Хвост после простоя уходит за
#: несколько ночей, а не одной транзакцией на всю таблицу.
TTL_SWEEP_BATCH_SIZE = 500

TTL_SWEEP_ACTOR = "memory_ttl_sweep"
TTL_SWEEP_RED_PURPOSE = "ttl_sweep — срок хранения красной зоны истёк"


@dataclass(frozen=True)
class ExpiredPurge:
    """Что сделал один прогон свипа срока. Числа, никогда не значения."""

    expiry_backfilled: int = 0
    purged_yellow: int = 0
    purged_red: int = 0
    users: int = 0

    @property
    def purged(self) -> int:
        return self.purged_yellow + self.purged_red

    def as_summary(self) -> dict[str, int]:
        return {
            "expiry_backfilled": self.expiry_backfilled,
            "purged": self.purged,
            "purged_yellow": self.purged_yellow,
            "purged_red": self.purged_red,
            "users": self.users,
        }


def _live_rows_with_a_term():
    """Живые жёлтые и красные строки, у которых срок вообще есть.

    ``ttl_days IS NULL`` — «срока нет» (спека §5, колонка ``ttl_days``), и
    ничего за строку не придумывается: ни срок, ни дата истечения.
    """
    return MemoryEntry.objects.filter(
        sensitivity_zone__in=TTL_ZONES,
        soft_deleted_at__isnull=True,
        delete_requested_at__isnull=True,
        ttl_days__isnull=False,
    )


def _backfill_missing_expiry() -> int:
    """Поставить ``expires_at`` тем строкам, где его нет, — по правилу 0016.

    Миграция 0016 проставила ``created_at + ttl_days`` строкам, лежавшим на
    момент её прогона, а писатель ставит дату только явным записям
    (``memory_writer``: ``source == EXPLICIT``). Строка со сроком, но без даты
    истечения, свипу невидима — а значит, жила бы вечно. Правило то же, что у
    0016, слово в слово, чтобы у одной строки не было двух дат в зависимости
    от того, кто её считал. Повторный прогон ничего не меняет: условие —
    ``expires_at IS NULL``.
    """
    rows = _live_rows_with_a_term().filter(expires_at__isnull=True)
    filled = 0
    for ttl in list(rows.order_by().values_list("ttl_days", flat=True).distinct()):
        filled += rows.filter(ttl_days=ttl).update(expires_at=F("created_at") + timedelta(days=ttl))
    return filled


def _expired(now: datetime):
    """Строки, срок которых истёк по ОБОИМ правилам, — и только они.

    В репозитории два правила срока, и они расходятся:

    * контракт памяти §3.1 — абсолютная дата ``expires_at`` (= запись +
      ``ttl_days``);
    * спека §5 — скользящее окно ``GREATEST(last_used_at, consent_at) <
      now() - ttl_days``: факт, которым пользуются, не стареет.

    Снимается строка, только если истекла по обоим. Это не выбор между ними,
    а их пересечение: расхождение решает владелец, а до его слова свип не
    стирает ничего, что хоть одно из правил ещё держит. Сегодня они совпадают
    (``last_used_at`` никто не пишет, он равен ``created_at``), но обновление
    ``last_used_at`` в будущем не должно молча начать стирать живые факты.

    Не трогаются строки человека, который уже попросил стереть всё
    (``forget_all_requested_at``) или чья заявка на удаление ещё жива
    (``deletion_requested_at``): у этих стираний свои пути, свои причины в
    надгробии и свой журнал, и TTL не должен перебивать их ответ аудиту на
    вопрос «почему снята строка».

    DRF-2774: категория, чьи сроки владелец утвердил
    (``memory_term.approved_categories``), судится по одной дате
    ``expires_at`` — её продлевает только использование до предела, и
    скользящее окно поверх неё держало бы строку за пределом. Остальные — то
    же пересечение (fail-safe). Пока сроки не утверждены, список пуст и свип
    прежний.
    """
    from apps.identity.services.memory_term import F4_CONSENT_SCOPE, approved_categories

    ttls = list(
        _live_rows_with_a_term()
        .filter(expires_at__lte=now)
        .order_by()
        .values_list("ttl_days", flat=True)
        .distinct()
    )
    if not ttls:
        return MemoryEntry.objects.none()
    window = Q()
    for ttl in ttls:
        cutoff = now - timedelta(days=ttl)
        window |= Q(ttl_days=ttl, last_used_at__lte=cutoff) & ~Q(consent_at__gt=cutoff)
    for zone, kind in approved_categories():
        window |= Q(sensitivity_zone=zone, kind=kind) & ~Q(consent_scope=F4_CONSENT_SCOPE)
    held = UserPersonalContext.objects.filter(user_id=OuterRef("user_id")).filter(
        Q(forget_all_requested_at__isnull=False) | Q(deletion_requested_at__isnull=False)
    )
    return (
        _live_rows_with_a_term()
        .filter(expires_at__isnull=False, expires_at__lte=now)
        .filter(window)
        .exclude(Exists(held))
    )


def soft_delete_expired_entries(
    *,
    now: datetime | None = None,
    limit: int = TTL_SWEEP_BATCH_SIZE,
) -> ExpiredPurge:
    """Снять жёлтые и красные строки, срок хранения которых истёк (DRF-2748).

    Тот же вид надгробия, что у остальных путей этого модуля
    (``delete_requested_at`` + ``soft_deleted_at`` + ``deletion_reason`` +
    ``status='deleted'`` + ``updated_at``), с причиной ``ttl_purge``. Ничего не
    удаляется физически: надгробие живёт свои 30 дней, как у любого стирания.

    # Почему здесь, а не в отдельном модуле свипа

    По той же причине, по которой отбор живёт в
    :func:`soft_delete_all_zones_for_forget_all`: красные строки видны только
    под GUC, и **WHERE у UPDATE подчиняется политике SELECT**. Граница «кто
    ставит GUC» обязана совпадать с границей «кто трогает красное» — и
    дозаполнение ``expires_at``, и отбор, и надгробие, и журнал идут в одной
    транзакции под одним GUC. Иначе в день перехода на ``ayla_app`` красные
    строки молча выпали бы из выборки, а свип вернул бы успех.

    # Журнал

    Каждая снятая красная строка — строка ``RedZoneAccessLog``
    (``access_type='purge'``, ``accessor_role='system_job'``) в той же
    транзакции: снятое без следа неисправимо, повторный прогон его уже не
    увидит. Строки отбираются ``FOR UPDATE``, поэтому журнал называет ровно
    те строки, которые снял этот прогон, а не те, что в промежутке снял
    человек.

    Args:
      now: момент, на который судим о сроке; по умолчанию — сейчас.
      limit: сколько строк снять за прогон.
    """
    now = now or timezone.now()
    request_id = uuid.uuid4()
    with transaction.atomic():
        _set_red_zone_guc(request_id)
        try:
            backfilled = _backfill_missing_expiry()
            doomed = list(
                _expired(now)
                .select_for_update()
                .order_by("expires_at", "id")
                .values_list("id", "user_id", "sensitivity_zone")[:limit]
            )
            MemoryEntry.objects.filter(id__in=[row[0] for row in doomed]).update(
                delete_requested_at=now,
                soft_deleted_at=now,
                deletion_reason=MemoryEntry.DELETION_REASON_TTL_PURGE,
                status=MemoryEntry.STATUS_DELETED,
                updated_at=now,
            )
            red = [
                (entry_id, user_id)
                for entry_id, user_id, zone in doomed
                if zone == MemoryEntry.SENSITIVITY_RED
            ]
            _log_red_zone_purge(red, request_id=request_id)
        finally:
            _reset_red_zone_guc()

    result = ExpiredPurge(
        expiry_backfilled=backfilled,
        purged_yellow=len(doomed) - len(red),
        purged_red=len(red),
        users=len({user_id for _, user_id, _ in doomed}),
    )
    if result.purged or result.expiry_backfilled:
        write_audit(
            "memory.ttl_purged",
            target="MemoryEntry",
            payload={
                **result.as_summary(),
                "reason": MemoryEntry.DELETION_REASON_TTL_PURGE,
                "request_id": str(request_id),
            },
        )
    return result


def _log_red_zone_purge(
    red: list[tuple[uuid.UUID, uuid.UUID]],
    *,
    request_id: uuid.UUID,
) -> None:
    """Строка журнала на каждую красную строку, снятую по сроку.

    Зовётся только изнутри :func:`soft_delete_expired_entries`, в его
    транзакции — по той же причине, что :func:`_log_red_zone_erasure`.
    """
    if not red:
        return
    principal = red_zone_principal(RedZoneAccessLog.ACCESSOR_SYSTEM_JOB, TTL_SWEEP_ACTOR)
    RedZoneAccessLog.objects.bulk_create(
        [
            RedZoneAccessLog(
                memory_entry_id=entry_id,
                user_id=user_id,
                accessor_role=RedZoneAccessLog.ACCESSOR_SYSTEM_JOB,
                accessor_principal=principal,
                access_type=RedZoneAccessLog.ACCESS_PURGE,
                request_id=request_id,
                purpose=TTL_SWEEP_RED_PURPOSE,
            )
            for entry_id, user_id in red
        ]
    )


#: DRF-2775 — сколько живёт надгробие, прежде чем строка удаляется физически.
#: ADR-0011 §5 (таблица зон): «Soft-delete tombstone retained 30 days …, then
#: hard-purged» — для всех трёх зон; для отзыва согласия — «Physical purge
#: within 24h». Сменит владелец срок — меняется здесь.
TOMBSTONE_RETENTION = timedelta(days=30)
WITHDRAWAL_TOMBSTONE_RETENTION = timedelta(hours=24)

#: Сколько строк удаляет один прогон: хвост после простоя уходит за несколько
#: ночей, а не одной транзакцией на всю таблицу.
TOMBSTONE_PURGE_BATCH_SIZE = 500

TOMBSTONE_PURGE_ACTOR = "memory_tombstone_purge"
TOMBSTONE_PURGE_RED_PURPOSE = "tombstone_purge — удержание надгробия истекло"


@dataclass(frozen=True)
class TombstonePurge:
    """Что удалил один прогон. Числа, никогда не значения."""

    purged_green: int = 0
    purged_yellow: int = 0
    purged_red: int = 0
    users: int = 0
    by_reason: tuple[tuple[str, int], ...] = ()

    @property
    def purged(self) -> int:
        return self.purged_green + self.purged_yellow + self.purged_red

    def as_summary(self) -> dict:
        return {
            "purged": self.purged,
            "purged_green": self.purged_green,
            "purged_yellow": self.purged_yellow,
            "purged_red": self.purged_red,
            "users": self.users,
            "by_reason": dict(self.by_reason),
        }


def _tombstones_past_retention(now: datetime):
    """Надгробия, удержание которых истекло. Живые строки сюда не попадают.

    Строка — надгробие, только если у неё стоит ``soft_deleted_at``: заявка
    без него (``delete_requested_at`` одна) — не завершённое стирание, и
    удалять её физически значило бы обогнать путь, который её снимает.
    """
    return MemoryEntry.objects.filter(soft_deleted_at__isnull=False).filter(
        Q(
            deletion_reason=MemoryEntry.DELETION_REASON_WITHDRAWAL,
            soft_deleted_at__lte=now - WITHDRAWAL_TOMBSTONE_RETENTION,
        )
        | (
            ~Q(deletion_reason=MemoryEntry.DELETION_REASON_WITHDRAWAL)
            & Q(soft_deleted_at__lte=now - TOMBSTONE_RETENTION)
        )
    )


def purge_expired_tombstones(
    *,
    now: datetime | None = None,
    limit: int = TOMBSTONE_PURGE_BATCH_SIZE,
) -> TombstonePurge:
    """Удалить физически строки памяти, чьё надгробие отлежало срок (DRF-2775).

    Все пути стирания этого модуля — «забудь X», «забудь всё», отзыв, срок —
    только ставят надгробие: даты и причину. ``content`` остаётся в строке,
    зашифрованный, и без этой функции оставался бы навсегда. ADR-0011 §5
    обещает физическую очистку после удержания; до DRF-2775 её не делал никто,
    и «удалено» было удалено только по виду (``OD_MEMORY.md`` §4).

    # Удержание и «не вспоминать стёртое»

    ``orchestrator.memory.evicted_review`` читает содержимое зелёных надгробий
    «стёрто по просьбе», чтобы не вернуть факт из сообщения, ещё лежащего в
    короткой памяти. Сообщение живёт там не дольше
    ``SHORT_TERM_MEMORY_DEPTH × SHORT_TERM_MEMORY_TTL_SECONDS`` (каждая
    реплика продлевает ключ на сутки, окно — 20 сообщений): ≤ 20 дней при 30
    днях удержания. Узел держит это неравенство. Отзыв согласия — жёлтая и
    красная зоны, которых ``evicted_review`` не читает, — удаляется через
    сутки.

    # Журнал красной зоны переживает строку

    Каждая удаляемая красная строка — строка ``RedZoneAccessLog``
    (``access_type='purge'``) в той же транзакции под GUC. Внешнего ключа у
    журнала нет (ADR-0011 §7.3), поэтому след остаётся после строки — это и
    есть его назначение. Строки отбираются ``FOR UPDATE``: журнал называет
    ровно удалённые.

    Карточка человека (``UserPersonalContext``) не удаляется — её
    физическое удаление спека запрещает; её личные поля чистит «забудь всё».

    Args:
      now: момент, на который судим об удержании; по умолчанию — сейчас.
      limit: сколько строк удалить за прогон.
    """
    now = now or timezone.now()
    request_id = uuid.uuid4()
    with transaction.atomic():
        _set_red_zone_guc(request_id)
        try:
            doomed = list(
                _tombstones_past_retention(now)
                .select_for_update()
                .order_by("soft_deleted_at", "id")
                .values_list("id", "user_id", "sensitivity_zone", "deletion_reason")[:limit]
            )
            red = [
                (entry_id, user_id)
                for entry_id, user_id, zone, _ in doomed
                if zone == MemoryEntry.SENSITIVITY_RED
            ]
            _log_red_zone_tombstone_purge(red, request_id=request_id)
            # QuerySet.delete(), а не сырой DELETE: строки, которые ссылаются на
            # удаляемую через ``superseded_by``, получают NULL (SET_NULL), а не
            # нарушение ключа.
            MemoryEntry.objects.filter(id__in=[row[0] for row in doomed]).delete()
        finally:
            _reset_red_zone_guc()

    zones = [zone for _, _, zone, _ in doomed]
    reasons: dict[str, int] = {}
    for _, _, _, reason in doomed:
        reasons[reason] = reasons.get(reason, 0) + 1
    result = TombstonePurge(
        purged_green=zones.count(MemoryEntry.SENSITIVITY_GREEN),
        purged_yellow=zones.count(MemoryEntry.SENSITIVITY_YELLOW),
        purged_red=len(red),
        users=len({user_id for _, user_id, _, _ in doomed}),
        by_reason=tuple(sorted(reasons.items())),
    )
    if result.purged:
        write_audit(
            "memory.tombstones_purged",
            target="MemoryEntry",
            payload={**result.as_summary(), "request_id": str(request_id)},
        )
    return result


def _log_red_zone_tombstone_purge(
    red: list[tuple[uuid.UUID, uuid.UUID]],
    *,
    request_id: uuid.UUID,
) -> None:
    """Строка журнала на каждую физически удаляемую красную строку.

    Зовётся только изнутри :func:`purge_expired_tombstones`, в его транзакции
    и ДО удаления: удалённое без следа неисправимо.
    """
    if not red:
        return
    principal = red_zone_principal(RedZoneAccessLog.ACCESSOR_SYSTEM_JOB, TOMBSTONE_PURGE_ACTOR)
    RedZoneAccessLog.objects.bulk_create(
        [
            RedZoneAccessLog(
                memory_entry_id=entry_id,
                user_id=user_id,
                accessor_role=RedZoneAccessLog.ACCESSOR_SYSTEM_JOB,
                accessor_principal=principal,
                access_type=RedZoneAccessLog.ACCESS_PURGE,
                request_id=request_id,
                purpose=TOMBSTONE_PURGE_RED_PURPOSE,
            )
            for entry_id, user_id in red
        ]
    )


#: DRF-2782 — сколько строк каждого вида обрабатывает один прогон.
INFERENCE_SWEEP_BATCH_SIZE = 500


@dataclass(frozen=True)
class InferenceExpiry:
    """Что сделал один прогон свипа сроков Ф4. Числа, никогда не значения."""

    unconfirmed_deleted: int = 0
    confirmed_flagged: int = 0
    users: int = 0

    def as_summary(self) -> dict[str, int]:
        return {
            "unconfirmed_deleted": self.unconfirmed_deleted,
            "confirmed_flagged": self.confirmed_flagged,
            "users": self.users,
        }


def _expired_inferences(now: datetime):
    """Живые зелёные выводы Ф4 с наступившим ``expires_at``.

    Только ``source='inferred'`` в зелёной зоне: явные факты человека
    («Вы сообщили») сроком не стареют, а жёлтую и красную зону ведёт свой свип
    (:func:`soft_delete_expired_entries`). Человек с «забудь всё» или с живой
    заявкой на удаление не трогается — у этих стираний свой путь и своя
    причина в надгробии.
    """
    held = UserPersonalContext.objects.filter(user_id=OuterRef("user_id")).filter(
        Q(forget_all_requested_at__isnull=False) | Q(deletion_requested_at__isnull=False)
    )
    return (
        MemoryEntry.objects.filter(
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            source=MemoryEntry.SOURCE_INFERRED,
            soft_deleted_at__isnull=True,
            delete_requested_at__isnull=True,
            expires_at__isnull=False,
            expires_at__lte=now,
        )
        .exclude(Exists(held))
        .order_by("expires_at", "id")
    )


def sweep_expired_inferences(
    *,
    now: datetime | None = None,
    limit: int = INFERENCE_SWEEP_BATCH_SIZE,
) -> InferenceExpiry:
    """Применить сроки производной памяти Ф4 (DRF-2782).

    Решение владельца 05.10 (``OWNER_DECISIONS_MEMORY_AND_CONSENT_2026-10-05``,
    «Сроки производной памяти Ф4»): неподтверждённые предположения — 30 дней;
    подтверждённые — 180 дней, «затем повторное подтверждение или удаление».
    Сам срок ставит писатель в ``expires_at`` (Ф4a-2 — запись + 30 дней,
    Ф4a-3 — подтверждение + 180 дней); свип только исполняет наступивший.

    * **Неподтверждённый** (``provenance IS NULL``) — надгробие, причина
      ``ttl_purge``, как у любого истёкшего срока. Физически строку удалит
      :func:`purge_expired_tombstones`, когда (и если) её включат.
    * **Подтверждённый** (``provenance='user_confirmed_inference'``) —
      строка НЕ удаляется: ``status='expired'``, то есть «нужно
      переподтвердить». Человек сам подтвердил этот факт, и молча стереть его
      через полгода значило бы решить за него. Уже помеченные повторно не
      трогаются.
    * Любой другой ``provenance`` — не трогается: свип не угадывает.
    * Замещённое исправлением (``status='superseded'``, Ф4a-3) — не
      трогается: это история, а не действующее предложение.

    Отдельный свип, а не ветка :func:`soft_delete_expired_entries`: тот
    держит жёлтую и красную зону и скользящее окно спеки §5, а у выводов Ф4
    срок абсолютный и исход для подтверждённых — не удаление.
    """
    now = now or timezone.now()
    with transaction.atomic():
        # Только действующие строки: замещённое исправлением (``superseded``)
        # — история, а не предложение, и уже помеченное ``expired`` второй
        # раз не трогается.
        expired = (
            _expired_inferences(now)
            .filter(Q(status__isnull=True) | Q(status=MemoryEntry.STATUS_ACTIVE))
            .select_for_update()
        )
        unconfirmed = list(
            expired.filter(provenance__isnull=True).values_list("id", "user_id")[:limit]
        )
        confirmed = list(
            expired.filter(provenance=MemoryEntry.PROVENANCE_USER_CONFIRMED_INFERENCE).values_list(
                "id", "user_id"
            )[:limit]
        )
        MemoryEntry.objects.filter(id__in=[row[0] for row in unconfirmed]).update(
            delete_requested_at=now,
            soft_deleted_at=now,
            deletion_reason=MemoryEntry.DELETION_REASON_TTL_PURGE,
            status=MemoryEntry.STATUS_DELETED,
            updated_at=now,
        )
        MemoryEntry.objects.filter(id__in=[row[0] for row in confirmed]).update(
            status=MemoryEntry.STATUS_EXPIRED,
            updated_at=now,
        )

    result = InferenceExpiry(
        unconfirmed_deleted=len(unconfirmed),
        confirmed_flagged=len(confirmed),
        users=len({user_id for _, user_id in unconfirmed + confirmed}),
    )
    if result.unconfirmed_deleted or result.confirmed_flagged:
        write_audit(
            "memory.inferences_expired",
            target="MemoryEntry",
            payload={**result.as_summary(), "reason": MemoryEntry.DELETION_REASON_TTL_PURGE},
        )
    return result
