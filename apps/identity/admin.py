"""BotUser admin (DRF-434 / Sprint 2 / A2).

Admin surface for support staff who need to look up a user across all
tenants (cross-tenant access is the whole point — admin is the escape
hatch). Read-mostly: edits to ``client_name`` / ``phone`` / ``context``
are allowed for manual data fixes, everything else is read-only.
"""

from __future__ import annotations

from django.contrib import admin

from apps.identity.models import BotUser


@admin.register(BotUser)
class BotUserAdmin(admin.ModelAdmin):
    list_display = ("channel", "channel_user_id", "display_name", "tenant", "last_seen")
    list_filter = ("channel", "tenant")
    search_fields = ("channel_user_id", "phone", "display_name", "client_name")
    readonly_fields = (
        "id",
        "tenant",
        "channel",
        "channel_user_id",
        "first_seen",
        "last_seen",
    )
    fieldsets = (
        (None, {"fields": ("id", "tenant", "channel", "channel_user_id")}),
        (
            "Identity (editable for manual fixes)",
            {"fields": ("display_name", "client_name", "phone", "chat_id")},
        ),
        (
            "Personalisation",
            {"fields": ("timezone", "context"), "classes": ("collapse",)},
        ),
        (
            "Системное",
            {"fields": ("first_seen", "last_seen"), "classes": ("collapse",)},
        ),
    )

    def get_queryset(self, request):
        # Admin must see all tenants for support cases; the
        # TenantScopedManager filter would hide rows otherwise.
        return BotUser.all_tenants.all()
