"""Schedule admin — salon-staff writeable.

Full CRUD for WorkingHours / ScheduleException / TimeBlock / SlotConfig;
read-only for ScheduleChangeRequest (resolution is owner-driven via bot
DM per Q-M6, not via admin form).
"""

from __future__ import annotations

from django.contrib import admin

from apps.scheduling.models import (
    ScheduleChangeRequest,
    ScheduleException,
    SlotConfig,
    TimeBlock,
    WorkingHours,
)


@admin.register(WorkingHours)
class WorkingHoursAdmin(admin.ModelAdmin):
    list_display = (
        "master",
        "day_of_week",
        "is_working",
        "start_time",
        "end_time",
        "lunch_start",
        "lunch_end",
        "tenant",
        "updated_at",
    )
    list_filter = ("tenant", "day_of_week", "is_working")
    search_fields = ("master__name",)
    ordering = ("master__name", "day_of_week")
    raw_id_fields = ("master", "created_by")

    def get_queryset(self, request):
        return self.model.all_tenants.all().select_related("master", "tenant")


@admin.register(ScheduleException)
class ScheduleExceptionAdmin(admin.ModelAdmin):
    list_display = (
        "master",
        "date",
        "type",
        "start_time",
        "end_time",
        "tenant",
        "updated_at",
    )
    list_filter = ("tenant", "type", "date")
    search_fields = ("master__name", "reason")
    date_hierarchy = "date"
    ordering = ("-date", "master__name")
    raw_id_fields = ("master", "created_by")

    def get_queryset(self, request):
        return self.model.all_tenants.all().select_related("master", "tenant")


@admin.register(TimeBlock)
class TimeBlockAdmin(admin.ModelAdmin):
    list_display = ("master", "start_at", "end_at", "reason", "tenant", "updated_at")
    list_filter = ("tenant",)
    search_fields = ("master__name", "reason")
    ordering = ("-start_at",)
    raw_id_fields = ("master", "created_by")

    def get_queryset(self, request):
        return self.model.all_tenants.all().select_related("master", "tenant")


@admin.register(ScheduleChangeRequest)
class ScheduleChangeRequestAdmin(admin.ModelAdmin):
    list_display = (
        "master",
        "status",
        "created_at",
        "resolved_at",
        "resolved_by",
        "tenant",
    )
    list_filter = ("tenant", "status")
    search_fields = ("master__name", "reason")
    ordering = ("-created_at",)
    raw_id_fields = ("master", "resolved_by")
    readonly_fields = ("requested_change", "created_at")

    def get_queryset(self, request):
        return self.model.all_tenants.all().select_related("master", "tenant", "resolved_by")


@admin.register(SlotConfig)
class SlotConfigAdmin(admin.ModelAdmin):
    list_display = (
        "tenant",
        "slot_granularity_min",
        "buffer_min",
        "lead_time_min",
        "max_advance_days",
        "updated_at",
    )
    list_filter = ("tenant",)

    def get_queryset(self, request):
        return self.model.all_tenants.all().select_related("tenant")
