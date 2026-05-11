"""Conversation.State HUMAN_HANDOFF tests (DRF-466 / Sprint 3 / C3)."""

from __future__ import annotations

import pytest

from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="hs-a", name="HA")


@pytest.fixture
def bot_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="hs-bot")


class TestHumanHandoffState:
    def test_state_choice_exists(self):
        assert Conversation.State.HUMAN_HANDOFF == "human_handoff"
        assert "human_handoff" in dict(Conversation.State.choices)

    def test_full_clean_accepts_human_handoff(self, tenant, bot_user, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            conv = Conversation.objects.create(
                tenant=tenant,
                bot_user=bot_user,
                state=Conversation.State.HUMAN_HANDOFF,
            )
            conv.full_clean()  # must NOT raise
            conv.refresh_from_db()
        assert conv.state == "human_handoff"

    def test_transition_idle_to_human_handoff(self, tenant, bot_user, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            conv = Conversation.objects.create(tenant=tenant, bot_user=bot_user)
            assert conv.state == Conversation.State.IDLE
            conv.state = Conversation.State.HUMAN_HANDOFF
            conv.save(update_fields=["state"])
            conv.refresh_from_db()
        assert conv.state == "human_handoff"

    def test_admin_filter_exposes_new_state(self):
        """Admin uses default choices-derived filter — must include HUMAN_HANDOFF."""

        from apps.conversations.admin import ConversationAdmin

        assert "state" in ConversationAdmin.list_filter
        # Choices on the field are what the filter offers.
        state_choices = dict(Conversation._meta.get_field("state").choices)
        assert "human_handoff" in state_choices
