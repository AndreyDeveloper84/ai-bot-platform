"""Skill base + registry + dispatcher tests (DRF-468 / Sprint 3 / D1)."""

from __future__ import annotations

from typing import ClassVar

import pytest

from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.skills.base import Skill, SkillContext, SkillResult
from apps.skills.registry import (
    _skills,
    dispatch,
    register,
    registered,
    reset_registry_cache,
)
from apps.tenancy.models import Tenant


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Each test starts with an empty registry, restores prior state after."""

    saved = list(_skills)
    _skills.clear()
    yield
    _skills.clear()
    _skills.extend(saved)


@pytest.fixture
def context(db) -> SkillContext:
    tenant = Tenant.objects.create(slug="sk-a", name="SkA")
    bu = BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="sk-bot")
    conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bu)
    return SkillContext(conversation=conv, bot_user=bu, message_text="hello world")


class TestSkillProtocol:
    def test_protocol_satisfied_by_duck_typed_class(self):
        class DuckSkill:
            name: ClassVar[str] = "duck"

            def matches(self, ctx):
                return True

            def handle(self, ctx):
                return SkillResult(reply_text="quack")

        assert isinstance(DuckSkill(), Skill)


class TestRegister:
    def test_register_returns_class_unchanged(self):
        @register
        class A:
            name: ClassVar[str] = "a"

            def matches(self, ctx):
                return True

            def handle(self, ctx):
                return SkillResult(reply_text="A")

        # Decorator returns the class so isinstance/checks still work.
        assert A.__name__ == "A"
        assert len(_skills) == 1
        assert _skills[0].name == "a"

    def test_register_appends_in_order(self):
        @register
        class First:
            name: ClassVar[str] = "first"

            def matches(self, ctx):
                return True

            def handle(self, ctx):
                return SkillResult(reply_text="first")

        @register
        class Second:
            name: ClassVar[str] = "second"

            def matches(self, ctx):
                return True

            def handle(self, ctx):
                return SkillResult(reply_text="second")

        assert [s.name for s in registered()] == ["first", "second"]


class TestDispatch:
    def test_first_match_wins(self, context):
        @register
        class NeverMatches:
            name: ClassVar[str] = "never"

            def matches(self, ctx):
                return False

            def handle(self, ctx):
                return SkillResult(reply_text="should not run")

        @register
        class AlwaysMatches:
            name: ClassVar[str] = "always"

            def matches(self, ctx):
                return True

            def handle(self, ctx):
                return SkillResult(reply_text="picked")

        result = dispatch(context)
        assert result is not None
        assert result.reply_text == "picked"

    def test_no_match_returns_none(self, context):
        @register
        class NeverA:
            name: ClassVar[str] = "a"

            def matches(self, ctx):
                return False

            def handle(self, ctx):
                return SkillResult(reply_text="x")

        assert dispatch(context) is None

    def test_dispatch_short_circuits_after_first_match(self, context):
        calls: list[str] = []

        @register
        class First:
            name: ClassVar[str] = "first"

            def matches(self, ctx):
                calls.append("first.matches")
                return True

            def handle(self, ctx):
                calls.append("first.handle")
                return SkillResult(reply_text="first")

        @register
        class Second:
            name: ClassVar[str] = "second"

            def matches(self, ctx):
                calls.append("second.matches")
                return True

            def handle(self, ctx):
                calls.append("second.handle")
                return SkillResult(reply_text="second")

        dispatch(context)
        # First short-circuits — second never invoked.
        assert calls == ["first.matches", "first.handle"]

    def test_exception_in_handle_bubbles(self, context):
        @register
        class Boom:
            name: ClassVar[str] = "boom"

            def matches(self, ctx):
                return True

            def handle(self, ctx):
                raise RuntimeError("kaboom")

        with pytest.raises(RuntimeError, match="kaboom"):
            dispatch(context)


class TestEchoSkill:
    """Verifies the legacy Sprint 2 echo behavior survives skill dispatch."""

    def test_echo_handles_start_with_welcome(self, context):
        from apps.skills.echo.skill import EchoSkill, _WELCOME_TEXT

        skill = EchoSkill()
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="/start",
        )
        result = skill.handle(ctx)
        assert result.reply_text == _WELCOME_TEXT
        assert result.meta["reply_kind"] == "welcome"

    def test_echo_handles_text_verbatim(self, context):
        from apps.skills.echo.skill import EchoSkill

        skill = EchoSkill()
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="just echo this",
        )
        result = skill.handle(ctx)
        assert result.reply_text == "just echo this"
        assert result.meta["reply_kind"] == "echo"

    def test_echo_handles_attachment_only_with_no_echo(self, context):
        from apps.skills.echo.skill import EchoSkill, _FALLBACK_NO_ECHO

        skill = EchoSkill()
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="",
            has_attachments=True,
        )
        result = skill.handle(ctx)
        assert result.reply_text == _FALLBACK_NO_ECHO
        assert result.meta["reply_kind"] == "no_echo"

    def test_echo_handles_empty_with_question_mark(self, context):
        from apps.skills.echo.skill import EchoSkill, _FALLBACK_EMPTY

        skill = EchoSkill()
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="",
        )
        result = skill.handle(ctx)
        assert result.reply_text == _FALLBACK_EMPTY
        assert result.meta["reply_kind"] == "empty_prompt"

    def test_echo_always_matches(self, context):
        from apps.skills.echo.skill import EchoSkill

        assert EchoSkill().matches(context) is True


def test_reset_registry_cache_clears():
    @register
    class X:
        name: ClassVar[str] = "x"

        def matches(self, ctx):
            return True

        def handle(self, ctx):
            return SkillResult(reply_text="x")

    assert len(registered()) == 1
    reset_registry_cache()
    assert registered() == []
