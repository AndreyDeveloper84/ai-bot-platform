"""AdminTask admin (DRF-464 / Sprint 3 / C1).

Operator-facing UI for working through the handoff queue. Most fields
are readonly — transcript_snapshot is forensic, bot_user / tenant /
conversation never change post-creation. The operator can edit:

* ``status`` — flip OPEN → IN_PROGRESS → RESOLVED|CANCELLED
* ``assigned_to`` — pick up a task from the unassigned queue
* ``resolution_note`` — free-form operator note on close

``resolved_at`` is auto-stamped on RESOLVED transition by the C3
services layer; admin doesn't expose it for direct edit.
"""

from __future__ import annotations

from django.contrib import admin
from django.utils import timezone

from apps.handoff.models import AdminTask


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
        # When the operator flips status to RESOLVED, stamp resolved_at if
        # not already set. CANCELLED leaves resolved_at NULL — semantically
        # different from "completed work".
        if change and obj.status == AdminTask.Status.RESOLVED and obj.resolved_at is None:
            obj.resolved_at = timezone.now()
        super().save_model(request, obj, form, change)
