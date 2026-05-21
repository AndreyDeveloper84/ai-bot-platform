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
    list_display = (
        "created_at",
        "tenant",
        "action",
        "target",
        "target_id",
        "actor_id",
        "is_archived",
    )
    # is_archived filter exposes soft-deleted rows in admin without
    # losing the default "live rows only" view — operators can flip
    # the filter to inspect retention residue (DRF-851 / PI1).
    list_filter = ("is_archived", "action", "target", "tenant")
    # Audit retro B2: ``target_id`` / ``actor_id`` are UUIDField.
    # Pre-fix they were in ``search_fields``; Django admin builds
    # ``WHERE target_id ILIKE '%foo%'`` against the UUID column which
    # Postgres rejects with ``invalid input syntax for type uuid`` the
    # moment an operator types anything that isn't a complete UUID.
    # Forensic search was effectively broken. Post-fix only string-
    # typed columns participate in the substring search; admins
    # filtering by id should use the URL query string with an exact
    # UUID value (``?target_id=<uuid>``).
    search_fields = ("action", "target")
    readonly_fields = (
        "id",
        "tenant",
        "actor_id",
        "action",
        "target",
        "target_id",
        "payload",
        "created_at",
        "is_archived",
        "archived_at",
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
