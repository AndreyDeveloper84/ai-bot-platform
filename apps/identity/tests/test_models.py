"""BotUser model contract tests (DRF-434 / Sprint 2 / A2)."""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant_a() -> Tenant:
    return Tenant.objects.create(slug="bot-user-a", name="A")


@pytest.fixture
def tenant_b() -> Tenant:
    return Tenant.objects.create(slug="bot-user-b", name="B")


class TestBotUserBasics:
    def test_uuid_pk_and_timestamps_autoset(self, tenant_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            u = BotUser.objects.create(tenant=tenant_a, channel="max", channel_user_id="42")
        assert u.id is not None
        assert str(u.id).count("-") == 4
        assert u.first_seen is not None
        assert u.last_seen is not None

    def test_str_falls_back_through_name_chain(self, tenant_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            u = BotUser.objects.create(
                tenant=tenant_a,
                channel="max",
                channel_user_id="55",
                display_name="Иван",
            )
        assert "Иван" in str(u)
        assert "max" in str(u)


class TestBotUserUniqueness:
    def test_same_tenant_channel_channel_user_id_unique(self, tenant_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            BotUser.objects.create(tenant=tenant_a, channel="max", channel_user_id="1")
        with (
            tenant_scope(tenant_a),
            transaction.atomic(),
            pytest.raises(IntegrityError),
        ):
            BotUser.objects.create(tenant=tenant_a, channel="max", channel_user_id="1")

    def test_cross_tenant_same_channel_user_id_allowed(self, tenant_a, tenant_b, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        # Same channel_user_id under two tenants — two distinct BotUsers.
        # Use all_tenants to avoid the cross-tenant filter detection
        # when no tenant is in scope for the second insert.
        u1 = BotUser.all_tenants.create(tenant=tenant_a, channel="max", channel_user_id="shared")
        u2 = BotUser.all_tenants.create(tenant=tenant_b, channel="max", channel_user_id="shared")
        assert u1.id != u2.id

    def test_same_channel_user_id_different_channels_allowed(self, tenant_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            BotUser.objects.create(tenant=tenant_a, channel="max", channel_user_id="x")
            BotUser.objects.create(tenant=tenant_a, channel="telegram", channel_user_id="x")
        assert BotUser.all_tenants.filter(channel_user_id="x").count() == 2


class TestBotUserManager:
    def test_default_manager_filters_to_current_tenant(self, tenant_a, tenant_b, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        u1 = BotUser.all_tenants.create(tenant=tenant_a, channel="max", channel_user_id="A1")
        BotUser.all_tenants.create(tenant=tenant_b, channel="max", channel_user_id="B1")

        with tenant_scope(tenant_a):
            ids = list(BotUser.objects.values_list("id", flat=True))
        assert ids == [u1.id]

    def test_all_tenants_escape_hatch_returns_both(self, tenant_a, tenant_b, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        BotUser.all_tenants.create(tenant=tenant_a, channel="max", channel_user_id="A2")
        BotUser.all_tenants.create(tenant=tenant_b, channel="max", channel_user_id="B2")
        assert BotUser.all_tenants.count() == 2
