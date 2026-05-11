"""Experiments admin (DRF-484 / Sprint 4 / B1)."""

from __future__ import annotations

from django.contrib import admin

from apps.experiments.models import Experiment, Holdout, UserAssignment


@admin.register(Experiment)
class ExperimentAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "primary_kpi", "started_at", "ended_at", "tenant")
    list_filter = ("status", "tenant")
    search_fields = ("name", "hypothesis", "primary_kpi")
    ordering = ("-started_at", "name")

    def get_queryset(self, request):  # type: ignore[override]
        return Experiment.all_tenants.all()


@admin.register(UserAssignment)
class UserAssignmentAdmin(admin.ModelAdmin):
    list_display = ("bot_user", "experiment", "variant", "assigned_at", "tenant")
    list_filter = ("variant", "experiment", "tenant")
    search_fields = ("variant",)
    readonly_fields = ("id", "bot_user", "experiment", "variant", "assigned_at", "tenant")
    ordering = ("-assigned_at",)

    def get_queryset(self, request):  # type: ignore[override]
        return UserAssignment.all_tenants.all()


@admin.register(Holdout)
class HoldoutAdmin(admin.ModelAdmin):
    list_display = ("bot_user", "since", "tenant")
    list_filter = ("tenant",)
    readonly_fields = ("id", "bot_user", "since", "tenant")
    ordering = ("-since",)

    def get_queryset(self, request):  # type: ignore[override]
        return Holdout.all_tenants.all()
