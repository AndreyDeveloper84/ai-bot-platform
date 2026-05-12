"""BotUser admin (DRF-434 / Sprint 2 / A2).

Admin surface for support staff who need to look up a user across all
tenants (cross-tenant access is the whole point — admin is the escape
hatch). Read-mostly: edits to ``client_name`` / ``phone`` / ``context``
are allowed for manual data fixes, everything else is read-only.
"""

from __future__ import annotations

from django.contrib import admin

from apps.identity.models import BotUser, ClientProfile


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


@admin.register(ClientProfile)
class ClientProfileAdmin(admin.ModelAdmin):
    """Read-only forensic view of computed RFM/LTV/tier per bot_user.

    All fields are derived by `apps.identity.services` — no manual edits.
    Recompute via Celery beat (P7) or `booking_completed` signal (P8).
    """

    list_display = (
        "bot_user",
        "tenant",
        "rfm_segment",
        "loyalty_tier",
        "lifecycle_stage",
        "recency_days",
        "frequency_visits",
        "monetary_total",
        "last_recomputed_at",
    )
    list_filter = ("rfm_segment", "loyalty_tier", "lifecycle_stage", "tenant")
    search_fields = (
        "bot_user__channel_user_id",
        "bot_user__phone",
        "bot_user__client_name",
    )
    readonly_fields = (
        "bot_user",
        "tenant",
        "recency_days",
        "frequency_visits",
        "monetary_total",
        "rfm_segment",
        "ltv",
        "predicted_ltv_12m",
        "churn_risk",
        "lifecycle_stage",
        "avg_visit_interval_days",
        "favorite_service_id",
        "favorite_category_id",
        "preferred_master_id",
        "loyalty_tier",
        "last_recomputed_at",
    )

    def get_queryset(self, request):
        return ClientProfile.all_tenants.all()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
