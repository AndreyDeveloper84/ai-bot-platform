"""Read-only KbDocument admin (DRF-558 / Sprint 7 / K1).

KB documents flow in from the catalog sync (C-track) + manual seed
management commands (K8). Manual edits in admin would split brain
with the upstream mysite row and silently rot the FAQ skill's
retrieved answers — admin is view-only for forensics and triage.

Mutation surface (force resync, manual reindex) lands in K9
(DRF-567) via admin actions on a separate KB collection-status view.
"""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest

from apps.kb.models import KbDocument


@admin.register(KbDocument)
class KbDocumentAdmin(admin.ModelAdmin):
    list_display = (
        "doc_type",
        "source_uri",
        "version",
        "tenant",
        "embedded_at",
        "updated_at",
    )
    list_filter = ("doc_type", "tenant", "locale", "embedded_at")
    search_fields = ("source_uri", "content")
    readonly_fields = (
        "id",
        "tenant",
        "doc_type",
        "source_uri",
        "version",
        "locale",
        "metadata",
        "checksum",
        "content",
        "embedded_at",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "updated_at"
    ordering = ("-updated_at",)

    # Admin spans tenants for forensic work.
    def get_queryset(self, request):
        return KbDocument.all_tenants.all()

    # No mutation from Django admin. K8 / K9 give the operator
    # controlled paths for reindex + manual seed.
    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False
