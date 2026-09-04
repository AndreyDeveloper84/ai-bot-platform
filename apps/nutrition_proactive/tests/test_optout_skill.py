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
    matches_opt_out,
    normalise,
    try_handle_opt_out,
    try_handle_surface_stop,
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


class TestDoesNotStealBookingCancellations:
    """The collision CI found, kept found.

    This skill registers ahead of every domain skill, so any phrase it
    claims is a phrase the booking flow never sees. «отпиши меня» reads as
    "stop messaging me" in a vacuum and as "take me off the appointment" in
    a salon — and the second reading is the one the owner's corpus encodes.
    A person cancelling a visit who instead gets silently unsubscribed from
    everything is the exact failure the closed match set exists to prevent.

    Asserted against the owner's corpus itself rather than a copied list, so
    a phrase added there is checked here on the next run without anyone
    remembering to mirror it.
    """

    def test_no_cancellation_phrase_is_claimed(self, bot_user: BotUser) -> None:
        from apps.skills.booking.tests.test_lookup_routing import (
            OD_IR1_CANCEL_CORPUS,
        )

        skill = ProactiveOptOutSkill()
        stolen = [p for p in OD_IR1_CANCEL_CORPUS if skill.matches(ctx(bot_user, p))]
        assert stolen == []

    def test_the_two_sets_are_disjoint(self) -> None:
        from apps.skills.booking.tests.test_lookup_routing import (
            OD_IR1_CANCEL_CORPUS,
        )

        assert OPT_OUT_PHRASES & {normalise(p) for p in OD_IR1_CANCEL_CORPUS} == set()

    @pytest.mark.parametrize("phrase", ["отпиши меня", "отпишите меня", "отпишись", "отписаться"])
    def test_the_otpis_family_stays_out(self, bot_user: BotUser, phrase: str) -> None:
        """Named individually so re-adding one fails loudly rather than
        silently shifting a corpus test in another app."""
        assert ProactiveOptOutSkill().matches(ctx(bot_user, phrase)) is False

    def test_privacy_erasure_phrases_are_not_claimed_either(self, bot_user: BotUser) -> None:
        """`privacy_consent` owns «удали меня» / «удалите мои данные»."""
        skill = ProactiveOptOutSkill()
        for phrase in ("удали меня", "удалите мои данные", "удалить мои данные"):
            assert skill.matches(ctx(bot_user, phrase)) is False


class TestGlobalSurface:
    """The pilot runs the GLOBAL bot, and the global ladder never dispatches
    the skill registry (``apps/channels/max/handler.py`` reaches
    ``skills.registry`` only in ``_handle_max_event_inner``, the per-tenant
    path). Shipping the off-switch as a skill alone therefore shipped an
    off-switch nobody on the pilot could reach: «не пиши мне» would have gone
    to the concierge model, earned a friendly answer, and turned nothing off.
    """

    def test_the_global_entry_point_turns_everything_off(self, bot_user: BotUser) -> None:
        reply = try_handle_opt_out(text="не пиши мне", bot_user=bot_user)
        assert reply is not None
        stored = BotUser.all_tenants.get(pk=bot_user.pk)
        assert stored.proactive_messages_opt_out is True
        assert prefs.get_prefs(stored)["water_reminders"] is False

    def test_it_falls_through_on_anything_else(self, bot_user: BotUser) -> None:
        assert try_handle_opt_out(text="привет", bot_user=bot_user) is None
        assert BotUser.all_tenants.get(pk=bot_user.pk).proactive_messages_opt_out is False

    def test_it_never_raises(self) -> None:
        """A failure must cost a log line, not the person's turn."""

        class Exploding:
            pk = "nope"

            @property
            def context(self):
                raise RuntimeError("boom")

        assert try_handle_opt_out(text="не пиши мне", bot_user=Exploding()) is None

    def test_both_entry_points_share_one_implementation(self, bot_user: BotUser) -> None:
        """Skill and global branch must not drift — an off-switch that works
        on one surface and not the other is worse than none, because the
        person has already been told it worked."""
        skill_reply = ProactiveOptOutSkill().handle(ctx(bot_user, "не пиши мне"))
        BotUser.all_tenants.filter(pk=bot_user.pk).update(proactive_messages_opt_out=False)
        fresh = BotUser.all_tenants.get(pk=bot_user.pk)
        global_reply = try_handle_opt_out(text="не пиши мне", bot_user=fresh)
        assert skill_reply.reply_text == global_reply


