"""ConsentRecord admin (DRF-456 / Sprint 3 / A1).

Read-only forensic admin. Consent records are legal trail — operators
must NOT edit historical rows. Adding a row goes through
`apps.consent.services.grant()`; withdrawing through `withdraw()`.
The admin surface is for inspection only.
"""

from __future__ import annotations

from django.contrib import admin

from apps.consent.models import ConsentRecord


@admin.register(ConsentRecord)
class ConsentRecordAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "bot_user",
        "consent_type",
        "granted",
        "document_version",
        "captured_at",
        "withdrawn_at",
    )
    list_filter = ("consent_type", "granted", "tenant")
    search_fields = (
        "id",
        "bot_user__channel_user_id",
        "bot_user__phone",
        "source",
        "document_version",
    )
    readonly_fields = (
        "id",
        "tenant",
        "bot_user",
        "consent_type",
        "granted",
        "source",
        "document_version",
        "captured_at",
        "withdrawn_at",
    )

    def has_add_permission(self, request) -> bool:
        # Consent must go through apps.consent.services.grant() so the
        # event + audit row are emitted alongside.
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        # Historical rows are immutable. Withdrawal goes through
        # services.withdraw() which writes a new row, not edits old.
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        # 152-ФЗ audit trail — never hard-delete from admin. Retention
        # sweeps go through Celery beat (Sprint 4+).
        return False

    def get_queryset(self, request):
        return ConsentRecord.all_tenants.all()
