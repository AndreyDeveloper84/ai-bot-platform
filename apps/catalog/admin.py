"""Read-only Catalog mirror admin (DRF-572 / Sprint 7 / C1).

Catalog mirrors are derived state — the source of truth lives in mysite,
the platform writes only through the catalog sync (C-track). Manual
edits would silently rot on the next sync overwrite. Admin is view-only
for forensics; controlled mutation (force resync) lands in C6 (DRF-576)
as an admin action.
"""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest

from apps.catalog.models import (
    CatalogFaq,
    CatalogHelpArticle,
    CatalogMaster,
    CatalogService,
    MasterService,
)


class _MirrorAdminBase(admin.ModelAdmin):
    list_filter = ("tenant", "external_updated_at")
    date_hierarchy = "external_updated_at"
    ordering = ("-external_updated_at",)

    def get_queryset(self, request):
        return self.model.all_tenants.all()

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


@admin.register(CatalogService)
class CatalogServiceAdmin(_MirrorAdminBase):
    list_display = ("slug", "name", "tenant", "is_active", "is_popular", "synced_at")
    search_fields = ("slug", "name", "external_id")


@admin.register(CatalogMaster)
class CatalogMasterAdmin(_MirrorAdminBase):
    list_display = (
        "name",
        "specialization",
        "tenant",
        "is_active",
        "invite_status",
        "mode",
        "synced_at",
    )
    list_filter = ("tenant", "is_active", "invite_status", "mode", "external_updated_at")  # type: ignore[assignment]
    search_fields = ("name", "specialization", "external_id", "max_handle")


@admin.register(MasterService)
class MasterServiceAdmin(admin.ModelAdmin):
    list_display = ("master", "service", "tenant", "created_at")
    list_filter = ("tenant",)
    search_fields = ("master__name", "service__name")
    raw_id_fields = ("master", "service", "created_by")

    def get_queryset(self, request):
        return self.model.all_tenants.all().select_related("master", "service", "tenant")


@admin.register(CatalogFaq)
class CatalogFaqAdmin(_MirrorAdminBase):
    list_display = ("question", "category_slug", "tenant", "synced_at")
    search_fields = ("question", "answer", "external_id")


@admin.register(CatalogHelpArticle)
class CatalogHelpArticleAdmin(_MirrorAdminBase):
    list_display = ("question", "tenant", "is_active", "order", "synced_at")
    search_fields = ("question", "answer", "external_id")
