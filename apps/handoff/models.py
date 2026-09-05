"""Admin handoff task registry (DRF-464 / Sprint 3 / C1).

Records every moment the bot stepped out of an autonomous flow and
asked a human operator to take over. Phase-0 design §F0.9 lists the
trigger sources:

* ``HANDOFF``        — user typed "human" / pushed the handoff button.
* ``COMPLAINT``      — sentiment classifier flagged a complaint.
* ``MEDICAL_RED_FLAG`` — health-screening contraindication detected.
* ``MANUAL``         — operator created the task from the admin.

The model is the **forensic source of truth** for handoffs. The bot
records the conversation transcript snapshot at the moment of handoff
(C2 packager fills it) so the operator can reproduce the exact state
the user was in even if the conversation continues live.

### FK strategy

* ``tenant`` PROTECT — accidental tenant deletion must not vapourise
  open AdminTasks across the org.
* ``bot_user`` PROTECT — a Sprint 2.5 H1 soft-delete on BotUser keeps
  the AdminTask intact. The transcript_snapshot already carries the
  user-identifying context the operator needs.
* ``conversation`` PROTECT — same logic; conversations participating
  in handoffs must survive cleanup tasks.
* ``assigned_to`` SET_NULL on auth.User — reassign on operator
  departure is fine; the task itself is forensic.

### Status machine

OPEN → IN_PROGRESS → RESOLVED|CANCELLED. The admin UI flips status;
C3 services formalise the transitions. ``resolved_at`` is stamped on
the OPEN/IN_PROGRESS → RESOLVED transition.

### Addressing (DRF-1488)

Every task on the pilot between 11.08 and 04.09.2026 carried
``assigned_to = None`` — all ten of them. Nobody was ever named, so
nobody was late, so nothing was ever chased: one task sat open for
20 hours and the client's bot stayed muted for all of it (DRF-1015
lifts the mute only when the task closes).

A task is therefore **addressed at creation**, on one of two axes:

* ``assigned_to`` — a named operator from the duty roster
  (``HANDOFF_DUTY_OPERATORS``); the least-loaded one wins.
* ``assigned_queue`` — an explicit duty queue (``HANDOFF_DUTY_QUEUE``)
  when no roster is configured. Assignment then happens on pickup:
  the operator sets ``assigned_to`` in the admin, which stamps
  ``claimed_at``.

Neither configured is a boot-time error (``apps.handoff.checks``), so
an un-addressed task cannot be produced by a running deployment —
"a task without an addressee is not a task that was filed".

``claimed_at`` and ``pickup_escalated_at`` make the wait measurable:
the sweep in :mod:`apps.handoff.escalation` escalates a task nobody
claimed within ``HANDOFF_PICKUP_SLA_MINUTES``, exactly once.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.tenancy.managers import TenantScopedManager


class AdminTask(models.Model):
    """One row per bot-to-operator handoff.

    Created exclusively through the handoff service (Sprint 3 / C3+D3);
    direct ``AdminTask.objects.create`` is permitted in tests but not
    in production paths.
    """

    class TaskType(models.TextChoices):
        HANDOFF = "handoff", "Handoff"
        COMPLAINT = "complaint", "Complaint"
        MEDICAL_RED_FLAG = "medical_red_flag", "Medical red flag"
        MANUAL = "manual", "Manual"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        RESOLVED = "resolved", "Resolved"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="admin_tasks",
        help_text="Tenant the task belongs to. PROTECT so accidental "
        "tenant delete cannot vapourise open operator queues.",
    )
    bot_user = models.ForeignKey(
        "identity.BotUser",
        on_delete=models.PROTECT,
        related_name="admin_tasks",
        help_text="The user whose conversation triggered the handoff. "
        "PROTECT so soft-delete on the user (Sprint 2.5 H1) keeps the "
        "forensic task intact.",
    )
    conversation = models.ForeignKey(
        "conversations.Conversation",
        on_delete=models.PROTECT,
        related_name="admin_tasks",
        help_text="The conversation snapshot anchor. PROTECT — handoff "
        "tasks survive conversation cleanup.",
    )
    task_type = models.CharField(
        max_length=32,
        choices=TaskType.choices,
        help_text="Why the task exists. See TaskType for the 4 Phase-0 categories.",
    )
    priority = models.CharField(
        max_length=16,
        choices=Priority.choices,
        default=Priority.NORMAL,
        help_text="Operator-queue priority. MEDICAL_RED_FLAG ships URGENT; "
        "COMPLAINT ships HIGH; HANDOFF ships NORMAL by default.",
    )
    transcript_snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text="Frozen messages + ClientProfile snapshot at the moment of "
        "creation. Filled by the C2 packager. Even if the underlying "
        "Conversation grows after handoff, this dict reflects the operator's "
        "starting context.",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_admin_tasks",
        help_text="Named operator working on this task. NULL means the task "
        "is addressed to `assigned_queue` instead and still waits for someone "
        "to pick it up. SET_NULL on user departure drops the task back to its "
        "queue rather than losing it.",
    )
    assigned_queue = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="Duty queue the task is addressed to when no named operator "
        "was resolvable (DRF-1488). Empty together with `assigned_to` means "
        "the task reached nobody — a configuration error, not a state.",
    )
    claimed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When an operator actually took the task (assigned_to set). "
        "NULL = still waiting in the queue; the pickup sweep measures the wait "
        "from `created_at` to this.",
    )
    pickup_escalated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Stamped ONCE by the pickup sweep when nobody claimed the "
        "task within HANDOFF_PICKUP_SLA_MINUTES. Non-NULL is what makes the "
        "escalation fire exactly once.",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
        help_text="OPEN → IN_PROGRESS → RESOLVED|CANCELLED. Transitions "
        "formalised in C3 handoff services; admin UI flips this.",
    )
    reason = models.TextField(
        blank=True,
        default="",
        help_text="Why the bot triggered the handoff. Free-form. Operator-facing.",
    )
    resolution_note = models.TextField(
        blank=True,
        default="",
        help_text="Operator's note on resolve / cancel. Forensic + customer-service trail.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Stamped on the {OPEN, IN_PROGRESS} → RESOLVED transition. "
        "NULL for OPEN/IN_PROGRESS/CANCELLED rows.",
    )

    # Default manager scopes to current_tenant(); leakage scanner picks this up.
    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Admin task"
        verbose_name_plural = "Admin tasks"
        ordering = ["-created_at"]
        indexes = [
            # Operator queue view: open tasks in this tenant, newest first.
            models.Index(fields=["tenant", "status", "-created_at"]),
            # My-queue view: tasks assigned to me, by status.
            models.Index(fields=["assigned_to", "status"]),
            # DRF-1488 pickup sweep: cross-tenant, «open and old», so the
            # tenant-first index above cannot serve it.
            models.Index(fields=["status", "created_at"], name="handoff_task_status_age_idx"),
        ]

    def __str__(self) -> str:
        return f"AdminTask[{self.task_type}/{self.priority}/{self.status}]"

    @property
    def is_addressed(self) -> bool:
        """True when the task reached SOMEONE — a person or a named queue.

        The pilot’s failure mode was a task addressed to neither, which
        reads as «filed» in the admin and is in fact a request dropped
        on the floor. ``create_admin_task`` refuses to treat such a row as a
        filed task (DRF-1488).
        """

        return bool(self.assigned_to_id or self.assigned_queue)

    @property
    def addressee(self) -> str:
        """Human-readable addressee for logs, admin columns and the CLI."""

        if self.assigned_to_id is not None:
            return getattr(self.assigned_to, "username", "") or f"user:{self.assigned_to_id}"
        return f"queue:{self.assigned_queue}" if self.assigned_queue else ""


class HandoffSilenceNotice(models.Model):
    """One muted dialog, and what the person on the other end was told (DRF-1486).

    DRF-1015 mutes the bot while an operator drives ANY dialog of the same
    channel identity — deliberately, so a human and a bot never answer the
    same person at once. On 04.09.2026 that worked exactly as designed and
    still produced 1h24m of silence the client could not interpret: they had
    written to a SALON bot, and the bot that went quiet was the GLOBAL one.
    Nothing connected the two events for them.

    This row is the memory that makes the explanation possible and keeps it
    to one message per episode:

    * ``announced_here`` — the handoff confirmation («передаю
      менеджеру») was delivered in THIS dialog, so the silence needs no
      introduction, only a reminder. False means the mute travelled here
      from another bot and must say so.
    * ``silence_notified_at`` — set the moment we told the person the bot
      is muted. Non-NULL is what stops the second, third and fifth inbound
      message from repeating it.
    * ``released_at`` — set when the mute lifts and we said so. A row with
      it NULL is the *current* episode; the partial unique constraint allows
      exactly one such row per conversation, and closed rows stay as the
      forensic record of what the client actually saw.

    No tenant FK on purpose: the row is anchored by the conversation, which
    carries the tenant (the sentinel one, for the global dialog). A second
    tenant column would give the leakage scanner a copy of the truth to
    disagree with.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        "conversations.Conversation",
        on_delete=models.PROTECT,
        related_name="handoff_silence_notices",
        help_text="The dialog that went silent. PROTECT for the same reason "
        "AdminTask.conversation is PROTECT — this is forensic evidence.",
    )
    bot_user = models.ForeignKey(
        "identity.BotUser",
        on_delete=models.PROTECT,
        related_name="handoff_silence_notices",
        help_text="Whose dialog it is. Carries channel + channel_user_id, "
        "which is how the release check re-asks «is this person still being "
        "served by a human?».",
    )
    chat_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Channel chat the notices go to. Stored rather than "
        "re-derived: the release message is sent long after the inbound turn "
        "that carried it.",
    )
    announced_here = models.BooleanField(
        default=False,
        help_text="True when the handoff confirmation was delivered in THIS "
        "dialog. Picks which of the two silence wordings the person reads.",
    )
    silence_notified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the person was told the bot is muted. NULL = not yet; "
        "non-NULL = never again for this episode.",
    )
    released_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the mute lifted and the person was told the bot is "
        "back. NULL marks the one open episode for this conversation.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Handoff silence notice"
        verbose_name_plural = "Handoff silence notices"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["conversation"],
                condition=models.Q(released_at__isnull=True),
                name="handoff_one_open_silence_notice_per_conversation",
            )
        ]
        indexes = [
            models.Index(fields=["bot_user", "released_at"]),
        ]

    def __str__(self) -> str:
        state = "open" if self.released_at is None else "released"
        return f"HandoffSilenceNotice[{self.conversation_id}/{state}]"
