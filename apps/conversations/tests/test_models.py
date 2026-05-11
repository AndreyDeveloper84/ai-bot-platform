"""Conversation + Message model contract tests (DRF-453 / Sprint 2 / B5)."""

from __future__ import annotations

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.conversations.models import Conversation, Message
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _on_sqlite() -> bool:
    """The conditional unique constraint uses Postgres partial unique
    indexes; SQLite silently no-ops the condition. Tests that depend on
    the constraint must skip on SQLite.
    """

    return connection.vendor == "sqlite"


@pytest.fixture
def tenant_a() -> Tenant:
    return Tenant.objects.create(slug="conv-a", name="A")


@pytest.fixture
def tenant_b() -> Tenant:
    return Tenant.objects.create(slug="conv-b", name="B")


@pytest.fixture
def bot_user_a(tenant_a) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant_a, channel="max", channel_user_id="conv-a-bot")


@pytest.fixture
def bot_user_b(tenant_b) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant_b, channel="max", channel_user_id="conv-b-bot")


class TestConversationBasics:
    def test_defaults_idle_and_active(self, tenant_a, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            c = Conversation.objects.create(tenant=tenant_a, bot_user=bot_user_a)
        assert c.state == Conversation.State.IDLE
        assert c.outcome == ""
        assert c.is_active is True
        assert c.deleted_at is None

    def test_mark_deleted_flips_active_and_stamps(self, tenant_a, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            c = Conversation.objects.create(tenant=tenant_a, bot_user=bot_user_a)
            c.mark_deleted()
            c.refresh_from_db()
        assert c.is_active is False
        assert c.deleted_at is not None


class TestConversationUniqueness:
    @pytest.mark.skipif(_on_sqlite(), reason="partial unique index needs Postgres")
    def test_two_active_conversations_same_bot_user_blocked(self, tenant_a, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            Conversation.objects.create(tenant=tenant_a, bot_user=bot_user_a)
        with (
            tenant_scope(tenant_a),
            transaction.atomic(),
            pytest.raises(IntegrityError),
        ):
            Conversation.objects.create(tenant=tenant_a, bot_user=bot_user_a)

    @pytest.mark.skipif(_on_sqlite(), reason="partial unique index needs Postgres")
    def test_mark_deleted_unblocks_replacement(self, tenant_a, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            first = Conversation.objects.create(tenant=tenant_a, bot_user=bot_user_a)
            first.mark_deleted()
            second = Conversation.objects.create(tenant=tenant_a, bot_user=bot_user_a)
        assert second.id != first.id
        assert second.is_active is True


class TestConversationManager:
    def test_default_manager_filters_by_tenant(
        self, tenant_a, tenant_b, bot_user_a, bot_user_b, settings
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        ca = Conversation.all_tenants.create(tenant=tenant_a, bot_user=bot_user_a)
        Conversation.all_tenants.create(tenant=tenant_b, bot_user=bot_user_b)

        with tenant_scope(tenant_a):
            ids = list(Conversation.objects.values_list("id", flat=True))
        assert ids == [ca.id]


class TestMessageBasics:
    def test_role_choices_required(self, tenant_a, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            conv = Conversation.objects.create(tenant=tenant_a, bot_user=bot_user_a)
            m = Message.objects.create(
                conversation=conv,
                tenant=tenant_a,
                role=Message.Role.USER,
                content="привет",
            )
        assert m.role == Message.Role.USER
        assert m.tokens_in == 0
        assert m.created_at is not None

    def test_content_preview_in_str(self, tenant_a, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            conv = Conversation.objects.create(tenant=tenant_a, bot_user=bot_user_a)
            m = Message.objects.create(
                conversation=conv,
                tenant=tenant_a,
                role=Message.Role.ASSISTANT,
                content="Hello there",
            )
        assert "Hello" in str(m)
        assert "assistant" in str(m)


class TestMessageManager:
    def test_filters_by_tenant(self, tenant_a, tenant_b, bot_user_a, bot_user_b, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        conv_a = Conversation.all_tenants.create(tenant=tenant_a, bot_user=bot_user_a)
        conv_b = Conversation.all_tenants.create(tenant=tenant_b, bot_user=bot_user_b)
        ma = Message.all_tenants.create(
            conversation=conv_a, tenant=tenant_a, role="user", content="A"
        )
        Message.all_tenants.create(conversation=conv_b, tenant=tenant_b, role="user", content="B")

        with tenant_scope(tenant_a):
            ids = list(Message.objects.values_list("id", flat=True))
        assert ids == [ma.id]


class TestLastMessageAtOrdering:
    def test_default_ordering_by_last_message_at_desc(self, tenant_a, bot_user_a, settings):
        """Conversation default ordering surfaces the most-recent first."""

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            older = Conversation.objects.create(tenant=tenant_a, bot_user=bot_user_a)
            older.last_message_at = timezone.now() - timezone.timedelta(hours=1)
            older.save(update_fields=["last_message_at"])
            older.mark_deleted()  # free the unique-active slot
            newer = Conversation.objects.create(tenant=tenant_a, bot_user=bot_user_a)
            newer.last_message_at = timezone.now()
            newer.save(update_fields=["last_message_at"])

            ids = list(
                Conversation.all_tenants.filter(tenant=tenant_a).values_list("id", flat=True)
            )
        assert ids == [newer.id, older.id]
