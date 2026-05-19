"""TenantStaff model contract tests (PR 1.5 / ADR-0008).

Pins the contract that the role resolver + /api/v1/me view depend on:

1. Three roles can coexist on the same tenant for the same person.
2. Owner is unique per tenant (DB-level partial unique constraint).
3. Soft-deactivation releases the Owner slot so handover works.
4. Cross-tenant: a TenantStaff row in tenant A never leaks to B; same
   BotUser can hold staff rows in two tenants (via two BotUser rows,
   one per tenant — BotUser uniqueness is ``(tenant, channel,
   channel_user_id)`` per ADR-0001).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from django.db import IntegrityError, transaction

from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant_a() -> Tenant:
    return Tenant.objects.create(slug="staff-a", name="Salon A")


@pytest.fixture
def tenant_b() -> Tenant:
    return Tenant.objects.create(slug="staff-b", name="Salon B")


@pytest.fixture
def bot_user_a(tenant_a: Tenant, settings) -> BotUser:
    settings.STRICT_TENANT_SCOPE = "strict"
    with tenant_scope(tenant_a):
        return BotUser.objects.create(
            tenant=tenant_a,
            channel="max",
            channel_user_id="staff-1",
            display_name="Анна",
        )


@pytest.fixture
def bot_user_a2(tenant_a: Tenant, settings) -> BotUser:
    settings.STRICT_TENANT_SCOPE = "strict"
    with tenant_scope(tenant_a):
        return BotUser.objects.create(
            tenant=tenant_a,
            channel="max",
            channel_user_id="staff-2",
            display_name="Карина",
        )


@pytest.fixture
def bot_user_b(tenant_b: Tenant, settings) -> BotUser:
    settings.STRICT_TENANT_SCOPE = "strict"
    with tenant_scope(tenant_b):
        return BotUser.objects.create(
            tenant=tenant_b,
            channel="max",
            channel_user_id="staff-1",  # same channel id, different tenant
            display_name="Анна (другой салон)",
        )


class TestTenantStaffBasics:
    def test_create_three_roles_on_same_tenant(self, tenant_a, bot_user_a, bot_user_a2, settings):
        """Tenant can have Owner + Admin + Receptionist simultaneously."""

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            owner = TenantStaff.objects.create(
                tenant=tenant_a, bot_user=bot_user_a, role=TenantStaff.Role.OWNER
            )
            admin = TenantStaff.objects.create(
                tenant=tenant_a, bot_user=bot_user_a2, role=TenantStaff.Role.ADMIN
            )
            recep = TenantStaff.all_tenants.create(
                tenant=tenant_a, bot_user=bot_user_a2, role=TenantStaff.Role.RECEPTIONIST
            )

        assert owner.role == "owner"
        assert admin.role == "admin"
        assert recep.role == "receptionist"
        # Same person holding two roles is allowed (admin + receptionist).
        assert TenantStaff.all_tenants.filter(tenant=tenant_a, bot_user=bot_user_a2).count() == 2

    def test_same_role_twice_for_same_user_rejected(self, tenant_a, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            TenantStaff.objects.create(
                tenant=tenant_a, bot_user=bot_user_a, role=TenantStaff.Role.ADMIN
            )
        with (
            tenant_scope(tenant_a),
            transaction.atomic(),
            pytest.raises(IntegrityError),
        ):
            TenantStaff.objects.create(
                tenant=tenant_a, bot_user=bot_user_a, role=TenantStaff.Role.ADMIN
            )

    def test_str_and_is_active(self, tenant_a, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            row = TenantStaff.objects.create(
                tenant=tenant_a, bot_user=bot_user_a, role=TenantStaff.Role.ADMIN
            )
        assert row.is_active is True
        assert "admin" in str(row)
        assert str(tenant_a.id) in str(row)


class TestOwnerUniqueness:
    def test_owner_unique_per_tenant_db_constraint(
        self, tenant_a, bot_user_a, bot_user_a2, settings
    ):
        """Two active Owners on the same tenant raises IntegrityError."""

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            TenantStaff.objects.create(
                tenant=tenant_a, bot_user=bot_user_a, role=TenantStaff.Role.OWNER
            )
        with (
            tenant_scope(tenant_a),
            transaction.atomic(),
            pytest.raises(IntegrityError),
        ):
            TenantStaff.objects.create(
                tenant=tenant_a, bot_user=bot_user_a2, role=TenantStaff.Role.OWNER
            )

    def test_deactivated_owner_does_not_block_new_owner(
        self, tenant_a, bot_user_a, bot_user_a2, settings
    ):
        """Owner handover: deactivate the old row, then create the new one."""

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            old = TenantStaff.objects.create(
                tenant=tenant_a, bot_user=bot_user_a, role=TenantStaff.Role.OWNER
            )
            old.deactivated_at = datetime(2026, 5, 19, tzinfo=timezone.utc)
            old.save(update_fields=["deactivated_at"])

            # New owner can be created — partial unique only counts
            # active rows.
            new_owner = TenantStaff.objects.create(
                tenant=tenant_a, bot_user=bot_user_a2, role=TenantStaff.Role.OWNER
            )
        assert new_owner.is_active is True
        assert (
            TenantStaff.all_tenants.filter(
                tenant=tenant_a, role="owner", deactivated_at__isnull=True
            ).count()
            == 1
        )
        assert TenantStaff.all_tenants.filter(tenant=tenant_a, role="owner").count() == 2

    def test_admin_uniqueness_does_not_apply(self, tenant_a, bot_user_a, bot_user_a2, settings):
        """Two active Admins on the same tenant is allowed by design."""

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            TenantStaff.objects.create(
                tenant=tenant_a, bot_user=bot_user_a, role=TenantStaff.Role.ADMIN
            )
            TenantStaff.objects.create(
                tenant=tenant_a, bot_user=bot_user_a2, role=TenantStaff.Role.ADMIN
            )
        assert TenantStaff.all_tenants.filter(tenant=tenant_a, role="admin").count() == 2


class TestSoftDeactivation:
    def test_deactivated_at_set_filters_default_queries(self, tenant_a, bot_user_a, settings):
        """Deactivated rows persist for audit but the active-filter excludes them."""

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            row = TenantStaff.objects.create(
                tenant=tenant_a, bot_user=bot_user_a, role=TenantStaff.Role.ADMIN
            )
            row.deactivated_at = datetime(2026, 5, 19, tzinfo=timezone.utc)
            row.save(update_fields=["deactivated_at"])

            active = TenantStaff.objects.filter(deactivated_at__isnull=True).count()
            archived = TenantStaff.objects.filter(deactivated_at__isnull=False).count()

        assert active == 0
        assert archived == 1

    def test_reactivation_by_clearing_deactivated_at(self, tenant_a, bot_user_a, settings):
        """Clearing deactivated_at restores the row to active state."""

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            row = TenantStaff.objects.create(
                tenant=tenant_a, bot_user=bot_user_a, role=TenantStaff.Role.RECEPTIONIST
            )
            row.deactivated_at = datetime(2026, 5, 19, tzinfo=timezone.utc)
            row.save(update_fields=["deactivated_at"])

            row.deactivated_at = None
            row.save(update_fields=["deactivated_at"])

        assert row.is_active is True


class TestCrossTenantIsolation:
    def test_staff_row_in_a_invisible_in_b(self, tenant_a, tenant_b, bot_user_a, settings):
        """A TenantStaff row scoped to tenant A is not seen under tenant B context."""

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            TenantStaff.objects.create(
                tenant=tenant_a, bot_user=bot_user_a, role=TenantStaff.Role.OWNER
            )

        with tenant_scope(tenant_b):
            # Default manager scopes to current tenant; under tenant B
            # the only Owner row above is invisible.
            assert TenantStaff.objects.filter(role="owner").count() == 0

    def test_same_person_two_tenants_two_rows(
        self, tenant_a, tenant_b, bot_user_a, bot_user_b, settings
    ):
        """One person can hold staff roles at two salons via two BotUsers."""

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            TenantStaff.objects.create(
                tenant=tenant_a, bot_user=bot_user_a, role=TenantStaff.Role.ADMIN
            )
        with tenant_scope(tenant_b):
            TenantStaff.objects.create(
                tenant=tenant_b, bot_user=bot_user_b, role=TenantStaff.Role.OWNER
            )

        # Two rows across the system; uniqueness scopes per tenant.
        assert TenantStaff.all_tenants.count() == 2

    def test_owner_uniqueness_is_per_tenant_not_global(
        self, tenant_a, tenant_b, bot_user_a, bot_user_b, settings
    ):
        """Tenant A's Owner and tenant B's Owner can coexist."""

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            TenantStaff.objects.create(
                tenant=tenant_a, bot_user=bot_user_a, role=TenantStaff.Role.OWNER
            )
        # No IntegrityError — uniqueness is per-tenant.
        with tenant_scope(tenant_b):
            TenantStaff.objects.create(
                tenant=tenant_b, bot_user=bot_user_b, role=TenantStaff.Role.OWNER
            )
        assert TenantStaff.all_tenants.filter(role="owner").count() == 2
