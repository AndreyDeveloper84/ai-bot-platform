"""Read-only WebhookJournal admin (DRF-423 / C1).

Forensic data — admin can view + filter + search, never edit. Retention
is per-row processed_at-based and lives in Sprint 5 replay infra.
"""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest

from apps.ingress.models import WebhookJournal


@admin.register(WebhookJournal)
class WebhookJournalAdmin(admin.ModelAdmin):
    list_display = (
        "received_at",
        "channel",
        "external_event_id",
        "resolved_tenant",
        "processed_at",
        "trace_id",
    )
    list_filter = ("channel", "resolved_tenant")
    search_fields = ("external_event_id", "trace_id")
    readonly_fields = (
        "id",
        "channel",
        "external_event_id",
        "raw_payload",
        "resolved_tenant",
        "trace_id",
        "received_at",
        "processed_at",
    )
    date_hierarchy = "received_at"
    ordering = ("-received_at",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False
