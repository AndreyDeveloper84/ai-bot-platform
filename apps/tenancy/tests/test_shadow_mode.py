"""Tenant.shadow_mode field tests (DRF-716 / Sprint 8 / S1)."""

from __future__ import annotations

import pytest

from apps.tenancy.models import Tenant


class TestShadowModeDefault:
    @pytest.mark.django_db
    def test_default_false(self) -> None:
        """Migration must default `shadow_mode=False` for all existing
        tenants — otherwise the formula-tela tenant would stop sending
        outbound the moment the migration ran in prod.
        """
        t = Tenant.objects.create(slug="s1-default", name="Default")
        assert t.shadow_mode is False

    @pytest.mark.django_db
    def test_can_be_flipped(self) -> None:
        t = Tenant.objects.create(slug="s1-flip", name="Flip")
        t.shadow_mode = True
        t.save(update_fields=["shadow_mode"])
        t.refresh_from_db()
        assert t.shadow_mode is True

    @pytest.mark.django_db
    def test_filter_by_shadow_mode(self) -> None:
        """Catalog cohort flips first — the daily delta task iterates
        ``Tenant.objects.filter(shadow_mode=True)``. Smoke that the
        query works.
        """
        Tenant.objects.create(slug="s1-on", name="Shadow on", shadow_mode=True)
        Tenant.objects.create(slug="s1-off", name="Shadow off", shadow_mode=False)
        shadow_slugs = list(Tenant.objects.filter(shadow_mode=True).values_list("slug", flat=True))
        assert shadow_slugs == ["s1-on"]


class TestAdminColumn:
    @pytest.mark.django_db
    def test_admin_lists_shadow_mode(self) -> None:
        """``list_display`` must include ``shadow_mode`` so ops can see
        which tenants are in shadow at a glance.
        """
        from apps.tenancy.admin import TenantAdmin

        assert "shadow_mode" in TenantAdmin.list_display
        assert "shadow_mode" in TenantAdmin.list_filter
        assert "shadow_mode" in TenantAdmin.list_editable
