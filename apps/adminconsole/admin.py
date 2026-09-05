"""Экран журнала действий админки (DRF-1495).

``django.contrib.admin.models.LogEntry`` Django пишет сам, но по
умолчанию не регистрирует — и в этом репозитории его никто не
регистрировал, так что след правок был, а посмотреть его было негде.

Экран строго на чтение. Журнал, который можно править, — не журнал;
``apps.audit.admin.AuditLogAdmin`` держит ту же линию с DRF-426.
Хронология в ``AuditLog`` шире (туда пишут ещё и сервисы), а этот
экран — быстрый ответ на «что делали руками через админку».
"""

from __future__ import annotations

from django.contrib import admin
from django.contrib.admin.models import LogEntry
from django.http import HttpRequest


@admin.register(LogEntry)
class AdminActionLogAdmin(admin.ModelAdmin):
    list_display = (
        "action_time",
        "user",
        "action_label",
        "content_type",
        "object_repr",
        "change_message_short",
    )
    list_filter = ("action_flag", "content_type", "user")
    search_fields = ("object_repr", "change_message", "user__username")
    date_hierarchy = "action_time"
    ordering = ("-action_time",)

    @admin.display(description="Действие", ordering="action_flag")
    def action_label(self, obj: LogEntry) -> str:
        if obj.is_addition():
            return "создано"
        if obj.is_change():
            return "изменено"
        if obj.is_deletion():
            return "удалено"
        return "—"

    @admin.display(description="Что менялось")
    def change_message_short(self, obj: LogEntry) -> str:
        """Перечень изменённых полей. Значений здесь нет — только имена."""
        message = obj.get_change_message()
        return message if len(message) <= 160 else message[:157] + "…"

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False
