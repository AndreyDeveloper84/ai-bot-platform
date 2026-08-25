"""«Забудь всё», executed — the sweep three docstrings already promised (DRF-1370).

``memory_deleter.request_forget_all`` records the *intent*: it stamps
``UPC.forget_all_requested_at`` and stops there. Three places in this
repository then told the reader what happens next —

    memory_deleter:16   «the async sweep then soft-deletes every entry»
    memory_reader:70    «the completed forget-all (``soft_deleted_at`` set
                         by the async sweep)»
    models.py:598       «async sweep then soft-deletes all entries»

— and no such job existed. ``apps/identity/tasks.py`` registered exactly one
task, and it recomputes profiles. So after «забудь всё» the person's rows sat
there ``status='active'``, ``soft_deleted_at IS NULL``, and the ONLY thing
standing between them and the prompt was the read gate in
:func:`~apps.identity.services.memory_reader.get_personal_context`.

That is a single point of failure of the worst shape. The gate lives in one
function; every read path is expected to go through it; and a read path that
forgets to would not fail, would not warn, and would not surface one stale
fact — it would resurrect the person's ENTIRE memory at once, after they were
told it was gone. Per the owner's ruling (``OD_MEMORY.md`` §4) «удалить» here
was executed only in appearance.

This module is the missing half. It is deliberately NOT a second gate: it
changes the stored state so that a gate-less read finds nothing to return.

# What it erases

* Every live 🟢 green ``MemoryEntry`` → the existing soft-delete tombstone
  (``delete_requested_at`` + ``soft_deleted_at`` + ``deletion_reason`` +
  ``status='deleted'``), via :func:`memory_deleter.soft_delete_green_entries`
  so there is exactly one way a green row is ever tombstoned.
* The personal columns ON the UPC row: ``summary``,
  ``display_name_preferred``, ``language_preferred``. These are not entries
  and no entry sweep would have touched them — and ``summary`` is the single
  largest leak of the three: Ayla's running prose account of who the person
  is, stored unencrypted precisely so the prompt builder can read it on every
  turn (see the model docstring), crossing salons in free text
  (``personal_fields.POLICY_DEBT``). A sweep that tombstoned the entries and
  left the summary would have passed a test written against
  ``build_concierge_memory_block`` — which never reads it — and leaked the
  paragraph through ``read_personal_context``, which does.
* Finally the UPC is stamped ``soft_deleted_at`` — the «completed forget-all»
  state ``memory_reader`` already documents.

# What it deliberately does NOT erase

* **``minor_lock``** — a protection, not a fact about the person. It blocks
  yellow/red writes once reconciliation finds the user is a minor. Clearing
  it as part of an erasure would turn a subject-rights request into a safety
  downgrade.
* **Yellow and red entries.** This stream is green-only, as the whole
  ``memory_deleter`` module is: yellow/red erasure carries extra rules
  (``RedZoneAccessLog``, contraindication warnings — policy §8.4) and is not
  in scope here. Neither zone is readable by the surfacing path this sweep
  is protecting: ``memory_reader`` never selects them, and red is reachable
  only through the audited ``red_zone_reader`` accessor. Named here rather
  than silently skipped — see ``export_coverage`` for the same rule applied
  to the export.
* **``UserPreferences``** — the four notification toggles and the profile
  screen's birthday. Standing instructions and a form the person maintains
  themselves are not things Ayla «remembers about» them; deleting that row
  would reset ``notify_reminders`` / ``notify_retention`` /
  ``notify_birthday`` to ``True`` and switch back ON the nudges someone had
  deliberately switched off. The chat command's wording was narrowed to say
  so instead (``apps.persona.memory_commands``), and the export now shows
  those values so «what does Ayla still hold about me» has an answer the
  person can pull rather than guess.
* **Nothing is hard-deleted.** Soft delete + tombstone is the contour's
  existing choice and it has reasons — audit, disputes, recovery from a
  mistaken erasure. The physical purge after the retention window is a
  separate job and a separate decision.

# Why it re-runs instead of stopping at a marker

The obvious design stamps «swept» and never looks at the row again. This one
keeps sweeping any forgotten user who still has a live green row, because the
write path does NOT honour the forget-all gate:
:func:`memory_reader.get_or_create_personal_context` returns the row whatever
its tombstones say, so a fact extracted from a later turn can still be minted
under a forgotten UPC. A one-shot sweep would leave that row live forever with
only the gate hiding it — the exact state this module exists to end. Re-running
is idempotent by construction: ``soft_delete_green_entries`` skips rows that
are already tombstoned, and a clean user costs one indexed count.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast

from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from apps.audit.services import write_audit
from apps.conversations.erasure import (
    AnonymizeResult,
    anonymize_dialogue,
    shell_ids_for_person,
)
from apps.conversations.models import ArchivedMessage, Conversation
from apps.identity.models import MemoryEntry, UserPersonalContext
from apps.identity.services.memory_deleter import soft_delete_green_entries

logger = logging.getLogger(__name__)

#: How many forgotten users one sweep run will process. The pilot has a
#: handful; the cap exists so a backlog after an outage drains over several
#: runs instead of holding one transaction open across all of them.
SWEEP_BATCH_SIZE = 500


@dataclass(frozen=True)
class ForgetAllSweepResult:
    """What one user's sweep actually changed. Counts, never values."""

    user_id: uuid.UUID
    entries_deleted: int = 0
    context_fields_cleared: int = 0
    tombstoned: bool = False
    conversations_anonymized: int = 0
    messages_archived: int = 0

    @property
    def changed(self) -> bool:
        """Did this sweep move any state? Drives the audit row."""

        return bool(
            self.entries_deleted
            or self.context_fields_cleared
            or self.tombstoned
            or self.conversations_anonymized
        )


