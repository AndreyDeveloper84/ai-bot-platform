"""Conversation services tests (DRF-453 / Sprint 2 / B5).

Covers all three service entry points: `resolve_active_conversation`,
`record_message`, `close_conversation`. The tenant-invariant on
`record_message` gets explicit cross-tenant adversarial coverage so the
critical contract from B2's docstring is pinned.
"""

from __future__ import annotations

import pytest

from apps.audit.models import AuditLog
from apps.conversations.models import Conversation
from apps.conversations.services import (
    close_conversation,
    record_message,
    resolve_active_conversation,
)
from apps.events.models import Event
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope, trace_id_scope
from apps.tenancy.exceptions import CrossTenantError
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant_a() -> Tenant:
    return Tenant.objects.create(slug="svc-a", name="A")


@pytest.fixture
def tenant_b() -> Tenant:
    return Tenant.objects.create(slug="svc-b", name="B")


@pytest.fixture
def bot_user_a(tenant_a) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant_a, channel="max", channel_user_id="svc-a-bot")


@pytest.fixture
def bot_user_b(tenant_b) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant_b, channel="max", channel_user_id="svc-b-bot")


class TestResolveActiveConversation:
    def test_creates_when_missing(self, tenant_a, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            conv = resolve_active_conversation(bot_user_a)
        assert conv.tenant_id == tenant_a.id
        assert conv.bot_user_id == bot_user_a.id
        assert conv.is_active is True
        # Event + AuditLog emitted.
        assert Event.objects.filter(
            tenant=tenant_a, event_type="conversations.conversation.created"
        ).exists()
        assert AuditLog.all_tenants.filter(tenant=tenant_a, action="conversation.created").exists()

    def test_returns_existing_when_present(self, tenant_a, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            first = resolve_active_conversation(bot_user_a)
            second = resolve_active_conversation(bot_user_a)
        assert first.id == second.id
        # Only ONE conversation.created event — the second call returned
        # the existing row, no fresh creation.
        created_count = Event.objects.filter(
            tenant=tenant_a, event_type="conversations.conversation.created"
        ).count()
        assert created_count == 1

    def test_after_mark_deleted_creates_new(self, tenant_a, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            first = resolve_active_conversation(bot_user_a)
            first.mark_deleted()
            second = resolve_active_conversation(bot_user_a)
        assert second.id != first.id
        assert second.is_active is True

    def test_create_if_missing_false_returns_none(self, tenant_a, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            result = resolve_active_conversation(bot_user_a, create_if_missing=False)
        assert result is None

    def test_missing_tenant_context_raises(self, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "audit"
        with pytest.raises(ValueError, match="tenant in scope"):
            resolve_active_conversation(bot_user_a)


class TestRecordMessage:
    def test_persists_and_updates_last_message_at(self, tenant_a, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            conv = resolve_active_conversation(bot_user_a)
            msg = record_message(conv, role="user", content="привет")
        msg.refresh_from_db()
        conv.refresh_from_db()
        assert msg.tenant_id == tenant_a.id
        assert msg.content == "привет"
        assert conv.last_message_at is not None

    def test_uses_trace_id_from_context_var(self, tenant_a, bot_user_a, settings):
        import uuid as _uuid

        trace = _uuid.uuid4()
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a), trace_id_scope(str(trace)):
            conv = resolve_active_conversation(bot_user_a)
            msg = record_message(conv, role="user", content="x")
        msg.refresh_from_db()
        assert msg.trace_id == trace

    def test_explicit_trace_id_overrides_context_var(self, tenant_a, bot_user_a, settings):
        import uuid as _uuid

        ctx_trace = _uuid.uuid4()
        explicit = _uuid.uuid4()
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a), trace_id_scope(str(ctx_trace)):
            conv = resolve_active_conversation(bot_user_a)
            msg = record_message(conv, role="user", content="x", trace_id=explicit)
        msg.refresh_from_db()
        assert msg.trace_id == explicit

    def test_cross_tenant_raises_cross_tenant_error(self, tenant_a, tenant_b, bot_user_a, settings):
        """Handler bug protection: Conversation resolved in tenant A,
        then record_message called while current_tenant() is B → must
        raise instead of silently writing the wrong tenant on Message.
        """

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            conv_a = resolve_active_conversation(bot_user_a)
        # Now switch context to tenant_b and try to write to conv_a.
        with tenant_scope(tenant_b), pytest.raises(CrossTenantError):
            record_message(conv_a, role="user", content="leak attempt")

    def test_missing_tenant_context_raises(self, tenant_a, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "audit"
        with tenant_scope(tenant_a):
            conv = resolve_active_conversation(bot_user_a)
        # Drop out of scope.
        with pytest.raises(ValueError, match="tenant in scope"):
            record_message(conv, role="user", content="x")


class TestCloseConversation:
    def test_sets_outcome_and_inactive(self, tenant_a, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            conv = resolve_active_conversation(bot_user_a)
            close_conversation(conv, outcome=Conversation.Outcome.SUCCESS)
        conv.refresh_from_db()
        assert conv.is_active is False
        assert conv.outcome == Conversation.Outcome.SUCCESS
        # Audit row written.
        assert AuditLog.all_tenants.filter(action="conversation.closed").exists()

    def test_invalid_outcome_raises(self, tenant_a, bot_user_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            conv = resolve_active_conversation(bot_user_a)
        with pytest.raises(ValueError, match="not a valid"):
            close_conversation(conv, outcome="nonsense")


class TestEndToEndChain:
    def test_full_turn_creates_two_messages_under_same_trace(self, tenant_a, bot_user_a, settings):
        """Sprint 2 echo handler skeleton: user + assistant messages
        share the same Conversation and the same trace_id.
        """

        import uuid as _uuid

        trace = _uuid.uuid4()
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a), trace_id_scope(str(trace)):
            conv = resolve_active_conversation(bot_user_a)
            user_msg = record_message(conv, role="user", content="Привет")
            asst_msg = record_message(
                conv, role="assistant", content="Привет", rendered_text="Привет"
            )
        assert user_msg.conversation_id == asst_msg.conversation_id
        assert user_msg.trace_id == trace
        assert asst_msg.trace_id == trace
        conv.refresh_from_db()
        assert conv.last_message_at is not None


# ---------------------------------------------------------------------------
# Conversations retro hotfix coverage
# ---------------------------------------------------------------------------


class TestRetroB2CrossTenantBotUser:
    """Pre-fix ``resolve_active_conversation`` filtered only by bot_user +
    is_active. ``Conversation.objects`` is TenantScopedManager which auto-
    adds the tenant filter, but a caller passing a bot_user from a
    different tenant than ``current_tenant()`` was silently invalid —
    the row miss + IntegrityError-recovery created or returned a row
    linked to the wrong tenant. Post-fix raises CrossTenantError loudly.
    """

    def test_cross_tenant_bot_user_raises(
        self,
        tenant_a: Tenant,
        tenant_b: Tenant,
        bot_user_b: BotUser,
    ) -> None:
        from apps.conversations.services import resolve_active_conversation

        # Enter tenant_a scope but pass a bot_user from tenant_b — that's
        # the programmer-error case the defence-in-depth check guards.
        with tenant_scope(tenant_a):
            with pytest.raises(CrossTenantError):
                resolve_active_conversation(bot_user_b)


class TestRetroB1SkillStateAtomicWrite:
    """Pre-fix skills did read-modify-write on Conversation.skill_state.
    Two concurrent writers to different sub-keys would clobber each other:
    each read the same pre-image and ``save(update_fields=["skill_state"])``
    replaced the column with the writer's single-key view.

    Post-fix ``write_skill_state`` wraps the read-modify-write in
    ``transaction.atomic`` + ``select_for_update`` so per-subkey writes
    serialise instead of clobbering.
    """

    def test_sequential_writes_preserve_distinct_subkeys(
        self,
        tenant_a: Tenant,
        bot_user_a: BotUser,
    ) -> None:
        from apps.conversations.services import (
            resolve_active_conversation,
            write_skill_state,
        )

        with tenant_scope(tenant_a):
            conv = resolve_active_conversation(bot_user_a)
            assert conv is not None
            write_skill_state(conv, "skill_one", {"step": "a"})
            write_skill_state(conv, "skill_two", {"step": "b"})

        # Re-fetch to confirm both subkeys land — pre-fix the second
        # writer's update_fields would have wiped skill_one.
        conv.refresh_from_db()
        assert conv.skill_state == {
            "skill_one": {"step": "a"},
            "skill_two": {"step": "b"},
        }

    def test_value_none_deletes_subkey_atomically(
        self,
        tenant_a: Tenant,
        bot_user_a: BotUser,
    ) -> None:
        from apps.conversations.services import (
            resolve_active_conversation,
            write_skill_state,
        )

        with tenant_scope(tenant_a):
            conv = resolve_active_conversation(bot_user_a)
            assert conv is not None
            write_skill_state(conv, "ephemeral", {"v": 1})
            write_skill_state(conv, "persistent", {"v": 2})
            write_skill_state(conv, "ephemeral", None)

        conv.refresh_from_db()
        assert conv.skill_state == {"persistent": {"v": 2}}

    def test_write_without_tenant_scope_raises(
        self,
        tenant_a: Tenant,
        bot_user_a: BotUser,
    ) -> None:
        from apps.conversations.services import (
            resolve_active_conversation,
            write_skill_state,
        )

        with tenant_scope(tenant_a):
            conv = resolve_active_conversation(bot_user_a)
            assert conv is not None

        # OUTSIDE tenant_scope — must fail loud.
        with pytest.raises(ValueError, match="tenant in scope"):
            write_skill_state(conv, "key", {"v": 1})

    def test_cross_tenant_conversation_raises(
        self,
        tenant_a: Tenant,
        tenant_b: Tenant,
        bot_user_a: BotUser,
    ) -> None:
        from apps.conversations.services import (
            resolve_active_conversation,
            write_skill_state,
        )

        with tenant_scope(tenant_a):
            conv = resolve_active_conversation(bot_user_a)
        assert conv is not None  # noqa: S101 — narrow Optional for mypy
        # tenant_a's conversation passed in while tenant_b is in scope.
        with tenant_scope(tenant_b):
            with pytest.raises(CrossTenantError):
                write_skill_state(conv, "key", {"v": 1})


class TestRetroY6StateCheckConstraint:
    """Reviewer Y-3: assert the DB-level CHECK constraint actually
    rejects out-of-enum state values. Pre-fix Django only validated
    state at full_clean() — bypassed by ``.update()`` calls. Post-fix
    the CHECK constraint rejects at the DB layer.

    Postgres-only: SQLite does not enforce CHECK constraints emitted
    by Django's ``models.CheckConstraint`` for table-level checks
    consistently across versions.
    """

    @pytest.mark.skipif(
        Conversation._meta.app_label  # noqa: SLF001 — sentinel guard
        and getattr(
            __import__("django.db", fromlist=["connection"]).connection,
            "vendor",
            "",
        )
        != "postgresql",
        reason="CHECK constraint enforcement requires Postgres",
    )
    def test_bogus_state_raises_integrity_error(
        self,
        tenant_a: Tenant,
        bot_user_a: BotUser,
    ) -> None:
        from django.db import IntegrityError

        from apps.conversations.services import resolve_active_conversation

        with tenant_scope(tenant_a):
            conv = resolve_active_conversation(bot_user_a)
            assert conv is not None
            with pytest.raises(IntegrityError):
                Conversation.all_tenants.filter(pk=conv.pk).update(state="zombie")
