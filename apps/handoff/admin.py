"""AdminTask admin (DRF-464 / Sprint 3 / C1; close path reworked DRF-980).

Operator-facing UI for working through the handoff queue. Most fields
are readonly — transcript_snapshot is forensic, bot_user / tenant /
conversation never change post-creation. The operator can edit:

* ``status`` — flip OPEN → IN_PROGRESS → RESOLVED|CANCELLED
* ``assigned_to`` — pick up a task from the unassigned queue
* ``resolution_note`` — free-form operator note on close

Closing a task (RESOLVED or CANCELLED) goes through the handoff
service layer (:mod:`apps.handoff.services`), which stamps metadata,
returns the conversation to the bot (state → IDLE) and writes audit —
never through a bare field save, which would leave the dialog muted
forever (DRF-980).
"""

from __future__ import annotations

import logging

from django.contrib import admin, messages
from django.utils import timezone

from apps.handoff.assignment import claim
from apps.handoff.models import AdminTask
from apps.handoff.services import (
    cancel_admin_task,
    release_conversation_to_bot,
    resolve_admin_task,
)
from apps.tenancy.context import tenant_scope

logger = logging.getLogger(__name__)


@admin.register(AdminTask)
class AdminTaskAdmin(admin.ModelAdmin):
    list_display = (
        "id_short",
        "task_type",
        "priority",
        "status",
        "tenant",
        "addressee_display",
        "age_display",
        "overdue_display",
        "created_at",
        "resolved_at",
    )
    list_filter = ("status", "priority", "task_type", "tenant", "assigned_queue")
    search_fields = ("id", "reason", "resolution_note", "bot_user__channel_user_id")
    readonly_fields = (
        "id",
        "tenant",
        "bot_user",
        "conversation",
        "task_type",
        "priority",
        "transcript_snapshot",
        "reason",
        "created_at",
        "updated_at",
        "resolved_at",
        "claimed_at",
        "pickup_escalated_at",
    )
    fields = (
        "id",
        "tenant",
        "bot_user",
        "conversation",
        "task_type",
        "priority",
        "reason",
        "transcript_snapshot",
        "status",
        "assigned_to",
        "assigned_queue",
        "claimed_at",
        "pickup_escalated_at",
        "resolution_note",
        "created_at",
        "updated_at",
        "resolved_at",
    )
    ordering = ("-created_at",)

    @admin.display(description="ID")
    def id_short(self, obj: AdminTask) -> str:
        return str(obj.id)[:8]

    @admin.display(description="Адресат")
    def addressee_display(self, obj: AdminTask) -> str:
        """Who this task is for. DRF-1488 — the column the pilot never had.

        Ten tasks, ten times ``assigned_to = None``: the changelist showed a
        blank cell and nobody read a blank cell as «nobody will do this».
        Now a task always names either an operator or a duty queue, and the
        one state that means nobody is spelled out in words.
        """

        return obj.addressee or "НЕ НАЗНАЧЕН"

    @admin.display(description="Ждёт")
    def age_display(self, obj: AdminTask) -> str:
        """How long the task has been open — or how long it took to close.

        The pilot's spread was 0 seconds to 20 hours, and the changelist
        showed neither number: `created_at` and `resolved_at` were two
        timestamps an operator had to subtract in their head.
        """

        end = obj.resolved_at or timezone.now()
        minutes = max(0, int((end - obj.created_at).total_seconds() // 60))
        if minutes < 60:
            return f"{minutes} мин"
        hours, rest = divmod(minutes, 60)
        return f"{hours} ч {rest:02d} мин"

    @admin.display(description="Просрочка", boolean=True)
    def overdue_display(self, obj: AdminTask) -> bool:
        """True once the pickup sweep escalated this task (DRF-1488)."""

        return obj.pickup_escalated_at is not None

    def get_queryset(self, request):  # type: ignore[override]
        # Admin spans tenants — use `all_tenants` instead of the default
        # tenant-scoped manager so superusers see the whole queue.
        return AdminTask.all_tenants.all()

    def changelist_view(self, request, extra_context=None):  # type: ignore[override]
        # DRF-1023 — the whole admin is CROSS-TENANT (see get_queryset):
        # anyone logged in here sees every salon's tasks. The banner must
        # be seen by the next person BEFORE they hand an account to salon
        # staff — with it they would also get every other salon's data.
        # Tenant-restricted operator access is a separate task (DRF-1022).
        messages.warning(
            request,
            "ВНИМАНИЕ: админка кросс-тенантная — здесь видны задачи и данные "
            "ВСЕХ салонов. Учётную запись выдаём только внутренней команде; "
            "сотруднику салона она откроет чужие данные.",
        )
        return super().changelist_view(request, extra_context)

    def save_model(self, request, obj: AdminTask, form, change):  # type: ignore[override]
        """Route close transitions through the handoff services (DRF-980).

        A bare field save of ``status`` leaves the conversation in
        HUMAN_HANDOFF forever — the bot stays muted with no way back.
        So a transition INTO RESOLVED/CANCELLED is executed by
        :func:`resolve_admin_task` / :func:`cancel_admin_task` (status +
        stamps + conversation release + audit), and a re-save of an
        already-closed task still heals a stuck conversation.

        The service layer requires a tenant in scope, but the admin is
        cross-tenant (``get_queryset`` uses ``all_tenants``) — the scope
        is taken from the task itself, never from the request.

        The form-bound ``obj`` already carries the NEW status, so the
        service is called on a fresh DB instance: its status check would
        otherwise see the target status and no-op.
        """
        previous_status: str | None = None
        if change and obj.pk:
            previous_status = (
                AdminTask.all_tenants.filter(pk=obj.pk).values_list("status", flat=True).first()
            )

        # DRF-1488 — «assignment on pickup» is what makes queue addressing a
        # real address rather than a label. The operator taking a task out of
        # the duty queue does it by setting `assigned_to` here, and that act
        # stamps `claimed_at`, which is the one thing that stops the pickup
        # sweep from chasing a task somebody is already working.
        operator_taking = obj.assigned_to if change and obj.pk else None
        if operator_taking is not None and obj.claimed_at is None:
            fresh_for_claim = AdminTask.all_tenants.filter(pk=obj.pk).first()
            if fresh_for_claim is not None and claim(fresh_for_claim, operator_taking):
                obj.claimed_at = fresh_for_claim.claimed_at

        closed_states = (AdminTask.Status.RESOLVED, AdminTask.Status.CANCELLED)
        closing = previous_status is not None and previous_status not in closed_states
        if change and obj.status in closed_states:
            operator = getattr(request, "user", None)
            logger.info(
                "handoff.admin_close actor=%s task=%s conversation=%s from=%s to=%s tenant=%s",
                getattr(operator, "pk", "?"),
                obj.pk,
                obj.conversation_id,
                previous_status,
                obj.status,
                obj.tenant_id,
            )
            with tenant_scope(obj.tenant):
                fresh = AdminTask.all_tenants.get(pk=obj.pk)
                if obj.status == AdminTask.Status.RESOLVED:
                    resolve_admin_task(fresh, resolution_note=obj.resolution_note or "")
                    # Mirror the service-stamped values back onto the
                    # form-bound instance so super().save_model persists
                    # a consistent row.
                    obj.resolved_at = fresh.resolved_at
                    obj.resolution_note = fresh.resolution_note
                elif closing:
                    cancel_admin_task(fresh, resolution_note=obj.resolution_note or "")
                    obj.resolution_note = fresh.resolution_note
                else:
                    # Already-closed task re-saved (or reclassified
                    # RESOLVED ↔ CANCELLED): just make sure the dialog is
                    # not left muted.
                    release_conversation_to_bot(fresh)
        super().save_model(request, obj, form, change)
