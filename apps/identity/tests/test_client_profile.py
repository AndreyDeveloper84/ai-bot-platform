"""ClientProfile model + signal tests (DRF-527 / Sprint 6 / P1)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.identity.models import BotUser, ClientProfile
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant():
    return Tenant.objects.create(slug="p1-test", name="P1 test")


@pytest.fixture
def bot_user(tenant):
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="42",
        display_name="Test user",
    )


class TestModel:
    def test_profile_auto_created_on_bot_user_save(self, bot_user):
        """P1 signal must populate ClientProfile on BotUser create."""
        assert hasattr(bot_user, "client_profile")
        profile = bot_user.client_profile
        assert profile is not None
        assert profile.tenant == bot_user.tenant

    def test_profile_defaults_to_zero(self, bot_user):
        profile = bot_user.client_profile
        assert profile.recency_days is None
        assert profile.frequency_visits == 0
        assert profile.monetary_total == Decimal("0")
        assert profile.rfm_segment == ""
        assert profile.ltv == Decimal("0")
        assert profile.predicted_ltv_12m == Decimal("0")
        assert profile.churn_risk == 0.0
        assert profile.lifecycle_stage == ""
        assert profile.avg_visit_interval_days is None
        assert profile.loyalty_tier == "bronze"
        assert profile.last_recomputed_at is None

    def test_signal_idempotent_on_resave(self, bot_user):
        """Re-saving BotUser doesn't create a second profile."""
        bot_user.client_name = "Updated"
        bot_user.save()
        assert ClientProfile.all_tenants.filter(bot_user=bot_user).count() == 1

    def test_str_representation(self, bot_user):
        profile = bot_user.client_profile
        s = str(profile)
        assert "ClientProfile" in s
        assert "bronze" in s

    def test_cascade_delete_on_bot_user(self, bot_user):
        bu_id = bot_user.id
        bot_user.delete()
        assert ClientProfile.all_tenants.filter(bot_user_id=bu_id).count() == 0


class TestTenantProtect:
    def test_tenant_drop_blocked_when_profiles_exist(self, tenant, bot_user):
        """tenant FK PROTECT — accidental drop must not vapourise derived state."""
        from django.db.models import ProtectedError

        # bot_user creates profile via signal. Tenant.delete should fail.
        with pytest.raises(ProtectedError):
            tenant.delete()

    def test_tenant_drop_works_after_explicit_profile_delete(self, tenant, bot_user):
        # Operator deletes profile explicitly first, then tenant succeeds.
        ClientProfile.all_tenants.filter(tenant=tenant).delete()
        bot_user.delete()
        tenant.delete()
        assert Tenant.objects.filter(slug="p1-test").count() == 0


class TestTenantScoping:
    def test_default_manager_filters_to_current_tenant(self, tenant, bot_user):
        from apps.tenancy.context import tenant_scope

        with tenant_scope(tenant):
            assert ClientProfile.objects.count() == 1
        # Outside tenant_scope — strict scope returns empty by default.
        assert ClientProfile.all_tenants.count() == 1

    def test_other_tenant_invisible_in_scope(self, tenant, bot_user):
        from apps.tenancy.context import tenant_scope

        other = Tenant.objects.create(slug="p1-other", name="Other")
        with tenant_scope(other):
            assert ClientProfile.objects.filter(bot_user=bot_user).count() == 0


class TestAdmin:
    def test_admin_class_is_read_only(self):
        from apps.identity.admin import ClientProfileAdmin

        admin = ClientProfileAdmin(ClientProfile, None)
        assert admin.has_add_permission(None) is False
        assert admin.has_change_permission(None) is False
        assert admin.has_delete_permission(None) is False

    def test_admin_list_display_includes_segment_and_tier(self):
        from apps.identity.admin import ClientProfileAdmin

        admin = ClientProfileAdmin(ClientProfile, None)
        assert "rfm_segment" in admin.list_display
        assert "loyalty_tier" in admin.list_display
        assert "lifecycle_stage" in admin.list_display


class TestBookingCompletedSignal:
    """P8 wires a real handler; P1 ships the signal definition + smoke-test."""

    def test_signal_can_be_fired(self, bot_user):
        from apps.identity.signals import booking_completed

        # No-op handlers; we're just verifying the signal exists + accepts kwargs.
        received: list[dict] = []

        def handler(sender, **kwargs):
            received.append(kwargs)

        booking_completed.connect(handler)
        try:
            booking_completed.send(sender=None, bot_user=bot_user, amount=Decimal("1000"))
            assert len(received) == 1
            assert received[0]["bot_user"] == bot_user
        finally:
            booking_completed.disconnect(handler)