def sweep_forget_all(user_id: uuid.UUID) -> ForgetAllSweepResult:
    """Execute one forgotten user's erasure. Idempotent; safe to re-run.

    A no-op (all-zero result, no audit row) when the user never requested
    forget-all — the caller may pass any id.
    """

    upc = UserPersonalContext.objects.filter(
        user_id=user_id, forget_all_requested_at__isnull=False
    ).first()
    if upc is None:
        return ForgetAllSweepResult(user_id=user_id)

    # Read the entries WITHOUT the read gate, on purpose.
    # ``memory_reader.read_green_entries`` returns [] for exactly this user —
    # ``get_personal_context`` filters ``forget_all_requested_at IS NULL`` — so
    # the surfacing reader is blind to the very rows we are here to bury. Going
    # around it is the point of the module; every OTHER caller must keep using
    # the reader.
    doomed_ids = list(
        MemoryEntry.objects.filter(
            user_id=user_id,
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            soft_deleted_at__isnull=True,
            delete_requested_at__isnull=True,
        ).values_list("id", flat=True)
    )
    entries_deleted = soft_delete_green_entries(
        user_id,
        doomed_ids,
        # Not `user_delete`: nobody named these rows. The tombstone must say
        # which request buried it, or an audit cannot tell «я забыла про
        # веганство» from «забудь всё».
        reason=MemoryEntry.DELETION_REASON_FORGET_ALL,
    )

    now = timezone.now()
    # Count what was actually populated so the audit row and the summary say
    # «cleared 2 fields», not «cleared 3» on a row that held one.
    populated = [
        name
        for name in ("summary", "display_name_preferred", "language_preferred")
        if (getattr(upc, name) or "").strip()
    ]
    tombstoned = upc.soft_deleted_at is None

    if populated or tombstoned:
        with transaction.atomic():
            UserPersonalContext.objects.filter(user_id=user_id).update(
                summary=None,
                display_name_preferred=None,
                language_preferred=None,
                # The «completed forget-all» state memory_reader documents.
                # Kept if already set: the first sweep's timestamp is the one
                # that answers «when was this honoured».
                soft_deleted_at=upc.soft_deleted_at or now,
                # ``auto_now`` does not fire on ``.update()``, and erasure is a
                # state transition — the same reason DRF-1263 moved it in the
                # entry tombstone UPDATE.
                updated_at=now,
            )

    # The dialogue (DRF-1369 / OD_MEMORY.md §4). Not deleted — anonymised:
    # the body moves to ``ArchivedMessage`` redacted, the columns every prompt
    # path reads are blanked, and both Redis stores of raw dialogue PII go.
    #
    # The cutoff is ``forget_all_requested_at``, NOT «now». «Забудь всё» is not
    # the end of the dialogue: the person keeps talking, and the turns they take
    # after the request are theirs again. A «now» cutoff would have this hourly
    # sweep blanking their live conversation every hour forever — and would make
    # the re-run this module is built around destructive instead of idempotent.
    # Non-NULL by the queryset above (``forget_all_requested_at__isnull=False``);
    # bound to a local so the invariant is stated where it is relied on rather
    # than asserted away with a cast.
    requested_at = upc.forget_all_requested_at
    dialogue = (
        anonymize_dialogue(
            shell_ids_for_person(ayla_user_id=user_id),
            through=requested_at,
            reason=ArchivedMessage.Reason.FORGET_ALL,
        )
        if requested_at is not None
        else AnonymizeResult()
    )

    result = ForgetAllSweepResult(
        user_id=user_id,
        entries_deleted=entries_deleted,
        context_fields_cleared=len(populated),
        tombstoned=tombstoned,
        conversations_anonymized=dialogue.conversations,
        messages_archived=dialogue.messages_archived,
    )

    if result.changed:
        write_audit(
            "memory.forget_all_swept",
            target="UserPersonalContext",
            target_id=user_id,
            payload={
                "user_id": str(user_id),
                "entries_deleted": result.entries_deleted,
                # Field NAMES, never their values — the audit payload must not
                # carry the summary paragraph we just erased (C5 §6.2).
                "context_fields_cleared": populated,
                "tombstoned": result.tombstoned,
                "conversations_anonymized": result.conversations_anonymized,
                "messages_archived": result.messages_archived,
            },
        )
        logger.info(
            "identity.forget_all_sweep.user user_id=%s entries=%d fields=%d "
            "tombstoned=%s conversations=%d messages=%d",
            user_id,
            result.entries_deleted,
            result.context_fields_cleared,
            result.tombstoned,
            result.conversations_anonymized,
            result.messages_archived,
        )
    return result


