"""Django-admin registration for orders (DRF-843 / Phase 1 / B7).

Read-only admin — payment artefacts must not be hand-edited from the
Django admin (any value change requires an audit-trailed code path).
"""

from __future__ import annotations

from django.contrib import admin

from apps.orders.models import Order, PaymentEvent


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "id",
        "tenant",
        "kind",
        "amount_rub",
        "status",
        "created_at",
        "paid_at",
    )
    list_filter = ("kind", "status", "tenant")
    search_fields = ("id", "external_payment_id", "recipient_email", "buyer_email")
    readonly_fields = (
        "id",
        "tenant",
        "bot_user",
        "kind",
        "amount_rub",
        "recipient_name",
        "recipient_email",
        "buyer_email",
        "description",
        "external_payment_id",
        "checkout_url",
        "status",
        "created_at",
        "updated_at",
        "paid_at",
    )

    def has_add_permission(self, request: object) -> bool:  # noqa: ARG002
        return False

    def has_delete_permission(self, request: object, obj: object | None = None) -> bool:  # noqa: ARG002
        return False


@admin.register(PaymentEvent)
class PaymentEventAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "external_event_id",
        "event_type",
        "order",
        "processed",
        "received_at",
    )
    list_filter = ("event_type", "processed")
    search_fields = ("external_event_id",)
    readonly_fields = (
        "id",
        "external_event_id",
        "event_type",
        "order",
        "raw_payload",
        "received_at",
        "processed",
    )

    def has_add_permission(self, request: object) -> bool:  # noqa: ARG002
        return False

    def has_delete_permission(self, request: object, obj: object | None = None) -> bool:  # noqa: ARG002
        return False
