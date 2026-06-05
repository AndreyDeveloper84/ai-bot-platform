"""Read-only Loyalty admin (Volna 4 — Phase 1.a).

Account + event history visible per tenant. Adjustments are NOT done
through Django admin — they go through the service-layer
``credit_points`` / ``manual_adjust`` (future PR) so audit + event log
stay in sync.
"""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest

from apps.loyalty.models import LoyaltyAccount, LoyaltyEvent


@admin.register(LoyaltyAccount)
class LoyaltyAccountAdmin(admin.ModelAdmin):
    list_display = (
        "customer",
        "tenant",
        "balance",
        "enrolled",
        "opted_out_at",
        "updated_at",
    )
    list_filter = ("tenant", "enrolled")
    search_fields = ("customer__id", "customer__phone")
    readonly_fields = tuple(f.name for f in LoyaltyAccount._meta.fields)
    ordering = ("-updated_at",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


@admin.register(LoyaltyEvent)
class LoyaltyEventAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_at",
        "tenant",
        "account",
        "event_type",
        "points_delta",
        "balance_after",
    )
    list_filter = ("event_type", "tenant")
    search_fields = ("account__customer__id", "booking__id", "reason")
    readonly_fields = tuple(f.name for f in LoyaltyEvent._meta.fields)
    date_hierarchy = "occurred_at"
    ordering = ("-occurred_at",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False
