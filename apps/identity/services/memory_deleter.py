"""152-ФЗ subject-rights deletion for GREEN memory (M-B4 / #1113).

The chat-side «forget {X}» and «forget everything» commands (ADR-0011 §8
erasure right; policy §8.2 / §8.3) land here. GREEN ONLY — yellow/red are out
of this stream's scope (their deletion has extra rules: RedZoneAccessLog,
contraindication warnings — policy §8.4).

Two operations, both append the 152-ФЗ audit trail:

- :func:`soft_delete_green_entries` — per-entry erasure. Sets
  ``delete_requested_at`` + ``soft_deleted_at`` + ``deletion_reason='user_delete'``
  + ``status='deleted'`` + ``updated_at`` in one UPDATE (ADR-0011 §11.3
  soft-delete tombstone; the async purge job hard-deletes after retention).
  The read-gate hides the row immediately.
- :func:`request_forget_all` — mass erasure INTENT. Sets
  ``UPC.forget_all_requested_at``; the async sweep then soft-deletes every
  entry. The read-gate already treats ``forget_all_requested_at`` as «forgotten»
  (memory_reader, #1111), so surfacing stops the instant this is set — even
  before the sweep runs.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from django.db import transaction
from django.utils import timezone

from apps.audit.services import write_audit
from apps.identity.models import MemoryEntry, UserPersonalContext


def soft_delete_green_entries(
    user_id: uuid.UUID,
    entry_ids: Iterable[uuid.UUID],
) -> int:
    """Soft-delete the given user's live GREEN entries. Returns the count deleted.

    Scoped hard to ``(user_id, sensitivity_zone=green, live)`` — an id that is
    not the user's, not green, or already deleted is silently skipped (defence
    against a caller passing a stray id). Idempotent: a re-delete of an
    already-tombstoned row is a no-op.
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
            deletion_reason=MemoryEntry.DELETION_REASON_USER_DELETE,
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
                "reason": MemoryEntry.DELETION_REASON_USER_DELETE,
            },
        )
    return deleted


def request_forget_all(user_id: uuid.UUID) -> bool:
    """Record the user's «forget everything» intent on their UPC.

    Sets ``forget_all_requested_at`` (user-intent moment; the async sweep then
    soft-deletes all entries — ADR-0011 §8 mass erasure). Idempotent: returns
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
