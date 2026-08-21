"""Revoking salon access (DRF-1227).

Access could be granted and never taken back: ``deactivated_at`` had no
writer outside tests and ``linked_bot_user`` was only ever set. These tests
pin the three properties that make revocation safe rather than merely
present.

* **The master stays bookable.** Revoking a person's login must not quietly
  pull a master out of the salon's booking surface — that is a different
  decision with a different owner.
* **History survives.** Rows are deactivated, never deleted; "who held what
  and until when" stays answerable.
* **The owner is refused.** One active owner per tenant, and only an owner
  can issue an owner code — deactivating that row would leave a salon
  nobody can re-enter.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.identity.services.role_resolver import resolve_role
from apps.identity.services.staff_revoke import (
    OwnerRevokeRefused,
    revoke_staff_access,
)
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="revoke-salon", name="Формула тела")


@pytest.fixture
def other_tenant() -> Tenant:
    return Tenant.objects.create(slug="revoke-other", name="Другой салон")


def _person(tenant: Tenant, channel_user_id: str = "77001") -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id=channel_user_id, chat_id=channel_user_id
    )


def _staff(tenant: Tenant, person: BotUser, role: str) -> TenantStaff:
    return TenantStaff.all_tenants.create(tenant=tenant, bot_user=person, role=role)


def _master(tenant: Tenant, *, linked: BotUser | None = None, name: str = "Ольга") -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        name=name,
        external_id=None,
        external_updated_at=timezone.now(),
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        is_active=True,
        linked_bot_user=linked,
    )


class TestStaffRoles:
    def test_an_admin_stops_being_an_admin(self, tenant):
        person = _person(tenant)
        _staff(tenant, person, TenantStaff.Role.ADMIN)

        result = revoke_staff_access(tenant=tenant, bot_user=person)

        assert result.roles_revoked == ("admin",)
        assert resolve_role(person).is_admin is False
        assert resolve_role(person).primary_role == "customer"

    def test_every_role_comes_off_at_once(self, tenant):
        # Revocation is about a person, not a role: leaving them the
        # receptionist half would be a role *change*, a different intent.
        person = _person(tenant)
        _staff(tenant, person, TenantStaff.Role.ADMIN)
        _staff(tenant, person, TenantStaff.Role.RECEPTIONIST)

        result = revoke_staff_access(tenant=tenant, bot_user=person)

        assert result.roles_revoked == ("admin", "receptionist")
        assert resolve_role(person).primary_role == "customer"

    def test_the_row_is_kept_as_history(self, tenant):
        person = _person(tenant)
        row = _staff(tenant, person, TenantStaff.Role.ADMIN)

        revoke_staff_access(tenant=tenant, bot_user=person)

        row.refresh_from_db()
        assert row.deactivated_at is not None
        assert row.is_active is False

    def test_access_can_be_granted_again_afterwards(self, tenant):
        """A revoke is not a ban — re-hiring must work.

        Worth pinning because the equivalent model in Ayla treats any
        inactive row as a permanent refusal; this one deliberately does not.
        """

        person = _person(tenant)
        _staff(tenant, person, TenantStaff.Role.ADMIN)
        revoke_staff_access(tenant=tenant, bot_user=person)

        _staff(tenant, person, TenantStaff.Role.RECEPTIONIST)

        assert resolve_role(person).is_receptionist is True


class TestMasterLink:
    def test_the_person_loses_the_master_surface(self, tenant):
        person = _person(tenant)
        master = _master(tenant, linked=person)

        result = revoke_staff_access(tenant=tenant, bot_user=person)

        master.refresh_from_db()
        assert result.master_unlinked is True
        assert master.linked_bot_user_id is None
        assert resolve_role(person).is_master is False

    def test_the_master_stays_bookable(self, tenant):
        """The property that keeps a revoke from emptying the schedule."""

        person = _person(tenant)
        master = _master(tenant, linked=person)

        revoke_staff_access(tenant=tenant, bot_user=person)

        master.refresh_from_db()
        assert master.invite_status == CatalogMaster.InviteStatus.ACCEPTED
        assert master.is_active is True
        assert CatalogMaster.all_tenants.filter(
            pk=master.pk,
            is_active=True,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        ).exists()

    def test_a_master_who_is_also_an_admin_loses_both(self, tenant):
        person = _person(tenant)
        _staff(tenant, person, TenantStaff.Role.ADMIN)
        master = _master(tenant, linked=person)

        result = revoke_staff_access(tenant=tenant, bot_user=person)

        master.refresh_from_db()
        assert result.roles_revoked == ("admin",)
        assert result.master_unlinked is True
        assert resolve_role(person).primary_role == "customer"


class TestOwner:
    def test_the_owner_is_refused(self, tenant):
        person = _person(tenant)
        _staff(tenant, person, TenantStaff.Role.OWNER)

        with pytest.raises(OwnerRevokeRefused):
            revoke_staff_access(tenant=tenant, bot_user=person)

    def test_the_refusal_changes_nothing(self, tenant):
        """Including the master link — the whole call rolls back."""

        person = _person(tenant)
        row = _staff(tenant, person, TenantStaff.Role.OWNER)
        master = _master(tenant, linked=person)

        with pytest.raises(OwnerRevokeRefused):
            revoke_staff_access(tenant=tenant, bot_user=person)

        row.refresh_from_db()
        master.refresh_from_db()
        assert row.deactivated_at is None
        assert master.linked_bot_user_id == person.id
        assert resolve_role(person).is_owner is True


class TestIdempotence:
    def test_revoking_twice_is_not_an_error(self, tenant):
        person = _person(tenant)
        _staff(tenant, person, TenantStaff.Role.ADMIN)

        first = revoke_staff_access(tenant=tenant, bot_user=person)
        second = revoke_staff_access(tenant=tenant, bot_user=person)

        assert first.changed is True
        assert second.changed is False

    def test_someone_who_never_had_access(self, tenant):
        person = _person(tenant)

        result = revoke_staff_access(tenant=tenant, bot_user=person)

        assert result.changed is False
        assert result.roles_revoked == ()


class TestTenantIsolation:
    def test_another_salons_rows_are_untouched(self, tenant, other_tenant):
        person = _person(tenant)
        here = _staff(tenant, person, TenantStaff.Role.ADMIN)
        # A stray row naming the same person under the other tenant. The
        # revoke names one salon and must stop at its boundary.
        elsewhere = TenantStaff.all_tenants.create(
            tenant=other_tenant, bot_user=person, role=TenantStaff.Role.ADMIN
        )

        revoke_staff_access(tenant=tenant, bot_user=person)

        here.refresh_from_db()
        elsewhere.refresh_from_db()
        assert here.deactivated_at is not None
        assert elsewhere.deactivated_at is None

    def test_another_salons_master_link_is_untouched(self, tenant, other_tenant):
        person = _person(tenant)
        theirs = _master(other_tenant, linked=person, name="Чужой мастер")

        revoke_staff_access(tenant=tenant, bot_user=person)

        theirs.refresh_from_db()
        assert theirs.linked_bot_user_id == person.id


class TestAudit:
    def test_a_revoke_is_recorded(self, tenant):
        person = _person(tenant)
        actor = _person(tenant, "77002")
        _staff(tenant, person, TenantStaff.Role.ADMIN)

        revoke_staff_access(tenant=tenant, bot_user=person, actor=actor, reason="уволилась")

        row = AuditLog.all_tenants.filter(action="staff.access_revoked").get()
        assert row.tenant_id == tenant.id
        assert row.payload["roles_revoked"] == ["admin"]
        assert row.payload["reason"] == "уволилась"
        assert row.payload["actor_id"] == str(actor.id)

    def test_nothing_revoked_writes_no_audit_row(self, tenant):
        # An audit feed that logs non-events is one people stop reading.
        person = _person(tenant)

        revoke_staff_access(tenant=tenant, bot_user=person)

        assert not AuditLog.all_tenants.filter(action="staff.access_revoked").exists()