class TestDoesNotStealNutritionTurns:
    """DRF-1268 put a deterministic nutrition branch on the global ladder and
    four nutrition tools in the concierge. This skill sits above it, so the
    same question as the booking collision applies: does it steal their turns?
    """

    def test_no_structured_nutrition_turn_is_claimed(self) -> None:
        """``/anketa``, ``cb:anketa:*`` and ``cb:food:*`` belong to the
        nutrition skills. None of them is whole-message opt-out text."""
        for phrase in (
            "/anketa",
            "cb:anketa:choice:goal:lose",
            "cb:food:to_diary:abc123",
            "cb:food:clarify:abc123",
            "cb:food:correct:portion:abc123",
        ):
            assert matches_opt_out(phrase) is False

    def test_the_nutrition_predicate_does_not_claim_opt_out_text(self) -> None:
        """The mirror direction, asserted against their predicate itself."""
        from apps.orchestrator.nutrition_global import is_structured_nutrition_turn

        for phrase in sorted(OPT_OUT_PHRASES):
            assert (
                is_structured_nutrition_turn(text=phrase, has_attachments=False, conversation=None)
                is False
            )

    def test_no_nutrition_tool_name_is_an_opt_out_phrase(self) -> None:
        from apps.orchestrator.nutrition_global import NUTRITION_TOOL_ACTIONS

        assert OPT_OUT_PHRASES & {normalise(n) for n in NUTRITION_TOOL_ACTIONS} == set()

    def test_opt_out_wins_over_an_active_anketa_fsm(self, bot_user: BotUser) -> None:
        """Mid-anketa, ANY free text is claimed by the nutrition branch —
        including «не пиши мне». That is why the opt-out branch is placed
        above it on the ladder: a person asking to be left alone is not
        answering a question about their weight.
        """
        from apps.orchestrator.nutrition_global import is_structured_nutrition_turn

        class FsmConversation:
            skill_state = {"nutrition_anketa": {"step": 2}}

        # Their predicate WOULD claim it...
        assert (
            is_structured_nutrition_turn(
                text="не пиши мне",
                has_attachments=False,
                conversation=FsmConversation(),
            )
            is True
        )
        # ...which is exactly why ours runs first and settles the turn.
        assert try_handle_opt_out(text="не пиши мне", bot_user=bot_user) is not None
        assert BotUser.all_tenants.get(pk=bot_user.pk).proactive_messages_opt_out is True

    def test_a_photo_turn_is_never_ours(self) -> None:
        """Photo-only turns go to the food scanner; empty text cannot match."""
        assert matches_opt_out("") is False


class TestRegistration:
    def test_the_skill_is_registered_ahead_of_the_catch_all_fallbacks(self) -> None:
        """Registered late, the request to stop would be echo-claimed."""
        from apps.skills.registry import registered

        names = [getattr(s, "name", "") for s in registered()]
        assert "proactive_opt_out" in names
        assert names.index("proactive_opt_out") < names.index("echo")


