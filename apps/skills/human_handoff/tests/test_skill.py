"""HumanHandoffSkill + dispatcher state guard tests (DRF-470 / Sprint 3 / D3)."""

from __future__ import annotations

import pytest

from apps.conversations.models import Conversation
from apps.events.models import Event
from apps.handoff.models import AdminTask
from apps.handoff.services import resolve_admin_task
from apps.identity.models import BotUser
from apps.skills.base import SkillContext
from apps.skills.human_handoff.skill import HumanHandoffSkill
from apps.skills.registry import dispatch
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="hh-a", name="HH")


@pytest.fixture
def bot_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="hh-bot")


@pytest.fixture
def conversation(tenant, bot_user) -> Conversation:
    return Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)


class TestMatches:
    @pytest.mark.parametrize(
        "text",
        [
            "оператор",
            "Хочу оператора",
            "Соедините со связь с человеком",
            "human please",
            "I want a manager",
            "talk to agent",
            # DRF-972: русские формулировки, которыми реально пишут пользователи.
            "позовите менеджера",
            "хочу поговорить с менеджером",
            "нужен администратор",
            "позовите админа",
        ],
    )
    def test_matches_trigger_phrases(self, text, bot_user, conversation):
        ctx = SkillContext(conversation=conversation, bot_user=bot_user, message_text=text)
        assert HumanHandoffSkill().matches(ctx) is True

    @pytest.mark.parametrize(
        "text",
        [
            "hello",
            "записаться",
            "выгрузить мои данные",
            "",
            # DRF-972: «человек» сознательно НЕ добавлено в словарь — слишком
            # высокий риск ложного срабатывания на бытовых оборотах.
            "я человек занятой",
            "нужен мастер, который понимает человека",
        ],
    )
    def test_does_not_match(self, text, bot_user, conversation):
        ctx = SkillContext(conversation=conversation, bot_user=bot_user, message_text=text)
        assert HumanHandoffSkill().matches(ctx) is False


class TestHandle:
    def test_creates_admin_task_and_flips_state(self, tenant, bot_user, conversation):
        ctx = SkillContext(
            conversation=conversation,
            bot_user=bot_user,
            message_text="оператор",
        )
        with tenant_scope(tenant):
            result = HumanHandoffSkill().handle(ctx)

        assert result.new_state == "human_handoff"
        assert "менеджеру" in result.reply_text
        # AdminTask row materialised in DB.
        assert AdminTask.all_tenants.filter(conversation=conversation).count() == 1
        # Conversation state flipped (C2 internal does this).
        conversation.refresh_from_db()
        assert conversation.state == "human_handoff"

    def test_emits_handoff_initiated_event(self, tenant, bot_user, conversation):
        ctx = SkillContext(
            conversation=conversation,
            bot_user=bot_user,
            message_text="оператор",
        )
        with tenant_scope(tenant):
            HumanHandoffSkill().handle(ctx)
        # C2 already emits `handoff_initiated` as part of create_admin_task,
        # so we verify it landed via the canonical event_name.
        assert Event.objects.filter(tenant=tenant, event_name="handoff_initiated").exists()


class TestDispatcherGuard:
    """The HUMAN_HANDOFF guard sits in registry.dispatch()."""

    def test_dispatcher_silent_when_state_is_human_handoff(self, tenant, bot_user, conversation):
        # First message triggers handoff.
        ctx_trigger = SkillContext(
            conversation=conversation,
            bot_user=bot_user,
            message_text="оператор",
        )
        with tenant_scope(tenant):
            dispatch(ctx_trigger)
        conversation.refresh_from_db()
        assert conversation.state == "human_handoff"

        # Subsequent message — must NOT route to a skill, must NOT echo.
        ctx_followup = SkillContext(
            conversation=conversation,
            bot_user=bot_user,
            message_text="алло? есть кто живой?",
        )
        with tenant_scope(tenant):
            result = dispatch(ctx_followup)
        assert result is not None
        assert result.should_send is False
        assert result.reply_text == ""
        assert (result.meta or {}).get("silenced_by") == "human_handoff"

    def test_resume_after_resolve(self, tenant, bot_user, conversation):
        """resolve_admin_task flips state back to IDLE → bot resumes dispatch."""

        ctx = SkillContext(
            conversation=conversation,
            bot_user=bot_user,
            message_text="оператор",
        )
        with tenant_scope(tenant):
            dispatch(ctx)
            task = AdminTask.all_tenants.filter(conversation=conversation).first()
            assert task is not None
            resolve_admin_task(task, resolution_note="done")

        conversation.refresh_from_db()
        assert conversation.state == "idle"

        # Now a normal text → the menu skill takes it as the last-resort
        # text responder (DRF-963 replaced the verbatim echo with the
        # honest fallback). What matters here is that the bot SPEAKS again.
        from apps.skills.menu.replies import FALLBACK_TEXT

        ctx_again = SkillContext(
            conversation=conversation,
            bot_user=bot_user,
            message_text="hello again",
        )
        with tenant_scope(tenant):
            result = dispatch(ctx_again)
        assert result is not None
        assert result.should_send is True
        assert result.reply_text == FALLBACK_TEXT
