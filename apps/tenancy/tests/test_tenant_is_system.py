"""Tests for the ``Tenant.is_system`` field and admin delete-guard (KB-RAG Sub-1).

Sub-1 ships a boolean flag that marks service tenants — like ``global_kb`` for
the shared knowledge-base corpus — and adds an admin-layer guard so a service
tenant cannot be deleted by accident. Real salons remain freely deletable.

Contracts pinned here:

    1. ``is_system`` defaults to False — no behaviour change for existing rows.
    2. Admin ``has_delete_permission`` returns False for system tenants.
    3. Admin ``delete_queryset`` refuses to run when any row in the selection
       is a system tenant (no partial deletes).
    4. Admin ``delete_model`` refuses for a single system tenant.
"""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import PermissionDenied

from apps.tenancy.admin import TenantAdmin
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


class _StubRequest:
    """Minimal request stand-in for ``ModelAdmin`` permission methods."""

    def __init__(self) -> None:
        self.user = _StubUser()


class _StubUser:
    is_active = True
    is_staff = True
    is_superuser = True

    def has_perm(self, perm: str, obj=None) -> bool:  # noqa: D401, ARG002
        return True


class TestIsSystemDefault:
    def test_default_is_false(self):
        t = Tenant.objects.create(slug="regular-salon", name="Regular Salon")
        assert t.is_system is False

    def test_explicit_true_persists(self):
        t = Tenant.objects.create(slug="svc-tenant", name="Service", is_system=True)
        t.refresh_from_db()
        assert t.is_system is True


class TestAdminDeleteGuard:
    def setup_method(self):
        self.site = AdminSite()
        self.admin = TenantAdmin(Tenant, self.site)
        self.request = _StubRequest()

    def test_has_delete_permission_false_for_system_tenant(self):
        system_tenant = Tenant.objects.create(slug="global-kb", name="Global KB", is_system=True)
        assert self.admin.has_delete_permission(self.request, system_tenant) is False

    def test_has_delete_permission_true_for_regular_tenant(self):
        regular = Tenant.objects.create(slug="salon-a", name="Salon A")
        assert self.admin.has_delete_permission(self.request, regular) is True

    def test_has_delete_permission_true_when_no_obj(self):
        # The changelist's "Delete selected" action calls has_delete_permission(request)
        # without an obj — that path still needs to render the dropdown. Per-row
        # guarding happens in delete_queryset/delete_model.
        assert self.admin.has_delete_permission(self.request) is True

    def test_delete_model_raises_for_system_tenant(self):
        system_tenant = Tenant.objects.create(slug="global-kb-2", name="Global KB", is_system=True)
        with pytest.raises(PermissionDenied):
            self.admin.delete_model(self.request, system_tenant)

        # And critically: row still exists.
        assert Tenant.all_objects.filter(slug="global-kb-2").exists()

    def test_delete_model_allows_regular_tenant(self):
        regular = Tenant.objects.create(slug="salon-b", name="Salon B")
        self.admin.delete_model(self.request, regular)
        assert not Tenant.all_objects.filter(slug="salon-b").exists()

    def test_delete_queryset_raises_when_any_row_is_system(self):
        Tenant.objects.create(slug="salon-c", name="C")
        Tenant.objects.create(slug="global-kb-3", name="Global KB", is_system=True)
        Tenant.objects.create(slug="salon-d", name="D")

        qs = Tenant.all_objects.filter(slug__in=["salon-c", "global-kb-3", "salon-d"])
        with pytest.raises(PermissionDenied):
            self.admin.delete_queryset(self.request, qs)

        # Refuse-all semantics: none of the three got deleted.
        remaining = set(Tenant.all_objects.values_list("slug", flat=True))
        assert {"salon-c", "global-kb-3", "salon-d"}.issubset(remaining)

    def test_delete_queryset_allows_all_regular(self):
        Tenant.objects.create(slug="salon-e", name="E")
        Tenant.objects.create(slug="salon-f", name="F")
        qs = Tenant.all_objects.filter(slug__in=["salon-e", "salon-f"])
        self.admin.delete_queryset(self.request, qs)
        assert not Tenant.all_objects.filter(slug__in=["salon-e", "salon-f"]).exists()
