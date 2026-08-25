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
from apps.catalog.provenance import MasterServiceSource, master_service_write


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
    """The one catalog admin that is NOT read-only -- and, until DRF-975, the
    second unaudited write path into ``catalog_masterservice``.

    Every other mirror here extends ``_MirrorAdminBase``, which denies add /
    change / delete outright. This one does not, and it never wrote an audit
    row either: a superuser could create master-service edges through
    ``/admin/`` and leave exactly the same forensic hole as the 2026-07-22
    script. That is not hypothetical on a pilot host where operators have
    admin accounts.

    It is wired rather than locked down, because the ability to fix one edge by
    hand is genuinely useful during a pilot. What changed is that it now has to
    say who it is: ``save_model`` / ``delete_*`` enter the provenance context,
    so the row is stamped ``source="django_admin"`` and the signals in
    ``apps.catalog.signals`` emit ``master.service_edge_created`` /
    ``master.service_edge_deleted``. Without the context the model gate would
    refuse the write and the admin would 500 -- correct, but a worse
    experience than an audited success.

    ``created_by_actor_id`` stays NULL here on purpose: the admin actor is an
    ``auth.User`` (integer pk), not the ``identity.BotUser`` UUID that column
    holds. The acting username goes into the audit payload's ``reason``
    instead, which is honest about what we actually know.
    """

    list_display = ("master", "service", "tenant", "source", "created_at")
    list_filter = ("tenant", "source")
    search_fields = ("master__name", "service__name")
    raw_id_fields = ("master", "service", "created_by")
    # Provenance is written by the platform, never typed by a human -- an
    # editable ``source`` would let an admin relabel a hand-made row as
    # ``catalog_sync`` and undo the whole point.
    readonly_fields = ("source", "created_by_actor_id", "created_at", "updated_at")

    def get_queryset(self, request):
        return self.model.all_tenants.all().select_related("master", "service", "tenant")

    def _ctx(self, request):
        return master_service_write(
            MasterServiceSource.DJANGO_ADMIN,
            reason=f"django admin user={getattr(request.user, 'username', '?')}",
        )

    def save_model(self, request, obj, form, change):
        with self._ctx(request):
            super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        with self._ctx(request):
            super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        with self._ctx(request):
            super().delete_queryset(request, queryset)


@admin.register(CatalogFaq)
class CatalogFaqAdmin(_MirrorAdminBase):
    list_display = ("question", "category_slug", "tenant", "synced_at")
    search_fields = ("question", "answer", "external_id")


@admin.register(CatalogHelpArticle)
class CatalogHelpArticleAdmin(_MirrorAdminBase):
    list_display = ("question", "tenant", "is_active", "order", "synced_at")
    search_fields = ("question", "answer", "external_id")
