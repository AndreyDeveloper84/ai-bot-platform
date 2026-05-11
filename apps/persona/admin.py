"""BrandVoiceConfig admin (DRF-445 / Sprint 2 / E1).

Minimal admin. Sprint 4 will replace with richer UI when persona work
lands (preview pane, example browser, A/B testing slot).
"""

from __future__ import annotations

from django.contrib import admin

from apps.persona.models import BrandVoiceConfig


@admin.register(BrandVoiceConfig)
class BrandVoiceConfigAdmin(admin.ModelAdmin):
    list_display = ("tenant", "tone", "updated_at")
    search_fields = ("tenant__slug", "tone")
    readonly_fields = ("id", "created_at", "updated_at")
