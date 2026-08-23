"""PrivacyConsentSkill + data_export/data_delete tests (DRF-469 / Sprint 3 / D2)."""

from __future__ import annotations

import hashlib
import uuid

import pytest

from apps.audit.models import AuditLog
from apps.consent.models import ConsentRecord
from apps.consent.services import grant
from apps.conversations.models import Conversation, Message
from apps.events.models import Event
from apps.identity.models import BotUser
from apps.skills.base import SkillContext
from apps.skills.privacy_consent.skill import PrivacyConsentSkill
from apps.skills.privacy_consent.tools import data_delete, data_export
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="pc-a", name="PC")


@pytest.fixture
def bot_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="pc-bot",
        display_name="Ivan",
        phone="+79991234567",
    )


@pytest.fixture
def conversation(tenant, bot_user) -> Conversation:
    return Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)


class TestMatches:
    @pytest.mark.parametrize(
        "text",
        [
            "удалить мои данные",
            "Удалите меня пожалуйста",
            "delete my data",
            "Please DELETE ME from your system",
            "выгрузить мои данные",
            "Export my data please",
            "скачать мои данные",
            "download my data",
        ],
    )
    def test_matches_trigger_phrases(self, text, bot_user, conversation):
        ctx = SkillContext(conversation=conversation, bot_user=bot_user, message_text=text)
        assert PrivacyConsentSkill().matches(ctx) is True

    @pytest.mark.parametrize(
        "text",
        [
            "hello",
            "записаться на массаж",
            "какое расписание сегодня",
            "",
        ],
    )
    def test_does_not_match_unrelated(self, text, bot_user, conversation):
        ctx = SkillContext(conversation=conversation, bot_user=bot_user, message_text=text)
        assert PrivacyConsentSkill().matches(ctx) is False


class TestDataExport:
    def test_returns_dict_with_required_keys(self, tenant, bot_user, conversation):
        Message.all_tenants.create(
            tenant=tenant, conversation=conversation, role="user", content="hi"
        )
        with tenant_scope(tenant):
            grant(bot_user, consent_type="personal_data", source="reg")
            export = data_export(bot_user)
        assert "bot_user" in export
        assert "consents" in export
        assert "conversations" in export
        assert export["bot_user"]["channel_user_id"] == "pc-bot"
        assert len(export["consents"]) == 1
        assert export["consents"][0]["consent_type"] == "personal_data"
        assert len(export["conversations"]) == 1
        assert len(export["conversations"][0]["messages"]) == 1

    def test_phone_hashed_not_raw(self, tenant, bot_user, conversation):
        with tenant_scope(tenant):
            export = data_export(bot_user)
        expected = hashlib.sha256(b"+79991234567").hexdigest()
        assert export["bot_user"]["phone_hash"] == expected
        assert "+79991234567" not in str(export)

    def test_writes_audit_rows(self, tenant, bot_user):
        with tenant_scope(tenant):
            data_export(bot_user)
        assert AuditLog.all_tenants.filter(
            tenant=tenant, action="privacy.data_export.requested"
        ).exists()
        assert AuditLog.all_tenants.filter(
            tenant=tenant, action="privacy.data_export.completed"
        ).exists()


