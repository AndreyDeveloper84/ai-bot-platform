"""Admin registration for Tenant (DRF-419 / Sprint 1 / A2).

Ported from Ayla ``origin/dev:tenants/admin.py``. Stripped of the
Unfold theming dependency — the platform admin is plain Django for
Sprint 1; we can layer Unfold in later if/when an admin polish sprint
arrives.

Key behaviour:
  * ``get_queryset`` uses ``Tenant.all_objects`` so deactivated tenants
    remain visible in admin (default ``Tenant.objects`` manager hides
    ``is_active=False`` rows from app code).
  * ``id``, ``created_at``, ``updated_at`` are read-only — the UUID is
    auto-generated and the timestamps are managed by ``auto_now*``.
  * Rows with ``is_system=True`` are protected from deletion (KB-RAG Sub-1,
    GH #114) — the ``global_kb`` corpus tenant must not vanish on a stray
    admin click. ``has_delete_permission`` hides the per-row delete button,
    ``delete_model`` and ``delete_queryset`` enforce the same rule
    server-side in case a custom action bypasses the UI.
"""

from __future__ import annotations

from django.contrib import admin
from django.core.exceptions import PermissionDenied

from apps.tenancy.models import Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "is_active",
        "is_system",
        "shadow_mode",
        "telegram_bot_token_masked",
        "created_at",
    )
    list_filter = ("is_active", "is_system", "shadow_mode")
    list_editable = ("shadow_mode",)
    search_fields = ("name", "slug")
    readonly_fields = ("id", "is_system", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("id", "slug", "name", "is_active", "is_system")}),
        (
            "Sprint 8 shadow-mode",
            {
                "fields": ("shadow_mode",),
                "description": (
                    "When checked, the orchestrator writes shadow rows but "
                    "does NOT send outbound messages to the user. See "
                    "docs/runbooks/shadow-mode-launch.md before flipping in prod."
                ),
            },
        ),
        (
            "Phase 1 — Telegram channel",
            {
                "fields": ("telegram_bot_token", "telegram_webhook_secret"),
                "description": (
                    "Per-tenant Telegram bot credentials. Token is from "
                    "@BotFather; webhook_secret is operator-generated via "
                    "secrets.token_urlsafe(32) and registered with "
                    "Telegram's setWebhook. The list view shows only the "
                    "last 4 token characters; full value is editable here. "
                    "See docs/runbooks/telegram-bot-onboarding.md."
                ),
            },
        ),
        (
            "Системное",
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="Telegram token (last 4)")
    def telegram_bot_token_masked(self, obj: Tenant) -> str:
        """Admin column: never expose the full Telegram bot token.

        Delegates to :meth:`Tenant._mask_telegram_token` so the masking
        rule lives in one place (model + admin can't drift).
        """
        return obj._mask_telegram_token() or "—"

    def get_queryset(self, request):
        # Admin must see deactivated tenants too — use all_objects manager.
        return Tenant.all_objects.all()

    def has_delete_permission(self, request, obj=None):
        # When obj is None Django asks "may this user delete *anything* here?"
        # to decide whether to render the changelist's "Delete selected" action.
        # We return True so the dropdown still appears for regular tenants;
        # the per-row enforcement happens in delete_model / delete_queryset.
        if obj is not None and getattr(obj, "is_system", False):
            return False
        return super().has_delete_permission(request, obj)

    def delete_model(self, request, obj):
        if getattr(obj, "is_system", False):
            raise PermissionDenied(
                "System tenants cannot be deleted from admin. "
                "Clear `is_system` first or remove the row via shell with explicit intent."
            )
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        # Refuse the whole batch if any row is a system tenant — partial
        # deletes are worse than full refusal here (operator gets a clear
        # error instead of mixed success).
        if queryset.filter(is_system=True).exists():
            raise PermissionDenied(
                "Selection includes system tenants. Remove them from the "
                "selection or clear `is_system` first."
            )
        super().delete_queryset(request, queryset)
