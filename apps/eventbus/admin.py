"""Read-only DomainEvent admin. Outbox rows are append-only telemetry."""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest

from apps.eventbus.models import DomainEvent


@admin.register(DomainEvent)
class DomainEventAdmin(admin.ModelAdmin):
    list_display = (
        "event_id",
        "occurred_at",
        "tenant",
        "event_name",
        "event_version",
        "is_dispatched",
        "dispatch_attempts",
    )
    list_filter = ("event_name", "is_dispatched", "tenant", "event_version")
    search_fields = ("event_id", "event_name", "correlation_id", "causation_id")
    readonly_fields = tuple(f.name for f in DomainEvent._meta.fields)
    date_hierarchy = "occurred_at"
    ordering = ("-event_id",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False
