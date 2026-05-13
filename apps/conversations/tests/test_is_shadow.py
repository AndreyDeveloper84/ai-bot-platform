"""Conversation.is_shadow field tests (DRF-702 / Sprint 8 / N3)."""

from __future__ import annotations

import pytest

from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="n3-tenant", name="N3")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="n3-uid",
    )


class TestField:
    @pytest.mark.django_db
    def test_default_false(self, bot_user: BotUser) -> None:
        c = Conversation.all_tenants.create(tenant=bot_user.tenant, bot_user=bot_user)
        assert c.is_shadow is False

    @pytest.mark.django_db
    def test_can_be_true(self, bot_user: BotUser) -> None:
        c = Conversation.all_tenants.create(
            tenant=bot_user.tenant, bot_user=bot_user, is_shadow=True
        )
        assert c.is_shadow is True


class TestActiveUniquenessExcludesShadow:
    """Sprint 8 / N3 — the active-uniqueness partial constraint excludes
    shadow rows so the primary and a shadow conversation for the same
    bot_user can coexist.
    """

    @pytest.mark.django_db
    def test_primary_and_shadow_coexist(self, bot_user: BotUser) -> None:
        primary = Conversation.all_tenants.create(
            tenant=bot_user.tenant, bot_user=bot_user, is_active=True, is_shadow=False
        )
        shadow = Conversation.all_tenants.create(
            tenant=bot_user.tenant, bot_user=bot_user, is_active=True, is_shadow=True
        )
        assert primary.pk != shadow.pk
        # Both visible in the table.
        active_count = Conversation.all_tenants.filter(
            tenant=bot_user.tenant, bot_user=bot_user, is_active=True
        ).count()
        assert active_count == 2


class TestAdmin:
    def test_admin_columns(self) -> None:
        from apps.conversations.admin import ConversationAdmin

        assert "is_shadow" in ConversationAdmin.list_display
        assert "is_shadow" in ConversationAdmin.list_filter
