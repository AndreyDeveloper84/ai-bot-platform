"""Tests for apps.identity.services.solo_onboarding (Веха 1).

Veha 1 scope: basic correctness — atomicity, idempotency, default naming,
bootstrap-tenant gate, partial-state detection.

Race-condition tests + exhaustive idempotency live in Veha 2.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.identity.services.solo_onboarding import (
    BOOTSTRAP_TENANT_SLUG,
    BootstrapTenantMissing,
    SoloOnboardingPartialStateError,
    SoloOnboardingResult,
    _default_tenant_name,
    _solo_external_id,
    _solo_tenant_slug,
    create_solo_provider,
)
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db


# ─── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def bootstrap_tenant():
    """The ops-pre-seeded bootstrap tenant. Required by every test."""
    return Tenant.objects.create(
        slug=BOOTSTRAP_TENANT_SLUG,
        name="Ayla Solo — Registration",
    )


@pytest.fixture
def channel_identity():
    """A unique synthetic identity per test to avoid slug collisions."""
    return {
        "channel": "max",
        "channel_user_id": f"max-{uuid.uuid4().hex[:8]}",
        "display_name": "Ольга Иванова",
        "phone": "+79991234567",
        "chat_id": "chat-123",
    }


# ─── TestHelpers — pure functions ───────────────────────────────────────


class TestHelpers:
    def test_default_tenant_name_first_token(self):
        assert _default_tenant_name("Ольга Иванова") == "Студия Ольга"

    def test_default_tenant_name_single_word(self):
        assert _default_tenant_name("Ольга") == "Студия Ольга"

    def test_default_tenant_name_empty_fallback(self):
        assert _default_tenant_name("") == "Студия мастера"
        assert _default_tenant_name("   ") == "Студия мастера"

    def test_default_tenant_name_strips_whitespace(self):
        assert _default_tenant_name("  Ольга  ") == "Студия Ольга"

    def test_solo_tenant_slug_deterministic(self):
        s1 = _solo_tenant_slug("max", "12345")
        s2 = _solo_tenant_slug("max", "12345")
        assert s1 == s2
        assert s1.startswith("solo-max-")
        assert len(s1) <= 50  # SlugField max_length

    def test_solo_tenant_slug_differs_per_identity(self):
        assert _solo_tenant_slug("max", "12345") != _solo_tenant_slug("max", "12346")
        assert _solo_tenant_slug("max", "12345") != _solo_tenant_slug("telegram", "12345")

    def test_solo_external_id_negative(self):
        v = _solo_external_id(uuid.uuid4())
        assert v < 0
        # Bound: within signed-int 32-bit range
        assert v >= -(2**31 - 1)

    def test_solo_external_id_deterministic(self):
        bot_user_id = uuid.uuid4()
        assert _solo_external_id(bot_user_id) == _solo_external_id(bot_user_id)


# ─── TestBootstrapGate — pre-flight check ───────────────────────────────


class TestBootstrapGate:
    """Pre-flight: BootstrapTenantMissing raises when ops setup not done."""

    def test_missing_bootstrap_raises(self, channel_identity):
        # No bootstrap_tenant fixture loaded — Tenant table empty.
        with pytest.raises(BootstrapTenantMissing, match=BOOTSTRAP_TENANT_SLUG):
            create_solo_provider(**channel_identity)


# ─── TestFreshOnboarding — happy path: all 5 rows created ───────────────


class TestFreshOnboarding:
    def test_creates_all_5_rows(self, bootstrap_tenant, channel_identity):
        result = create_solo_provider(**channel_identity)

        assert isinstance(result, SoloOnboardingResult)
        assert result.created is True

        # All 5 records exist with correct cross-references
        assert result.tenant.pk is not None
        assert result.tenant.slug.startswith("solo-max-")
        assert result.tenant.id != bootstrap_tenant.id

        assert result.bot_user.tenant_id == result.tenant.id
        assert result.bot_user.channel == "max"
        assert result.bot_user.channel_user_id == channel_identity["channel_user_id"]
        assert result.bot_user.display_name == "Ольга Иванова"
        assert result.bot_user.phone == "+79991234567"

        assert result.owner_staff.tenant_id == result.tenant.id
        assert result.owner_staff.bot_user_id == result.bot_user.id
        assert result.owner_staff.role == TenantStaff.Role.OWNER
        assert result.owner_staff.deactivated_at is None

        assert result.admin_staff.tenant_id == result.tenant.id
        assert result.admin_staff.bot_user_id == result.bot_user.id
        assert result.admin_staff.role == TenantStaff.Role.ADMIN
        assert result.admin_staff.deactivated_at is None

        assert result.master.tenant_id == result.tenant.id
        assert result.master.linked_bot_user_id == result.bot_user.id
        assert result.master.invite_status == CatalogMaster.InviteStatus.ACCEPTED
        assert result.master.invite_token is None  # self-onboarded, no invite
        assert result.master.external_id < 0  # synthetic negative space

    def test_default_tenant_name_used(self, bootstrap_tenant, channel_identity):
        result = create_solo_provider(**channel_identity)
        # display_name='Ольга Иванова' → first token 'Ольга' → «Студия Ольга»
        assert result.tenant.name == "Студия Ольга"
        # Master name mirrors tenant name at seed time
        assert result.master.name == "Студия Ольга"

    def test_explicit_tenant_name_overrides_default(self, bootstrap_tenant, channel_identity):
        result = create_solo_provider(**channel_identity, tenant_name="Salon Maxima")
        assert result.tenant.name == "Salon Maxima"
        assert result.master.name == "Salon Maxima"

    def test_empty_display_name_fallback(self, bootstrap_tenant):
        result = create_solo_provider(
            channel="max",
            channel_user_id=f"max-{uuid.uuid4().hex[:8]}",
            display_name="",
        )
        assert result.tenant.name == "Студия мастера"


# ─── TestIdempotency — second call returns existing ────────────────────


class TestIdempotency:
    def test_second_call_returns_existing(self, bootstrap_tenant, channel_identity):
        first = create_solo_provider(**channel_identity)
        second = create_solo_provider(**channel_identity)

        assert first.created is True
        assert second.created is False
        assert first.tenant.pk == second.tenant.pk
        assert first.bot_user.pk == second.bot_user.pk
        assert first.owner_staff.pk == second.owner_staff.pk
        assert first.admin_staff.pk == second.admin_staff.pk
        assert first.master.pk == second.master.pk

    def test_no_duplicate_rows_on_second_call(self, bootstrap_tenant, channel_identity):
        create_solo_provider(**channel_identity)
        create_solo_provider(**channel_identity)

        solo_tenant_slug = _solo_tenant_slug(
            channel_identity["channel"], channel_identity["channel_user_id"]
        )
        tenant = Tenant.objects.get(slug=solo_tenant_slug)

        assert BotUser.all_tenants.filter(tenant=tenant).count() == 1
        assert TenantStaff.all_tenants.filter(tenant=tenant).count() == 2  # owner + admin
        assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == 1

    def test_idempotent_tenant_name_ignored_on_second_call(
        self, bootstrap_tenant, channel_identity
    ):
        """Second call with different tenant_name returns the FIRST name —
        we don't rename on idempotent return (caller intent ambiguous)."""
        first = create_solo_provider(**channel_identity, tenant_name="Original Name")
        second = create_solo_provider(**channel_identity, tenant_name="New Name")

        assert first.tenant.name == "Original Name"
        assert second.tenant.name == "Original Name"  # not renamed


