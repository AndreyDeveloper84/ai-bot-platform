"""Conversation + Message admin (DRF-437 / Sprint 2 / B4).

Forensic-mostly admin: read-only on Message entirely (a message is
historical truth; editing it would break audit replay). Conversation
allows editing `outcome` only — for the rare case where the cleanup
sweep didn't fire and support needs to manually mark closure.

Both admins use ``all_tenants`` queryset because support staff are
cross-tenant by definition. The tenant column is in `list_display` so
the operator always knows which tenant they're looking at.
"""

from __future__ import annotations

from django.contrib import admin, messages

from apps.conversations.models import Conversation, Message

# DRF-1023 — both admins below are CROSS-TENANT (get_queryset uses
# ``all_tenants``): anyone logged in sees every salon's conversations,
# and MessageAdmin even searches message TEXT. The banner must be seen by
# the next person BEFORE they hand an account to salon staff.
_CROSS_TENANT_WARNING = (
    "ВНИМАНИЕ: админка кросс-тенантная — здесь видны переписки клиентов "
    "ВСЕХ салонов. Учётную запись выдаём только внутренней команде; "
    "сотруднику салона она откроет чужие данные."
)


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "bot_user",
        "state",
        "outcome",
        "is_active",
        "is_shadow",
        "last_message_at",
    )
    list_filter = ("state", "outcome", "is_active", "is_shadow", "tenant")
    search_fields = (
        "id",
        "bot_user__channel_user_id",
        "bot_user__phone",
        "bot_user__display_name",
    )
    readonly_fields = (
        "id",
        "tenant",
        "bot_user",
        "state",
        "is_active",
        "deleted_at",
        "last_message_at",
        "created_at",
    )
    fieldsets = (
        (None, {"fields": ("id", "tenant", "bot_user", "state")}),
        (
            "Closure (editable for manual cleanup)",
            {"fields": ("outcome",)},
        ),
        (
            "Lifecycle",
            {
                "fields": ("is_active", "deleted_at", "last_message_at", "created_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def has_add_permission(self, request) -> bool:
        # Conversations are created by webhook handlers — never via admin.
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        # Hard-delete forbidden; use mark_deleted() at the model layer.
        return False

    def get_queryset(self, request):
        return Conversation.all_tenants.all()

    def changelist_view(self, request, extra_context=None):  # type: ignore[override]
        messages.warning(request, _CROSS_TENANT_WARNING)  # DRF-1023
        return super().changelist_view(request, extra_context)


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "tenant", "role", "content_preview", "created_at")
    list_filter = ("role", "action_type", "tenant")
    search_fields = ("id", "content", "rendered_text", "tool_call_id")
    readonly_fields = (
        "id",
        "conversation",
        "tenant",
        "role",
        "content",
        "rendered_text",
        "action_type",
        "action_data",
        "tool_call",
        "tool_call_id",
        "trace_id",
        "tokens_in",
        "tokens_out",
        "latency_ms",
        "created_at",
    )

    @admin.display(description="Content (preview)")
    def content_preview(self, obj: Message) -> str:
        body = obj.rendered_text or obj.content
        return body[:60] + ("…" if len(body) > 60 else "")

    def has_add_permission(self, request) -> bool:
        # Messages are persisted by record_message() — never via admin.
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        # All fields are readonly; even toggling would be misleading.
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        # Historical truth — retention sweeps cascade-prune via cleanup.
        return False

    def get_queryset(self, request):
        return Message.all_tenants.all()

    def changelist_view(self, request, extra_context=None):  # type: ignore[override]
        messages.warning(request, _CROSS_TENANT_WARNING)  # DRF-1023
        return super().changelist_view(request, extra_context)
