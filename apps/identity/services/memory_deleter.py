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
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from django.db import transaction
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
