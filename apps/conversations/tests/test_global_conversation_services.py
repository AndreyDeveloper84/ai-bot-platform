"""Sentinel-scoped global conversation persistence (#1026 / EPIC #1014).

The siblings of resolve_active_conversation / record_message for the tenant-less
discovery bot persist under the `global_bot` sentinel at current_tenant()=None
without entering a tenant_scope. The per-tenant functions stay untouched.
"""

from __future__ import annotations

import pytest

from apps.conversations.models import Conversation
from apps.conversations.services import (
    record_global_message,
    record_message,
    resolve_active_conversation,
    resolve_active_global_conversation,
)
from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.identity.models import BotUser
from apps.identity.services import resolve_or_create_global_bot_user
from apps.tenancy.context import current_tenant
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def test_resolve_and_record_under_sentinel_at_no_tenant(settings) -> None:
    settings.STRICT_TENANT_SCOPE = "strict"
    assert current_tenant() is None

    bot_user = resolve_or_create_global_bot_user(channel="max", channel_user_id="g-100")
    conv = resolve_active_global_conversation(bot_user)
    assert conv is not None
    assert conv.tenant.slug == GLOBAL_BOT_TENANT_SLUG

    msg = record_global_message(conv, role="user", content="привет")
    assert msg.conversation_id == conv.id
    assert msg.tenant_id == conv.tenant_id  # message tenant == sentinel

    # Re-resolve returns the same active conversation (cross-turn).
    again = resolve_active_global_conversation(bot_user)
    assert again is not None
    assert again.id == conv.id
    # Never leaked a tenant scope.
    assert current_tenant() is None


def test_idempotent_single_active_conversation(settings) -> None:
    settings.STRICT_TENANT_SCOPE = "strict"
    bot_user = resolve_or_create_global_bot_user(channel="max", channel_user_id="g-101")
    a = resolve_active_global_conversation(bot_user)
    b = resolve_active_global_conversation(bot_user)
    assert a is not None and b is not None
    assert a.id == b.id
    assert Conversation.all_tenants.filter(bot_user=bot_user, is_active=True).count() == 1


def test_defence_in_depth_rejects_non_sentinel(settings) -> None:
    """A non-global BotUser / Conversation must be rejected by the siblings."""
    settings.STRICT_TENANT_SCOPE = "strict"
    other = Tenant.objects.create(slug="not-sentinel", name="Other")
    foreign_bu = BotUser.all_tenants.create(tenant=other, channel="max", channel_user_id="g-102")

    with pytest.raises(ValueError, match="sentinel"):
        resolve_active_global_conversation(foreign_bu)

    # Build a global conversation, then try to record under a foreign-tenant one.
    global_bu = resolve_or_create_global_bot_user(channel="max", channel_user_id="g-103")
    global_conv = resolve_active_global_conversation(global_bu)
    assert global_conv is not None
    foreign_conv = Conversation.all_tenants.create(tenant=other, bot_user=foreign_bu)
    with pytest.raises(ValueError, match="sentinel"):
        record_global_message(foreign_conv, role="user", content="x")
    # Sanity: the global path itself works.
    record_global_message(global_conv, role="user", content="ok")


def test_per_tenant_functions_left_untouched(settings) -> None:
    """The per-tenant resolver still fails loud at current_tenant()=None."""
    settings.STRICT_TENANT_SCOPE = "strict"
    global_bu = resolve_or_create_global_bot_user(channel="max", channel_user_id="g-104")
    with pytest.raises(ValueError, match="tenant in scope"):
        resolve_active_conversation(global_bu)
    conv = resolve_active_global_conversation(global_bu)
    assert conv is not None
    with pytest.raises(ValueError, match="tenant in scope"):
        record_message(conv, role="user", content="x")
