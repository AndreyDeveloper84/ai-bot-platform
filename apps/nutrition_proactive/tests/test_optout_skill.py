"""The chat off-switch (DRF-1285).

Two properties matter and both are tested here: one message stops everything,
and no message that is not a request to stop can trigger it.
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from unittest.mock import Mock

import pytest

from apps.identity.models import BotUser
from apps.nutrition_proactive import prefs, selection
from apps.nutrition_proactive.optout_skill import (
    OPT_OUT_PHRASES,
    ProactiveOptOutSkill,
    normalise,
)
from apps.skills.base import SkillContext
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="np-optout", name="Salon")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    now = datetime(2026, 5, 1, tzinfo=dt_timezone.utc)
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="np-oo-1",
        chat_id="chat-oo-1",
        consent_at=now,
        food_scanner_consent_at=now,
        context={prefs.CONTEXT_KEY: {"water_reminders": True, "daily_report_time": "21:00"}},
    )


def ctx(bot_user: BotUser, text: str) -> SkillContext:
    # The skill never reads the conversation; a Mock keeps the fixture
    # honest about that (same convention as apps/skills/water/tests).
    return SkillContext(conversation=Mock(), bot_user=bot_user, message_text=text)


class TestMatching:
    @pytest.mark.parametrize("phrase", sorted(OPT_OUT_PHRASES))
    def test_every_declared_phrase_matches(self, bot_user: BotUser, phrase: str) -> None:
        assert ProactiveOptOutSkill().matches(ctx(bot_user, phrase)) is True

    @pytest.mark.parametrize(
        "text",
        [
            "Не пиши мне!",
            "  не пиши мне  ",
            "НЕ ПИШИ МНЕ",
            "Отключи напоминания.",
        ],
    )
    def test_casing_punctuation_and_padding_are_tolerated(
        self, bot_user: BotUser, text: str
    ) -> None:
        assert ProactiveOptOutSkill().matches(ctx(bot_user, text)) is True

    @pytest.mark.parametrize(
        "text",
        [
            "стоп, а во сколько вы работаете?",
            "не пиши мне пока я не попрошу, а напоминания оставь",
            "можно отключить напоминания?",
            "а что будет если написать stop",
            "",
            "   ",
            "не",
            "напоминания",
        ],
    )
    def test_a_message_that_is_not_a_request_to_stop_does_not_match(
        self, bot_user: BotUser, text: str
    ) -> None:
        """A false positive here is silent, so the match set is closed.

        Someone quietly unsubscribed from everything by a substring hit
        would keep writing to a bot that has stopped answering them first,
        and never learn why.
        """
        assert ProactiveOptOutSkill().matches(ctx(bot_user, text)) is False

    def test_yo_is_folded(self) -> None:
        assert normalise("Отпишись!") == "отпишись"


class TestEffect:
    def test_one_message_sets_the_platform_wide_veto(self, bot_user: BotUser) -> None:
        ProactiveOptOutSkill().handle(ctx(bot_user, "не пиши мне"))
        stored = BotUser.all_tenants.get(pk=bot_user.pk)
        assert stored.proactive_messages_opt_out is True

    def test_it_also_clears_both_nutrition_preferences(self, bot_user: BotUser) -> None:
        """So re-enabling the feature flag cannot resurrect a cancellation."""
        ProactiveOptOutSkill().handle(ctx(bot_user, "не пиши мне"))
        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=bot_user.pk))
        assert stored["daily_report_time"] == prefs.REPORT_OFF
        assert stored["water_reminders"] is False
        assert stored["opted_out_at"]

    def test_the_effect_is_immediate_for_the_next_beat_tick(self, bot_user: BotUser) -> None:
        assert bot_user.pk in {u.pk for u in selection.base_queryset()}
        ProactiveOptOutSkill().handle(ctx(bot_user, "не пиши мне"))
        assert bot_user.pk not in {u.pk for u in selection.base_queryset()}

    def test_the_reply_says_what_still_arrives(self, bot_user: BotUser) -> None:
        """Silence about booking reminders would be a lie by omission."""
        result = ProactiveOptOutSkill().handle(ctx(bot_user, "не пиши мне"))
        assert "не пишу первой" in result.reply_text
        assert "записях" in result.reply_text

    def test_repeating_it_is_harmless(self, bot_user: BotUser) -> None:
        skill = ProactiveOptOutSkill()
        skill.handle(ctx(bot_user, "не пиши мне"))
        skill.handle(ctx(bot_user, "не пиши мне"))
        assert BotUser.all_tenants.get(pk=bot_user.pk).proactive_messages_opt_out is True

    def test_other_context_keys_survive(self, bot_user: BotUser) -> None:
        BotUser.all_tenants.filter(pk=bot_user.pk).update(
            context={"last_followup_sent_at": "2026-08-22", prefs.CONTEXT_KEY: {}}
        )
        fresh = BotUser.all_tenants.get(pk=bot_user.pk)
        ProactiveOptOutSkill().handle(ctx(fresh, "не пиши мне"))
        stored = BotUser.all_tenants.get(pk=bot_user.pk)
        assert stored.context["last_followup_sent_at"] == "2026-08-22"


class TestRegistration:
    def test_the_skill_is_registered_ahead_of_the_catch_all_fallbacks(self) -> None:
        """Registered late, the request to stop would be echo-claimed."""
        from apps.skills.registry import registered

        names = [getattr(s, "name", "") for s in registered()]
        assert "proactive_opt_out" in names
        assert names.index("proactive_opt_out") < names.index("echo")
