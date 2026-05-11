"""Read-only Event admin (DRF-429 / E0).

Events are telemetry — admin can view + filter + search, never edit
or delete. Retention will be added in Sprint 5 when replay fixtures
need a separate retention policy from raw events.
"""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest

from apps.events.models import Event


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "tenant", "event_type", "trace_id")
    list_filter = ("event_type", "tenant")
    search_fields = ("event_type", "trace_id")
    readonly_fields = ("id", "tenant", "event_type", "payload", "trace_id", "created_at")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False
