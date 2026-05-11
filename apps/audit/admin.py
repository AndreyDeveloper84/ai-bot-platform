"""Read-only AuditLog admin (DRF-426 / B1).

Audit data is forensic — admin can view + filter + export but never
edit, add, or delete. Retention is handled by
``apps.audit.tasks.cleanup_old_audit_logs`` (Celery task), not by
human cleanup.
"""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest

from apps.audit.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "tenant", "action", "target", "target_id", "actor_id")
    list_filter = ("action", "target", "tenant")
    search_fields = ("action", "target", "target_id", "actor_id")
    readonly_fields = (
        "id",
        "tenant",
        "actor_id",
        "action",
        "target",
        "target_id",
        "payload",
        "created_at",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    # Admin must see every tenant's rows for forensic work.
    def get_queryset(self, request):
        return AuditLog.all_tenants.all()

    # Forbid all mutation paths.
    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False
