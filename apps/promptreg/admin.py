"""Prompt registry admin (DRF-477 / Sprint 4 / A1).

Phase-1 admin actions (publish/unpublish/diff/canary slider) ship in
Sprint 4 / A7. Sprint 4 / A1 lands the read-side basics so the
operator can browse and edit prompts.
"""

from __future__ import annotations

from django.contrib import admin

from apps.promptreg.models import PromptVersion


@admin.register(PromptVersion)
class PromptVersionAdmin(admin.ModelAdmin):
    list_display = (
        "skill_name",
        "version",
        "is_active",
        "traffic_percent",
        "tenant",
        "created_by",
        "published_at",
        "created_at",
    )
    list_filter = ("is_active", "skill_name", "tenant")
    search_fields = ("skill_name", "body")
    readonly_fields = ("id", "created_at", "published_at")
    ordering = ("-created_at",)

    def get_queryset(self, request):  # type: ignore[override]
        # Admin spans tenants — surface every row to superusers.
        return PromptVersion.all_tenants.all()