def pending_forget_all_user_ids(limit: int = SWEEP_BATCH_SIZE) -> list[uuid.UUID]:
    """Forgotten users whose erasure is not finished. Oldest request first.

    «Not finished» is three conditions ORed, and each is needed:

    * ``soft_deleted_at IS NULL`` — the request has never been swept.
    * a live green row exists — the request WAS swept, and something was
      written afterwards. See the module docstring: the write path does not
      honour the forget-all gate, so this is reachable without a bug.
    * a pre-request conversation whose anonymisation cutoff has not reached
      the request — the dialogue half did not finish (DRF-1369).

    Ordered by ``forget_all_requested_at`` so that under a backlog the person
    who has been waiting longest is erased first — the ordering a regulator
    would expect of a statutory deadline.
    """

    live_green = MemoryEntry.objects.filter(
        user_id=OuterRef("user_id"),
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        soft_deleted_at__isnull=True,
        delete_requested_at__isnull=True,
    )
    # DRF-1369 — a third way the erasure can be unfinished. The dialogue half
    # writes to Redis before it writes to Postgres (see
    # ``erasure._clear_redis_stores``), so a Redis outage leaves the cutoff
    # unmoved. Without this term the sweep would tick the user off after the
    # memory half succeeded and never come back for the переписка — the
    # «success reported for work not done» shape the cascade already refuses
    # elsewhere.
    unanonymized_dialogue = Conversation.all_tenants.filter(
        bot_user__ayla_user_id=OuterRef("user_id"),
        created_at__lte=OuterRef("forget_all_requested_at"),
    ).filter(
        Q(anonymized_through__isnull=True)
        | Q(anonymized_through__lt=OuterRef("forget_all_requested_at"))
    )
    qs = (
        UserPersonalContext.objects.filter(forget_all_requested_at__isnull=False)
        .annotate(
            has_live_green=Exists(live_green),
            has_live_dialogue=Exists(unanonymized_dialogue),
        )
        .filter(
            Q(soft_deleted_at__isnull=True) | Q(has_live_green=True) | Q(has_live_dialogue=True)
        )
        .order_by("forget_all_requested_at")
        .values_list("user_id", flat=True)
    )
    # `cast` because django-stubs types `values_list` on an ANNOTATED queryset
    # as yielding the annotated model, not the column — the annotation is what
    # loses it the element type. `user_id` is the UUID primary key, so the
    # runtime values are `uuid.UUID`; the cast states that rather than widening
    # the signature to hide it.
    return list(cast("Iterable[uuid.UUID]", qs[:limit]))


def sweep_pending_forget_all(limit: int = SWEEP_BATCH_SIZE) -> dict:
    """Sweep every forgotten user whose erasure is not finished.

    Returns a summary dict for the task's return value / logs. One bad user
    is counted and skipped: a statutory erasure for person B must not be
    blocked by whatever is wrong with person A's row.
    """

    user_ids = pending_forget_all_user_ids(limit)
    swept = 0
    entries = 0
    errors = 0
    for user_id in user_ids:
        try:
            result = sweep_forget_all(user_id)
        except Exception:  # noqa: BLE001 — one bad row must not stall the queue
            logger.exception("identity.forget_all_sweep.user_failed user_id=%s", user_id)
            errors += 1
            continue
        if result.changed:
            swept += 1
            entries += result.entries_deleted

    summary = {
        "candidates": len(user_ids),
        "users_swept": swept,
        "entries_deleted": entries,
        "errors": errors,
    }
    logger.info("identity.forget_all_sweep.summary=%s", summary)
    return summary