class TestDataDelete:
    def test_audit_row_written_BEFORE_delete(self, tenant, bot_user, conversation):
        """Audit row must exist with the original bot_user.id even after delete."""

        bot_user_id = bot_user.id
        with tenant_scope(tenant):
            data_delete(bot_user)
        # The "requested" row was written before delete — must exist with the
        # original target_id, even though BotUser is now gone.
        assert AuditLog.all_tenants.filter(
            tenant=tenant,
            action="privacy.data_delete.requested",
            target_id=bot_user_id,
        ).exists()

    def test_cascades_conversations_messages_consents(self, tenant, bot_user, conversation):
        Message.all_tenants.create(
            tenant=tenant, conversation=conversation, role="user", content="msg"
        )
        with tenant_scope(tenant):
            grant(bot_user, consent_type="personal_data", source="reg")

        bot_user_id = bot_user.id
        conv_id = conversation.id
        with tenant_scope(tenant):
            count = data_delete(bot_user)
        assert count >= 1

        # BotUser gone.
        assert not BotUser.all_tenants.filter(pk=bot_user_id).exists()
        # Conversation gone (hard-deleted by delete_bot_user_data).
        assert not Conversation.all_tenants.filter(pk=conv_id).exists()
        # Messages gone (Conversation CASCADE).
        assert not Message.all_tenants.filter(conversation_id=conv_id).exists()

    def test_emits_tool_called_event(self, tenant, bot_user):
        with tenant_scope(tenant):
            data_delete(bot_user)
        ev = Event.objects.filter(
            tenant=tenant, event_name="tool_called", properties__tool="data_delete"
        ).first()
        assert ev is not None

    def test_raises_protected_error_on_ai_metric(self, tenant, bot_user):
        """Locks WHY the chat path no longer calls this (DRF-956 blocker A).

        `delete_bot_user_data` hard-deletes the BotUser row, and
        `AIRequestMetric.bot_user` is PROTECT — so any user who ever
        triggered one AI turn cannot be deleted this way. The helper is
        admin-only now; re-wiring it to a customer surface must first
        resolve these references.
        """
        from django.db.models import ProtectedError

        from apps.observability.models import AIRequestMetric

        AIRequestMetric.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            request_id=uuid.uuid4(),
            message_text_length=10,
            latency_total_ms=100,
            outcome=AIRequestMetric.OUTCOME_SUCCESS,
        )
        with tenant_scope(tenant), pytest.raises(ProtectedError):
            data_delete(bot_user)

    def test_staff_assistant_thread_wiped_with_the_user(self, tenant, bot_user):
        """DRF-1276 — the staff assistant thread no longer blocks the wipe.

        ``StaffAssistantThread.bot_user`` is PROTECT, deliberately
        mirroring ``Conversation.bot_user`` — and until DRF-1276 only half
        the mirror was built: ``delete_bot_user_data`` cleared the
        Conversations before ``bot_user.delete()``, but nothing cleared
        the threads, so an employee who ever wrote to the assistant made
        the whole cascade abort on ProtectedError. The threads now get the
        same treatment: soft-delete breadcrumb, then hard delete, and the
        messages cascade off the thread.
        """
        from apps.conversations.models import StaffAssistantMessage, StaffAssistantThread
        from apps.conversations.staff_assistant import (
            record_staff_message,
            resolve_active_staff_thread,
        )

        with tenant_scope(tenant):
            thread = resolve_active_staff_thread(bot_user, role_at_open="master")
            assert thread is not None
            record_staff_message(thread, role="user", content="во сколько я завтра")

        with tenant_scope(tenant):
            data_delete(bot_user)

        assert not StaffAssistantMessage.all_tenants.filter(thread_id=thread.id).exists()
        assert not StaffAssistantThread.all_tenants.filter(bot_user_id=bot_user.id).exists()
        assert not BotUser.all_tenants.filter(pk=bot_user.id).exists()