# ─── TestAtomicity — failure mid-seed → full rollback ──────────────────


class TestAtomicity:
    def test_force_master_failure_rolls_back_staff_tenant_bot_user(
        self, bootstrap_tenant, channel_identity
    ):
        """Simulate CatalogMaster.create failure → all 4 earlier creates
        roll back. No orphan tenant / bot_user / staff rows."""
        with patch.object(
            CatalogMaster.all_tenants,
            "create",
            side_effect=IntegrityError("simulated master create failure"),
        ):
            with pytest.raises(IntegrityError):
                create_solo_provider(**channel_identity)

        # Verify NO partial state survived rollback
        solo_slug = _solo_tenant_slug(
            channel_identity["channel"], channel_identity["channel_user_id"]
        )
        assert not Tenant.objects.filter(slug=solo_slug).exists()
        assert not BotUser.all_tenants.filter(
            channel="max", channel_user_id=channel_identity["channel_user_id"]
        ).exists()
        assert TenantStaff.all_tenants.count() == 0
        assert CatalogMaster.all_tenants.count() == 0

    def test_force_admin_staff_failure_rolls_back_earlier_rows(
        self, bootstrap_tenant, channel_identity
    ):
        """Force second TenantStaff.create (admin) to fail → tenant + bot_user
        + owner_staff also roll back."""
        call_count = {"n": 0}
        real_create = TenantStaff.all_tenants.create

        def flaky_create(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:  # second call = admin
                raise IntegrityError("simulated admin staff failure")
            return real_create(*args, **kwargs)

        with patch.object(TenantStaff.all_tenants, "create", side_effect=flaky_create):
            with pytest.raises(IntegrityError):
                create_solo_provider(**channel_identity)

        solo_slug = _solo_tenant_slug(
            channel_identity["channel"], channel_identity["channel_user_id"]
        )
        assert not Tenant.objects.filter(slug=solo_slug).exists()
        assert TenantStaff.all_tenants.count() == 0


# ─── TestPartialState — pre-existing inconsistent rows → raise ─────────


class TestPartialState:
    def test_tenant_without_bot_user_raises(self, bootstrap_tenant, channel_identity):
        """Solo tenant exists but no matching BotUser → partial state."""
        slug = _solo_tenant_slug(channel_identity["channel"], channel_identity["channel_user_id"])
        Tenant.objects.create(slug=slug, name="Manual Tenant")
        # No BotUser, no staff, no master

        with pytest.raises(SoloOnboardingPartialStateError, match="no BotUser found"):
            create_solo_provider(**channel_identity)

    def test_tenant_bot_user_but_missing_master_raises(self, bootstrap_tenant, channel_identity):
        """Tenant + BotUser + 2 staff exist, but CatalogMaster missing."""
        slug = _solo_tenant_slug(channel_identity["channel"], channel_identity["channel_user_id"])
        tenant = Tenant.objects.create(slug=slug, name="Partial Salon")
        bu = BotUser.all_tenants.create(
            tenant=tenant,
            channel=channel_identity["channel"],
            channel_user_id=channel_identity["channel_user_id"],
        )
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=bu, role=TenantStaff.Role.OWNER)
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=bu, role=TenantStaff.Role.ADMIN)
        # CatalogMaster missing

        with pytest.raises(SoloOnboardingPartialStateError, match="master=False"):
            create_solo_provider(**channel_identity)

    def test_tenant_bot_user_but_missing_admin_raises(self, bootstrap_tenant, channel_identity):
        """Owner + master exist, admin missing → partial state."""
        slug = _solo_tenant_slug(channel_identity["channel"], channel_identity["channel_user_id"])
        tenant = Tenant.objects.create(slug=slug, name="Partial Salon")
        bu = BotUser.all_tenants.create(
            tenant=tenant,
            channel=channel_identity["channel"],
            channel_user_id=channel_identity["channel_user_id"],
        )
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=bu, role=TenantStaff.Role.OWNER)
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=_solo_external_id(bu.id),
            external_updated_at=timezone.now(),
            name="X",
            linked_bot_user=bu,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        )
        # admin missing

        with pytest.raises(SoloOnboardingPartialStateError, match="admin=False"):
            create_solo_provider(**channel_identity)
