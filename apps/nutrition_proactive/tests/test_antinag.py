"""The shared anti-nag mechanism (DRF-1468).

Policy R2/R6 (``docs/design/policies/nutrition-coach-copy-policy.md``) says
the bot goes quiet on its own: a person who never answers is not reminded
that they never answer, and no surface may spend the whole week's budget of
unsolicited messages. This suite pins the mechanics:

* every proactive send is journaled (``TestOutboxJournal``);
* a sliding 7-day ceiling spans surfaces (``TestWeeklyCeiling``);
* two unanswered sends pause a surface silently (``TestSurfaceIgnoreStreak``).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from apps.identity.models import BotUser
from apps.nutrition_proactive import prefs, tasks
from apps.nutrition_proactive.tests.test_tasks import (
    NOON,
    make_user,
    only,
    summary_reader,
    water_reader,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="np-antinag", name="Salon", timezone="Europe/Moscow")


def outbox_entry(surface: str, *, days_ago: float = 1, at: datetime = NOON) -> dict[str, str]:
    return {
        "surface": surface,
        "sent_at": (at - timedelta(days=days_ago)).isoformat(),
    }


def outbox_prefs(surface: str, count: int, *, days_ago: float = 1) -> dict[str, list]:
    return {prefs.OUTBOX_KEY: [outbox_entry(surface, days_ago=days_ago) for _ in range(count)]}


# ---------------------------------------------------------------------------
# 1 — the shared outbound journal
# ---------------------------------------------------------------------------


class TestOutboxJournal:
    def test_append_outbox_appends_an_entry(self) -> None:
        updated = prefs.append_outbox({}, surface="report", sent_at=NOON)
        entries = prefs.outbox_entries(updated)
        assert entries == [{"surface": "report", "sent_at": NOON.isoformat()}]

    def test_append_outbox_prunes_entries_older_than_the_keep_window(self) -> None:
        stale = outbox_entry("water", days_ago=prefs.OUTBOX_KEEP_DAYS + 1)
        fresh = outbox_entry("water", days_ago=1)
        updated = prefs.append_outbox(
            {prefs.OUTBOX_KEY: [stale, fresh]}, surface="report", sent_at=NOON
        )
        entries = prefs.outbox_entries(updated)
        assert len(entries) == 2
        assert stale not in entries

    def test_append_outbox_caps_the_list(self) -> None:
        full = [outbox_entry("water") for _ in range(prefs.OUTBOX_CAP)]
        updated = prefs.append_outbox({prefs.OUTBOX_KEY: full}, surface="report", sent_at=NOON)
        entries = prefs.outbox_entries(updated)
        assert len(entries) == prefs.OUTBOX_CAP
        assert entries[-1]["surface"] == "report"

    def test_append_outbox_tolerates_a_corrupt_journal(self) -> None:
        """A hand-edited context must not crash the send path."""
        updated = prefs.append_outbox(
            {prefs.OUTBOX_KEY: "not-a-list"}, surface="water", sent_at=NOON
        )
        assert prefs.outbox_entries(updated) == [{"surface": "water", "sent_at": NOON.isoformat()}]

    def test_a_successful_send_is_journaled(self, tenant: Tenant, settings) -> None:
        user = make_user(tenant)
        settings.NUTRITION_PROACTIVE_ENABLED = True
        settings.NUTRITION_PROACTIVE_DRY_RUN = False
        with (
            patch("apps.nutrition_proactive.tasks.send_message"),
            patch("apps.nutrition_proactive.tasks._fetch_water", side_effect=water_reader(0)),
            patch("apps.nutrition_proactive.tasks.dj_timezone.now", return_value=NOON),
        ):
            result = tasks.send_water_reminders()
        assert result["sent"] == 1

        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=user.pk))
        assert stored["water"]["sent"] == 1
        assert prefs.outbox_entries(stored) == [{"surface": "water", "sent_at": NOON.isoformat()}]

    def test_a_failed_send_is_not_journaled(self, tenant: Tenant, settings) -> None:
        from apps.channels.max.outbound import MaxAPIError

        user = make_user(tenant)
        settings.NUTRITION_PROACTIVE_ENABLED = True
        settings.NUTRITION_PROACTIVE_DRY_RUN = False
        with (
            patch(
                "apps.nutrition_proactive.tasks.send_message",
                side_effect=MaxAPIError(502, "bad gateway"),
            ),
            patch("apps.nutrition_proactive.tasks._fetch_water", side_effect=water_reader(0)),
            patch("apps.nutrition_proactive.tasks.dj_timezone.now", return_value=NOON),
        ):
            result = tasks.send_water_reminders()
        assert result["failed"] == 1

        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=user.pk))
        assert stored["water_reminders"]
        assert prefs.outbox_entries(stored) == []

    def test_a_dry_run_journals_nothing(self, tenant: Tenant, settings) -> None:
        user = make_user(tenant)
        settings.NUTRITION_PROACTIVE_ENABLED = True
        settings.NUTRITION_PROACTIVE_DRY_RUN = True
        with (
            patch("apps.nutrition_proactive.tasks.send_message"),
            patch("apps.nutrition_proactive.tasks._fetch_water", side_effect=water_reader(0)),
            patch("apps.nutrition_proactive.tasks.dj_timezone.now", return_value=NOON),
        ):
            result = tasks.send_water_reminders()
        assert result["would_send"] == 1

        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=user.pk))
        assert stored["water_reminders"]
        assert prefs.outbox_entries(stored) == []


# ---------------------------------------------------------------------------
# 2 — the sliding 7-day ceiling, across surfaces
# ---------------------------------------------------------------------------


class TestWeeklyCeiling:
    def test_report_is_blocked_when_the_cross_surface_total_is_reached(
        self, tenant: Tenant
    ) -> None:
        capped = make_user(
            tenant,
            suffix="capped",
            report="12:00",
            extra_prefs=outbox_prefs("water", prefs.MAX_WEEKLY_OUTBOUND_TOTAL),
        )
        neighbour = make_user(tenant, suffix="neighbour", report="12:00")

        decisions = tasks.plan_daily_reports(now_utc=NOON, fetch=summary_reader())
        decision = only(decisions, capped)
        assert decision.send is False
        assert decision.reason == "weekly_cap_total"
        # The ceiling is per person, not a batch-wide abort.
        assert only(decisions, neighbour).send is True

    def test_report_is_blocked_by_its_own_weekly_surface_cap(self, tenant: Tenant) -> None:
        user = make_user(
            tenant,
            report="12:00",
            extra_prefs=outbox_prefs("report", prefs.WEEKLY_SURFACE_CAPS["report"], days_ago=1),
        )
        # A reply after those sends resets the ignore streak, so the ONLY
        # mechanism left that can bite here is the weekly ceiling.
        user_reply(user, at=NOON - timedelta(hours=1))

        decisions = tasks.plan_daily_reports(now_utc=NOON, fetch=summary_reader())
        decision = only(decisions, user)
        assert decision.send is False
        assert decision.reason == "weekly_cap_surface"

    def test_entries_older_than_seven_days_do_not_count(self, tenant: Tenant) -> None:
        user = make_user(
            tenant,
            report="12:00",
            extra_prefs=outbox_prefs("water", prefs.MAX_WEEKLY_OUTBOUND_TOTAL, days_ago=8),
        )
        decisions = tasks.plan_daily_reports(now_utc=NOON, fetch=summary_reader())
        assert only(decisions, user).send is True

    def test_water_is_blocked_by_the_weekly_ceiling_too(self, tenant: Tenant) -> None:
        capped = make_user(
            tenant,
            suffix="capped",
            extra_prefs=outbox_prefs("report", prefs.MAX_WEEKLY_OUTBOUND_TOTAL),
        )
        neighbour = make_user(tenant, suffix="neighbour")

        decisions = tasks.plan_water_reminders(now_utc=NOON, fetch=water_reader(0))
        decision = only(decisions, capped)
        assert decision.send is False
        assert decision.reason == "weekly_cap_total"
        assert only(decisions, neighbour).send is True

    def test_the_weekly_ceiling_is_not_the_daily_idempotency(self, tenant: Tenant) -> None:
        """The per-day key keeps its own reason slug: the two mechanisms must
        stay distinguishable in a dry run, not collapse into one "capped"."""
        user = make_user(
            tenant,
            report="12:00",
            extra_prefs={"last_report_date": "2026-08-23"},
        )
        decisions = tasks.plan_daily_reports(now_utc=NOON, fetch=summary_reader())
        assert only(decisions, user).reason == "already_sent_today"

    def test_future_surfaces_default_to_one_touch_a_week(self) -> None:
        """The hint and the weekly report ship with the strict default; the
        two live surfaces are pinned at what their per-day quotas allow."""
        assert prefs.weekly_cap_for("hint") == 1
        assert prefs.weekly_cap_for("weekly_report") == 1
        assert prefs.weekly_cap_for("report") == 7
        assert prefs.weekly_cap_for("water") == 21

    def test_weekly_sent_count_filters_by_surface(self) -> None:
        journal = {
            prefs.OUTBOX_KEY: [
                outbox_entry("report", days_ago=1),
                outbox_entry("water", days_ago=2),
                outbox_entry("water", days_ago=8),
            ]
        }
        assert prefs.weekly_sent_count(journal, now_utc=NOON) == 2
        assert prefs.weekly_sent_count(journal, now_utc=NOON, surface="water") == 1
        assert prefs.weekly_sent_count(journal, now_utc=NOON, surface="report") == 1


# ---------------------------------------------------------------------------
# 3 — the universal ignore streak: unanswered sends pause a surface, silently
# ---------------------------------------------------------------------------


def user_reply(bot_user: BotUser, *, at: datetime) -> None:
    """A ``Message(role=user)`` stamped ``at`` -- the only "was heeded"
    signal MAX gives us (it sends no read receipts)."""
    from apps.conversations.models import Conversation, Message

    conversation = Conversation.all_tenants.create(tenant=bot_user.tenant, bot_user=bot_user)
    message = Message.all_tenants.create(
        conversation=conversation,
        tenant=bot_user.tenant,
        role=Message.Role.USER,
        content="привет",
    )
    Message.all_tenants.filter(pk=message.pk).update(created_at=at)


class TestSurfaceIgnoreStreak:
    def test_two_unanswered_reports_pause_the_surface_silently(self, tenant: Tenant) -> None:
        user = make_user(
            tenant,
            report="12:00",
            extra_prefs=outbox_prefs("report", prefs.SURFACE_IGNORE_LIMIT),
        )
        decisions = tasks.plan_daily_reports(now_utc=NOON, fetch=summary_reader())
        decision = only(decisions, user)
        assert decision.send is False
        assert decision.reason == "surface_auto_paused"
        # The pause is a pref flip, never a message to the person (R2/R6).
        assert decision.text == ""
        assert decision.pref_updates["daily_report_time"] == prefs.REPORT_OFF

    def test_the_pause_is_persisted_without_sending_anything(
        self, tenant: Tenant, settings
    ) -> None:
        user = make_user(
            tenant,
            report="12:00",
            extra_prefs=outbox_prefs("report", prefs.SURFACE_IGNORE_LIMIT),
        )
        settings.NUTRITION_PROACTIVE_ENABLED = True
        settings.NUTRITION_PROACTIVE_DRY_RUN = True
        with (
            patch("apps.nutrition_proactive.tasks.send_message") as send,
            patch("apps.nutrition_proactive.tasks.dj_timezone.now", return_value=NOON),
        ):
            tasks.send_daily_reports()
        send.assert_not_called()

        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=user.pk))
        assert stored["daily_report_time"] == prefs.REPORT_OFF
        # Nothing was sent, so nothing new was journaled either.
        assert len(prefs.outbox_entries(stored)) == prefs.SURFACE_IGNORE_LIMIT

    def test_one_unanswered_send_does_not_pause(self, tenant: Tenant) -> None:
        user = make_user(tenant, report="12:00", extra_prefs=outbox_prefs("report", 1))
        decisions = tasks.plan_daily_reports(now_utc=NOON, fetch=summary_reader())
        assert only(decisions, user).send is True

    def test_a_reply_after_the_sends_resets_the_streak(self, tenant: Tenant) -> None:
        user = make_user(
            tenant,
            report="12:00",
            extra_prefs=outbox_prefs("report", prefs.SURFACE_IGNORE_LIMIT, days_ago=1),
        )
        user_reply(user, at=NOON - timedelta(hours=1))

        decisions = tasks.plan_daily_reports(now_utc=NOON, fetch=summary_reader())
        decision = only(decisions, user)
        assert decision.send is True
        assert decision.reason == "due"

    def test_a_reply_before_the_sends_does_not_reset(self, tenant: Tenant) -> None:
        user = make_user(
            tenant,
            report="12:00",
            extra_prefs=outbox_prefs("report", prefs.SURFACE_IGNORE_LIMIT, days_ago=1),
        )
        user_reply(user, at=NOON - timedelta(days=2))

        decisions = tasks.plan_daily_reports(now_utc=NOON, fetch=summary_reader())
        assert only(decisions, user).reason == "surface_auto_paused"

    def test_only_the_surfaces_own_sends_count(self, tenant: Tenant) -> None:
        """Ignored WATER sends must not pause the report, and vice versa."""
        user = make_user(
            tenant,
            report="12:00",
            extra_prefs=outbox_prefs("water", prefs.SURFACE_IGNORE_LIMIT),
        )
        decisions = tasks.plan_daily_reports(now_utc=NOON, fetch=summary_reader())
        assert only(decisions, user).send is True

    def test_water_keeps_its_domain_streak(self, tenant: Tenant) -> None:
        """The universal streak does NOT pause water: a person who drinks
        without replying is being heeded, and the intake comparison is the
        stronger signal. Water's own streak (intake, limit 3) is untouched."""
        user = make_user(tenant, extra_prefs=outbox_prefs("water", prefs.SURFACE_IGNORE_LIMIT))
        decisions = tasks.plan_water_reminders(now_utc=NOON, fetch=water_reader(50))
        decision = only(decisions, user)
        assert decision.send is True
        assert decision.reason == "behind_proportional_norm"