class TestSkillHandle:
    """DRF-956 / T-05 — the chat delete intent is a redirect, not a delete."""

    @pytest.mark.parametrize(
        "text",
        ["удалить мои данные", "удали меня", "delete my data", "DELETE ME"],
    )
    def test_delete_intent_does_not_mutate(self, tenant, bot_user, conversation, text):
        """One message must never be enough to erase an account."""
        Message.all_tenants.create(
            tenant=tenant, conversation=conversation, role="user", content="msg"
        )
        with tenant_scope(tenant):
            grant(bot_user, consent_type="personal_data", source="reg")
            result = PrivacyConsentSkill().handle(
                SkillContext(conversation=conversation, bot_user=bot_user, message_text=text)
            )

        assert result.should_send is not False
        # Nothing was touched: user, conversation, messages, consents.
        bot_user.refresh_from_db()
        assert bot_user.phone == "+79991234567"
        assert bot_user.display_name == "Ivan"
        assert bot_user.deleted_at is None
        conversation.refresh_from_db()
        assert conversation.deleted_at is None
        assert Message.all_tenants.filter(conversation=conversation).count() == 1
        assert ConsentRecord.all_tenants.filter(
            bot_user=bot_user, withdrawn_at__isnull=True
        ).exists()

    def test_delete_intent_does_not_claim_success(self, tenant, bot_user, conversation):
        """No false success — the reply must say plainly that nothing went."""
        ctx = SkillContext(
            conversation=conversation,
            bot_user=bot_user,
            message_text="удалить мои данные",
        )
        with tenant_scope(tenant):
            result = PrivacyConsentSkill().handle(ctx)

        assert "ничего не удалила" in result.reply_text
        assert "Мои данные" in result.reply_text  # where the confirmed flow lives
        assert "удалены" not in result.reply_text
        assert result.meta["outcome"] == "redirected_to_confirmed_flow"
        # Conversation stays open — nothing terminal happened.
        assert result.should_close_conversation is False

    def test_delete_intent_does_not_raise_on_protected_references(
        self, tenant, bot_user, conversation
    ):
        """Blocker A: a user with an AIRequestMetric used to 500 here."""
        from apps.observability.models import AIRequestMetric

        AIRequestMetric.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            request_id=uuid.uuid4(),
            message_text_length=10,
            latency_total_ms=100,
            outcome=AIRequestMetric.OUTCOME_SUCCESS,
        )
        ctx = SkillContext(
            conversation=conversation,
            bot_user=bot_user,
            message_text="удалить мои данные",
        )
        with tenant_scope(tenant):
            result = PrivacyConsentSkill().handle(ctx)  # must not raise

        assert result.reply_text
        assert BotUser.all_tenants.filter(pk=bot_user.id).exists()

    def test_delete_intent_still_emits_dispatch_event(self, tenant, bot_user, conversation):
        """Gated ≠ invisible — the privacy request stays observable."""
        ctx = SkillContext(
            conversation=conversation,
            bot_user=bot_user,
            message_text="удалить мои данные",
        )
        with tenant_scope(tenant):
            PrivacyConsentSkill().handle(ctx)

        ev = Event.objects.filter(
            tenant=tenant, event_name="skill_dispatched", properties__skill="privacy_consent"
        ).first()
        assert ev is not None
        assert ev.properties["intent"] == "delete"

    def test_no_destructive_tool_event_emitted(self, tenant, bot_user, conversation):
        ctx = SkillContext(
            conversation=conversation,
            bot_user=bot_user,
            message_text="удалить мои данные",
        )
        with tenant_scope(tenant):
            PrivacyConsentSkill().handle(ctx)

        assert not Event.objects.filter(
            tenant=tenant, event_name="tool_called", properties__tool="data_delete"
        ).exists()

    def test_export_intent_returns_json_in_reply(self, tenant, bot_user, conversation):
        ctx = SkillContext(
            conversation=conversation,
            bot_user=bot_user,
            message_text="выгрузить мои данные",
        )
        with tenant_scope(tenant):
            result = PrivacyConsentSkill().handle(ctx)
        assert result.should_close_conversation is False
        # JSON archive inline in the reply.
        assert "Ваши данные" in result.reply_text
        assert "conversations" in result.reply_text
        # Raw phone NEVER leaked in the reply.
        assert "+79991234567" not in result.reply_text

    def test_skill_dispatched_event_emitted(self, tenant, bot_user, conversation):
        ctx = SkillContext(
            conversation=conversation,
            bot_user=bot_user,
            message_text="выгрузить мои данные",
        )
        with tenant_scope(tenant):
            PrivacyConsentSkill().handle(ctx)
        ev = Event.objects.filter(
            tenant=tenant, event_name="skill_dispatched", properties__skill="privacy_consent"
        ).first()
        assert ev is not None
        assert ev.properties["intent"] == "export"


class TestCrossTenantIsolation:
    def test_export_only_sees_own_tenant_data(self):
        a = Tenant.objects.create(slug="pc-iso-a", name="A")
        b = Tenant.objects.create(slug="pc-iso-b", name="B")
        bu_a = BotUser.all_tenants.create(tenant=a, channel="max", channel_user_id="a")
        bu_b = BotUser.all_tenants.create(tenant=b, channel="max", channel_user_id="b")
        Conversation.all_tenants.create(tenant=a, bot_user=bu_a)
        Conversation.all_tenants.create(tenant=b, bot_user=bu_b)

        # Export for bu_a — must not see B's conversations.
        with tenant_scope(a):
            export = data_export(bu_a)
        for conv in export["conversations"]:
            # The Conversation FK filter on bot_user=bu_a already isolates;
            # this is the belt-and-braces check.
            assert "id" in conv
        assert len(export["conversations"]) == 1
