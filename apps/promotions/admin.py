"""Django admin for :class:`Promotion` (DRF-842 / Phase 1 / B6).

Promotions are operator-managed (unlike the catalog mirror tables,
which are auto-synced and admin-read-only). The basic ModelAdmin
below gives ops a place to add / edit / disable codes without a
manage.py shell.

### Why not list `applicable_service_ids` in `list_filter`

JSONField doesn't render usefully in Django's stock list filter — the
distinct-values query for a JSON column varies per backend and the
result set ("which combinations of UUIDs have been used") isn't
operator-meaningful. Filtering by ``tenant`` / ``is_active`` covers
the day-to-day workflow.
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from apps.promotions.models import Promotion


@admin.register(Promotion)
class PromotionAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "discount_percent",
        "is_active",
        "valid_from",
        "valid_to",
        "tenant",
        "created_at",
    )
    list_filter = ("is_active", "tenant")
    search_fields = ("code", "notes")
    readonly_fields = ("id", "created_at")
    ordering = ("-created_at",)

    def get_queryset(self, request: HttpRequest) -> Any:
        # Admin must see across tenants — use the escape-hatch manager.
        return Promotion.all_tenants.all()
