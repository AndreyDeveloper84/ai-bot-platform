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
        help_text="Operator working on this task. NULL while in OPEN. "
        "SET_NULL on user departure keeps the task in the queue for reassign.",
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
        ]

    def __str__(self) -> str:
        return f"AdminTask[{self.task_type}/{self.priority}/{self.status}]"