class TestSurfaceStopButton:
    """The one-tap unsubscribe on every proactive outbound (DRF-1468, R6).

    Unlike the text opt-out, the button silences ONE surface -- the person
    tapped «Не присылать» under a specific message, not «never write to me».
    The platform-wide veto therefore stays unset.
    """

    def test_the_report_button_turns_off_only_the_report(self, bot_user: BotUser) -> None:
        result = ProactiveOptOutSkill().handle(ctx(bot_user, "cb:nutri:stop:report"))
        stored_user = BotUser.all_tenants.get(pk=bot_user.pk)
        stored = prefs.get_prefs(stored_user)
        assert stored["daily_report_time"] == prefs.REPORT_OFF
        assert stored["water_reminders"] is True
        assert stored_user.proactive_messages_opt_out is False
        assert "мини-приложении" in result.reply_text

    def test_the_water_button_turns_off_only_water(self, bot_user: BotUser) -> None:
        result = ProactiveOptOutSkill().handle(ctx(bot_user, "cb:nutri:stop:water"))
        stored_user = BotUser.all_tenants.get(pk=bot_user.pk)
        stored = prefs.get_prefs(stored_user)
        assert stored["water_reminders"] is False
        assert stored["daily_report_time"] == "21:00"
        assert stored_user.proactive_messages_opt_out is False
        assert "мини-приложении" in result.reply_text

    def test_an_unknown_surface_is_claimed_but_changes_nothing(self, bot_user: BotUser) -> None:
        """A stale button (a surface the schema no longer knows) still gets
        an honest answer -- silence would leave the tap looking broken."""
        reply = try_handle_surface_stop(text="cb:nutri:stop:hint", bot_user=bot_user)
        assert reply is not None
        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=bot_user.pk))
        assert stored["daily_report_time"] == "21:00"
        assert stored["water_reminders"] is True

    def test_the_button_callbacks_match(self, bot_user: BotUser) -> None:
        skill = ProactiveOptOutSkill()
        assert skill.matches(ctx(bot_user, "cb:nutri:stop:report")) is True
        assert skill.matches(ctx(bot_user, "cb:nutri:stop:water")) is True
        assert skill.matches(ctx(bot_user, "cb:nutri:stop:hint")) is True

    def test_lookalikes_do_not_match(self, bot_user: BotUser) -> None:
        """``cb:nutri:stop`` without a surface is not a button we drew, and
        other callback families keep their owners."""
        skill = ProactiveOptOutSkill()
        assert skill.matches(ctx(bot_user, "cb:nutri:stop")) is False
        assert skill.matches(ctx(bot_user, "cb:food:diary")) is False
        assert matches_opt_out("cb:nutri:stop:water") is False

    def test_it_falls_through_on_anything_else(self, bot_user: BotUser) -> None:
        assert try_handle_surface_stop(text="привет", bot_user=bot_user) is None
        assert try_handle_surface_stop(text="cb:nutri:stop", bot_user=bot_user) is None
        assert try_handle_surface_stop(text="cb:food:diary", bot_user=bot_user) is None
        assert BotUser.all_tenants.get(pk=bot_user.pk).proactive_messages_opt_out is False

    def test_it_never_raises(self) -> None:
        """A failure must cost a log line, not the person's turn."""

        class Exploding:
            pk = "nope"

            @property
            def context(self):
                raise RuntimeError("boom")

        assert try_handle_surface_stop(text="cb:nutri:stop:water", bot_user=Exploding()) is None

    def test_both_entry_points_share_one_implementation(self, bot_user: BotUser) -> None:
        skill_reply = ProactiveOptOutSkill().handle(ctx(bot_user, "cb:nutri:stop:water"))
        BotUser.all_tenants.filter(pk=bot_user.pk).update(
            context={prefs.CONTEXT_KEY: {"water_reminders": True, "daily_report_time": "21:00"}}
        )
        fresh = BotUser.all_tenants.get(pk=bot_user.pk)
        global_reply = try_handle_surface_stop(text="cb:nutri:stop:water", bot_user=fresh)
        assert skill_reply.reply_text == global_reply

    def test_repeating_the_tap_is_harmless(self, bot_user: BotUser) -> None:
        skill = ProactiveOptOutSkill()
        skill.handle(ctx(bot_user, "cb:nutri:stop:water"))
        skill.handle(ctx(bot_user, "cb:nutri:stop:water"))
        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=bot_user.pk))
        assert stored["water_reminders"] is False

    def test_other_context_keys_survive(self, bot_user: BotUser) -> None:
        BotUser.all_tenants.filter(pk=bot_user.pk).update(
            context={"last_followup_sent_at": "2026-08-22", prefs.CONTEXT_KEY: {}}
        )
        fresh = BotUser.all_tenants.get(pk=bot_user.pk)
        ProactiveOptOutSkill().handle(ctx(fresh, "cb:nutri:stop:report"))
        stored = BotUser.all_tenants.get(pk=bot_user.pk)
        assert stored.context["last_followup_sent_at"] == "2026-08-22"
