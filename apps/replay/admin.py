"""Replay trace admin (DRF-497 / Sprint 5 / A1).

Read-only forensic view. Operators browse + inspect captured traces;
nobody edits replay rows directly — all mutations go through the
recorder + cleanup task.
"""

from __future__ import annotations

from django.contrib import admin

from apps.replay.models import ReplayTrace


@admin.register(ReplayTrace)
class ReplayTraceAdmin(admin.ModelAdmin):
    list_display = (
        "trace_id_short",
        "tenant",
        "redacted",
        "redaction_method",
        "captured_at",
        "expires_at",
    )
    list_filter = ("redacted", "redaction_method", "tenant")
    search_fields = ("trace_id", "id")
    readonly_fields = (
        "id",
        "tenant",
        "trace_id",
        "pipeline_steps",
        "redacted",
        "redaction_method",
        "captured_at",
        "expires_at",
    )
    ordering = ("-captured_at",)

    @admin.display(description="trace_id")
    def trace_id_short(self, obj: ReplayTrace) -> str:
        return obj.trace_id[:12] + ("…" if len(obj.trace_id) > 12 else "")

    def get_queryset(self, request):  # type: ignore[override]
        # Admin spans tenants — surface every row to superusers.
        return ReplayTrace.all_tenants.all()

    def has_add_permission(self, request):  # type: ignore[override]
        return False

    def has_change_permission(self, request, obj=None):  # type: ignore[override]
        return False

    def has_delete_permission(self, request, obj=None):  # type: ignore[override]
        # Cleanup goes through the daily Celery task, not the admin.
        return False
