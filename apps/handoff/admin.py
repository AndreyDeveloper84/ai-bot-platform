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

from django.contrib import admin

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
        "assigned_to",
        "created_at",
        "resolved_at",
    )
    list_filter = ("status", "priority", "task_type", "tenant")
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
        "resolution_note",
        "created_at",
        "updated_at",
        "resolved_at",
    )
    ordering = ("-created_at",)

    @admin.display(description="ID")
    def id_short(self, obj: AdminTask) -> str:
        return str(obj.id)[:8]

    def get_queryset(self, request):  # type: ignore[override]
        # Admin spans tenants — use `all_tenants` instead of the default
        # tenant-scoped manager so superusers see the whole queue.
        return AdminTask.all_tenants.all()

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
