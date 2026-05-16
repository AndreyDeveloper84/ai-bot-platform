"""Read-only admin views for BookingRequest + BookingReminder (B2).

Read-only on purpose: these tables are written exclusively by the
webhook handler (B2) and the booking-skill tools (B3). Hand-editing
in /admin/ would silently bypass the idempotency contract and the
event-emission side effects. Operators who need to mutate rows do so
through a manage.py shell with audit context.
"""

from __future__ import annotations

from django.contrib import admin

from apps.booking.models import BookingReminder, BookingRequest


@admin.register(BookingRequest)
class BookingRequestAdmin(admin.ModelAdmin):
    list_display = (
        "client_name",
        "service_name",
        "master_name",
        "source",
        "is_processed",
        "created_at",
        "tenant",
    )
    list_filter = ("source", "is_processed", "tenant")
    search_fields = ("client_name", "client_phone", "service_name", "comment")
    readonly_fields = tuple(f.name for f in BookingRequest._meta.fields)
    ordering = ("-created_at",)

    def has_add_permission(self, request) -> bool:  # type: ignore[no-untyped-def]
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # type: ignore[no-untyped-def]
        return False

    def get_queryset(self, request):  # type: ignore[no-untyped-def]
        # Admin must see across tenants — use the escape-hatch manager.
        return BookingRequest.all_tenants.all()


@admin.register(BookingReminder)
class BookingReminderAdmin(admin.ModelAdmin):
    list_display = (
        "yclients_record_id",
        "kind",
        "status",
        "visit_at",
        "scheduled_at",
        "sent_at",
        "master_name",
        "service_name",
        "tenant",
    )
    list_filter = ("status", "kind", "tenant")
    search_fields = ("yclients_record_id", "master_name", "service_name")
    readonly_fields = tuple(f.name for f in BookingReminder._meta.fields)
    ordering = ("scheduled_at",)

    def has_add_permission(self, request) -> bool:  # type: ignore[no-untyped-def]
        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # type: ignore[no-untyped-def]
        return False

    def get_queryset(self, request):  # type: ignore[no-untyped-def]
        return BookingReminder.all_tenants.all()
