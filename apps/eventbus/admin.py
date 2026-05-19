"""DomainEvent admin. Read-only telemetry + dead-letter replay action."""

from __future__ import annotations

from django.contrib import admin, messages
from django.http import HttpRequest

from apps.eventbus.dispatcher import replay_dead_letter
from apps.eventbus.models import DomainEvent


class DeadLetterFilter(admin.SimpleListFilter):
    """Slice the list by dead-letter status — distinct from is_dispatched."""

    title = "Dead-letter"
    parameter_name = "dlq"

    def lookups(self, request, model_admin):
        return (("yes", "Dead-letter"), ("no", "Live"))

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(dead_lettered_at__isnull=False)
        if self.value() == "no":
            return queryset.filter(dead_lettered_at__isnull=True)
        return queryset


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
        "is_dead_letter",
    )
    list_filter = (
        DeadLetterFilter,
        "event_name",
        "is_dispatched",
        "tenant",
        "event_version",
    )
    search_fields = ("event_id", "event_name", "correlation_id", "causation_id")
    readonly_fields = tuple(f.name for f in DomainEvent._meta.fields)
    date_hierarchy = "occurred_at"
    ordering = ("-event_id",)
    actions = ("replay_selected_dead_letters",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    @admin.action(description="Replay selected dead-letter events")
    def replay_selected_dead_letters(self, request, queryset):
        ids = list(queryset.values_list("event_id", flat=True))
        reset_count = replay_dead_letter(ids)
        if reset_count:
            self.message_user(
                request,
                f"Reset {reset_count} dead-letter row(s); dispatcher will re-claim on next tick.",
                level=messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                "No dead-letter rows in selection — nothing reset.",
                level=messages.WARNING,
            )
