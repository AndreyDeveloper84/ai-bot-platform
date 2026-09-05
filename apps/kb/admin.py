"""Read-only KbDocument admin + force-reindex action (DRF-558 / DRF-567).

KB documents flow in from the catalog sync (C-track) + manual seed
management commands (K8). Manual edits in admin would split brain
with the upstream mysite row and silently rot the FAQ skill's
retrieved answers — admin is view-only for forensics and triage.

K9 (DRF-567) adds an admin action to **trigger** reindex via the
K6 Celery task, without exposing edit forms. The pattern is:

* Select rows → action menu "Force reindex selected tenant(s)" →
  enqueues :func:`apps.kb.tasks.reindex_tenant_kb` per distinct tenant.
* Admin sees a Django message confirming "queued N tasks".
* The Celery worker (must be running) picks them up; admin can refresh
  to see ``embedded_at`` move.

We deliberately do NOT run the reindex synchronously from the admin
request — embedding hundreds of chunks would block the worker thread
for minutes. Enqueue + redirect.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.http import HttpRequest

from apps.kb.models import KbDocument
from apps.kb.tasks import reindex_tenant_kb


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
    actions = ("force_reindex_selected_tenants",)

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

    # ------------------------------------------------------------------
    # Admin actions
    # ------------------------------------------------------------------

    # DRF-1495: без ``permissions=`` Django отдаёт действие всякому, кто
    # открыл экран (``_filter_actions_by_permissions`` пропускает всё, у
    # чего нет ``allowed_permissions``) — включая роль «смотрящий»,
    # которой правки не положены вовсе. Реиндекс ставит в очередь
    # эмбеддинги (платные вызовы) по произвольным тенантам, то есть это
    # правка, а не чтение.
    #
    # ``permissions=("change",)`` здесь не годится: ``has_change_permission``
    # выше безусловно False, и действие умерло бы вместе с ролями. Поэтому
    # отдельный предикат — он спрашивает право на модель, а не разрешение
    # экрана: суперпользователь и роль «правящий» получают, «смотрящий» нет.
    def has_reindex_permission(self, request: HttpRequest) -> bool:
        return request.user.has_perm("kb.change_kbdocument")

    @admin.action(
        description="Force reindex selected tenant(s)",
        permissions=("reindex",),
    )
    def force_reindex_selected_tenants(self, request: HttpRequest, queryset) -> None:
        """Enqueue :func:`reindex_tenant_kb` per distinct tenant in the
        selected rows.

        Selecting 10 documents for the same tenant enqueues ONE task —
        the dedup is on tenant id, not row count. The K6 task walks
        every KbDocument under that tenant (optionally filtered by
        ``doc_type`` — admin doesn't pass it here; force-resync means
        "everything").
        """
        tenant_ids = sorted({str(row.tenant_id) for row in queryset})
        if not tenant_ids:
            self.message_user(
                request,
                "No tenants in selection — nothing to enqueue.",
                level=messages.WARNING,
            )
            return

        for tenant_id in tenant_ids:
            # `.delay` enqueues; the result is a Celery AsyncResult we
            # don't await. Admin returns immediately.
            reindex_tenant_kb.delay(tenant_id, force=True)

        self.message_user(
            request,
            f"Queued {len(tenant_ids)} reindex task(s). Monitor `embedded_at` to track progress.",
            level=messages.SUCCESS,
        )
