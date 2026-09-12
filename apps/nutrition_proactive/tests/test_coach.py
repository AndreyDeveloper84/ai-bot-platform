"""The coach_hint planner — every gate, every slug (DRF-1464, T5).

Mirrors ``test_tasks.py``: one recipient who clears every gate, then one
test per reason slug that changes exactly one thing, so a failure names
the condition that broke. The cases the ticket calls mandatory:

* ``no_goal`` — без цели тишина даже при идеальном паттерне (R7);
* ``sensitive_perimeter`` — беременность/РПП/bmi_floor гасят ВСЮ
  поверхность, а недоступный профиль читается как «не знаем» = молчим;
* ``weekly_cap_surface`` — вторая подсказка в те же 7 дней не уходит;
* ``surface_auto_paused`` — две подсказки без ответа = тихая пауза;
* safety-hit — не отправляется и НЕ журналирует отправку.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone as dj_timezone

from apps.consent.models import ConsentRecord
from apps.identity.models import BotUser
from apps.integrations.ayla import ProfileResponse
from apps.nutrition_coach import copy as coach_copy
from apps.nutrition_coach.goals import Goal
from apps.nutrition_coach.history import DayPicture, WeekPicture, WeekStatus
from apps.nutrition_proactive import coach, prefs, tasks
from apps.nutrition_proactive.tests.test_antinag import outbox_entry, user_reply
from apps.nutrition_proactive.tests.test_tasks import NOON, at_msk, make_user, only
from apps.orchestrator.food_history import Meal, Status, TodayDiary
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

MSK = ZoneInfo("Europe/Moscow")
ELEVEN_PM = at_msk(23)

SLEEP_GOAL = Goal(key="sleep_better", text=None)


@pytest.fixture(autouse=True)
def coach_enabled(settings) -> None:
    """The planner's first gate is the master flag; every test here
    exercises what is BEHIND it, so it is open by default and closed
    explicitly in the one test that is about it."""
    settings.NUTRITION_COACH_ENABLED = True


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="np-coach", name="Salon", timezone="Europe/Moscow")


def grant_health(bot_user: BotUser) -> ConsentRecord:
    """The special-category basis the hint reads the diary on (152-ФЗ
    ст. 10): PERSONAL_DATA alone is not enough for this surface."""
    return ConsentRecord.all_tenants.create(
        tenant=bot_user.tenant,
        bot_user=bot_user,
        consent_type=ConsentRecord.ConsentType.HEALTH.value,
        granted=True,
        source="test:fixture",
    )


def grant_marketing(bot_user: BotUser) -> ConsentRecord:
    """The advertising consent (38-ФЗ ст. 18): a coach hint is PROMO class
    (DRF-1731) — a tip nobody asked for that morning."""
    return ConsentRecord.all_tenants.create(
        tenant=bot_user.tenant,
        bot_user=bot_user,
        consent_type=ConsentRecord.ConsentType.MARKETING.value,
        granted=True,
        source="test:fixture",
    )


def coach_user(tenant: Tenant, **kwargs) -> BotUser:
    """A recipient who clears every gate the planner asks about."""
    user = make_user(tenant, **kwargs)
    grant_health(user)
    grant_marketing(user)
    return user


def clean_profile(**overrides) -> ProfileResponse:
    payload: dict[str, Any] = dict(
        gender="female",
        age=31,
        height_cm=168,
        weight_kg=62,
        goal="maintain",
        daily_kcal=1994,
        protein_g=128,
        fat_g=66,
        carbs_g=221,
        water_ml=2400,
        bmr=1400,
        health_flags={},
        disclaimer_acked=None,
        goal_overridden_by=None,
        raw={},
    )
    payload.update(overrides)
    return ProfileResponse(**payload)


def dinner_day(offset: int) -> DayPicture:
    dinner = Meal(
        dish="Ужин",
        calories=400,
        meal_type="dinner",
        logged_at="2026-09-04T18:30:00Z",  # 21:30 MSK
    )
    return DayPicture(
        date=(date.today() - timedelta(days=offset)).isoformat(),
        diary=TodayDiary(Status.OK, (dinner,)),
    )


def late_dinner_week(count: int = 3) -> WeekPicture:
    return WeekPicture(
        status=WeekStatus.OK,
        days=tuple(dinner_day(i) for i in range(count)),
    )


def goal_reader(goal: Goal | None):
    return lambda _bot_user: goal


def week_reader(week: WeekPicture):
    return lambda _bot_user: week


def profile_reader(profile: ProfileResponse | None):
    return lambda _bot_user: profile


def plan(tenant_user: BotUser, **kwargs) -> list:
    defaults = dict(
        now_utc=NOON,
        fetch_goal=goal_reader(SLEEP_GOAL),
        fetch_history=week_reader(late_dinner_week()),
        fetch_profile=profile_reader(clean_profile()),
    )
    defaults.update(kwargs)
    return coach.plan_coach_hints(**defaults)


# ---------------------------------------------------------------------------
# The flags gate
# ---------------------------------------------------------------------------


class TestFlagsGate:
    def test_disabled_means_no_candidates_at_all(self, tenant: Tenant, settings) -> None:
        coach_user(tenant)
        # Контроль присутствия: при открытом флаге тот же план не пуст —
        # иначе «пусто при выключенном» доказывало бы не флаг, а сломанный
        # планировщик.
        assert coach.plan_coach_hints(now_utc=NOON) != []
        settings.NUTRITION_COACH_ENABLED = False
        assert coach.plan_coach_hints(now_utc=NOON) == []


# ---------------------------------------------------------------------------
# One slug per gate, in gate order
# ---------------------------------------------------------------------------


class TestCommonGatePassesThrough:
    def test_never_consented_is_blocked_by_the_shared_gate(self, tenant: Tenant) -> None:
        user = make_user(tenant, consented=False)
        decisions = plan(user)
        assert only(decisions, user).reason == "no_consent"

    def test_opted_out_never_reaches_the_planner(self, tenant: Tenant) -> None:
        user = make_user(tenant, opt_out=True)
        grant_health(user)
        assert all(d.bot_user_id != user.pk for d in plan(user))


class TestHealthBasis:
    def test_no_health_consent_blocks_a_fully_common_consenting_person(
        self, tenant: Tenant
    ) -> None:
        """PERSONAL_DATA + the shared gate pass; HEALTH is the basis the
        diary is read on, and without it the surface is silent."""
        user = make_user(tenant)  # personal-data record only, no health
        decisions = plan(user)
        assert only(decisions, user).reason == "no_health_consent"

    def test_a_throwing_consent_read_fails_closed(self, tenant: Tenant, monkeypatch) -> None:
        user = coach_user(tenant)

        from apps.consent.services import has_global_consent as real

        def boom(bot_user, consent_type, **kwargs):
            if consent_type == ConsentRecord.ConsentType.HEALTH.value:
                raise RuntimeError("db down")
            return real(bot_user, consent_type, **kwargs)

        monkeypatch.setattr("apps.consent.services.has_global_consent", boom)
        decisions = plan(user)
        assert only(decisions, user).reason == "no_health_consent"


class TestMarketingBasis:
    """DRF-1731: PROMO class — the advertising consent, by record, after
    the health basis and before the subscription pref."""

    def test_no_marketing_consent_blocks_a_health_consenting_person(self, tenant: Tenant) -> None:
        user = make_user(tenant)
        grant_health(user)  # baseline + special category, no advertising consent
        assert only(plan(user), user).reason == "no_marketing_consent"

    def test_withdrawn_marketing_consent_blocks_the_same_tick(self, tenant: Tenant) -> None:
        user = coach_user(tenant)
        # POSITIVE control first: with the toggle on, the planner gets past
        # every consent question (whatever it decides later is not consent).
        assert only(plan(user), user).reason not in (
            "no_marketing_consent",
            "no_health_consent",
            "consent_withdrawn",
        )
        ConsentRecord.all_tenants.filter(
            bot_user=user, consent_type=ConsentRecord.ConsentType.MARKETING.value
        ).update(withdrawn_at=dj_timezone.now())
        decision = only(plan(user), user)
        assert decision.send is False
        assert decision.reason == "no_marketing_consent"

    def test_the_slug_is_in_the_surface_vocabulary(self) -> None:
        assert "no_marketing_consent" in coach.BLOCK_REASONS


class TestSubscriptionPref:
    def test_hints_off_blocks_before_any_fetch(self, tenant: Tenant) -> None:
        """The «Не присылать» button flips this pref (DRF-1468 generic
        path); the planner honours it before spending a single Ayla call."""
        user = coach_user(tenant, extra_prefs={"coach_hints": False})
        decisions = plan(
            user,
            fetch_profile=lambda _u: pytest.fail("profile fetched for an opted-out person"),
        )
        assert only(decisions, user).reason == "hints_off"

    def test_the_pref_defaults_to_on(self, tenant: Tenant) -> None:
        """Owner decision Q-09: the subscription basis is the goal itself,
        so a person who never touched the pref is subscribed."""
        user = coach_user(tenant)
        decisions = plan(user)
        assert only(decisions, user).send is True


class TestSensitivePerimeter:
    """The whole surface goes quiet — not just the remark (owner decision:
    sensitive-периметр гасит ВСЮ поверхность)."""

    @pytest.mark.parametrize(
        "override", ["pregnancy", "breastfeeding", "eating_disorder", "bmi_floor"]
    )
    def test_every_sensitive_override_gates_the_hint(self, tenant: Tenant, override: str) -> None:
        user = coach_user(tenant)
        decisions = plan(
            user, fetch_profile=profile_reader(clean_profile(goal_overridden_by=override))
        )
        assert only(decisions, user).reason == "sensitive_perimeter"

    def test_an_eating_disorder_flag_gates_the_hint(self, tenant: Tenant) -> None:
        user = coach_user(tenant)
        profile = clean_profile(health_flags={"eating_disorder": True})
        decisions = plan(user, fetch_profile=profile_reader(profile))
        assert only(decisions, user).reason == "sensitive_perimeter"

    def test_an_unavailable_profile_is_silence_not_a_guess(self, tenant: Tenant) -> None:
        """«Не знаем» читается как sensitive: молчим, пока не доказано,
        что писать можно."""
        user = coach_user(tenant)
        decisions = plan(user, fetch_profile=profile_reader(None))
        decision = only(decisions, user)
        assert decision.send is False
        assert decision.reason == "sensitive_perimeter"


class TestExplicitGoal:
    def test_no_goal_means_silence_even_with_a_perfect_pattern(self, tenant: Tenant) -> None:
        """R7, the mandatory case: идеальная неделя поздних ужинов, но без
        выбранной цели подсказки не существует."""
        user = coach_user(tenant)
        decisions = plan(
            user,
            fetch_goal=goal_reader(None),
            fetch_history=lambda _u: pytest.fail("week read for a goal-less person"),
        )
        assert only(decisions, user).reason == "no_goal"


class TestQuietHours:
    def test_2300_is_silent_whatever_the_pattern(self, tenant: Tenant) -> None:
        user = coach_user(tenant)
        decisions = plan(user, now_utc=ELEVEN_PM)
        assert only(decisions, user).reason == "quiet_hours"


class TestWeeklyBudget:
    def test_a_second_hint_inside_seven_days_does_not_go(self, tenant: Tenant) -> None:
        """The mandatory weekly-cap case: coach_hint is an unlisted
        surface, so DRF-1468 hands it the default budget of one a week."""
        user = coach_user(
            tenant,
            extra_prefs={prefs.OUTBOX_KEY: [outbox_entry("coach_hint", days_ago=1)]},
        )
        decisions = plan(user)
        decision = only(decisions, user)
        assert decision.send is False
        assert decision.reason == "weekly_cap_surface"

    def test_the_cross_surface_total_caps_the_hint_too(self, tenant: Tenant) -> None:
        user = coach_user(
            tenant,
            extra_prefs={
                prefs.OUTBOX_KEY: [
                    outbox_entry("water", days_ago=1)
                    for _ in range(prefs.MAX_WEEKLY_OUTBOUND_TOTAL)
                ]
            },
        )
        decisions = plan(user)
        assert only(decisions, user).reason == "weekly_cap_total"


class TestAutoPause:
    def test_two_unanswered_hints_pause_the_surface_silently(self, tenant: Tenant) -> None:
        """Sends 8+ days old: outside the 7-day cap window, inside the
        14-day journal — the streak is the only mechanism that can bite."""
        user = coach_user(
            tenant,
            extra_prefs={
                prefs.OUTBOX_KEY: [outbox_entry("coach_hint", days_ago=8) for _ in range(2)]
            },
        )
        decisions = plan(user)
        decision = only(decisions, user)
        assert decision.send is False
        assert decision.reason == "surface_auto_paused"
        # The pause is a pref flip, never a message (R2/R6).
        assert decision.text == ""
        assert decision.pref_updates == {"coach_hints": False}

    def test_a_reply_after_the_sends_resets_the_streak(self, tenant: Tenant) -> None:
        user = coach_user(
            tenant,
            extra_prefs={
                prefs.OUTBOX_KEY: [outbox_entry("coach_hint", days_ago=8) for _ in range(2)]
            },
        )
        user_reply(user, at=NOON - timedelta(days=1))
        decisions = plan(user)
        assert only(decisions, user).send is True


class TestTrigger:
    def test_an_unrelated_goal_fires_nothing(self, tenant: Tenant) -> None:
        user = coach_user(tenant)
        decisions = plan(user, fetch_goal=goal_reader(Goal(key="relax", text=None)))
        assert only(decisions, user).reason == "no_trigger"

    def test_a_week_with_a_hole_is_not_a_pattern(self, tenant: Tenant) -> None:
        """UNAVAILABLE ≠ пустая неделя: no trigger off partial data."""
        user = coach_user(tenant)
        hole = WeekPicture(status=WeekStatus.UNAVAILABLE, days=(dinner_day(0),))
        decisions = plan(user, fetch_history=week_reader(hole))
        decision = only(decisions, user)
        assert decision.reason == "no_trigger"
        assert decision.detail["week_status"] == "unavailable"

    def test_a_consent_refused_week_is_not_a_pattern(self, tenant: Tenant) -> None:
        user = coach_user(tenant)
        refused = WeekPicture(status=WeekStatus.NO_CONSENT)
        decisions = plan(user, fetch_history=week_reader(refused))
        assert only(decisions, user).reason == "no_trigger"


class TestDue:
    def test_the_full_path_sends(self, tenant: Tenant) -> None:
        user = coach_user(tenant)
        decisions = plan(user)
        decision = only(decisions, user)
        assert decision.send is True
        assert decision.reason == "due"
        assert decision.detail["trigger"] == "late_dinner"
        assert decision.text == coach_copy.render_hint("late_dinner", first_ever=True)

    def test_the_first_ever_hint_carries_the_tail(self, tenant: Tenant) -> None:
        user = coach_user(tenant)
        decision = only(plan(user), user)
        assert coach_copy.FIRST_HINT_TAIL in decision.text
        assert decision.detail["first_ever"] is True

    def test_a_repeat_hint_drops_the_tail(self, tenant: Tenant) -> None:
        """A send 10 days back: outside the 7-day budget window, inside
        the 14-day journal, and answered since — so the only difference
        from «first ever» is that it is not first."""
        user = coach_user(
            tenant,
            extra_prefs={prefs.OUTBOX_KEY: [outbox_entry("coach_hint", days_ago=10)]},
        )
        user_reply(user, at=NOON - timedelta(days=9))
        decision = only(plan(user), user)
        assert decision.send is True
        assert coach_copy.FIRST_HINT_TAIL not in decision.text
        assert decision.detail["first_ever"] is False

    def test_a_solicited_observation_is_not_a_hint(self, tenant: Tenant) -> None:
        """DRF-1464 T6 (Q-NUTRITION-05): the diary observation is journaled on
        this surface with the solicited marker — it must not spend the hint's
        weekly budget, must not build the ignore streak, and must not strip
        the FIRST hint of its cadence tail. The same entry unmarked blocks
        all three ways (the weekly-cap test above), so «due» here is the
        marker working, not a broken gate."""
        user = coach_user(
            tenant,
            extra_prefs={
                prefs.OUTBOX_KEY: [{**outbox_entry("coach_hint", days_ago=1), "solicited": True}]
            },
        )
        decision = only(plan(user), user)
        assert decision.send is True
        assert decision.reason == "due"
        assert decision.detail["first_ever"] is True
        assert coach_copy.FIRST_HINT_TAIL in decision.text

    def test_the_text_passes_the_outbound_guard(self, tenant: Tenant) -> None:
        from apps.orchestrator.safety.outbound import evaluate_outbound

        coach_user(tenant)
        for decision in plan(coach_user(tenant, suffix="second")):
            if decision.send:
                assert evaluate_outbound(decision.text).allowed


class TestSafetyHit:
    def test_a_blocked_text_sends_nothing_and_journals_nothing(self, tenant: Tenant) -> None:
        """The whitelist makes this unreachable with shipped copy; the test
        pins the PATH: a guard hit is silence, no idempotency bump, no
        journal entry — the next tick evaluates the person fresh."""
        user = coach_user(tenant)
        from unittest.mock import patch

        with patch.object(
            coach_copy, "render_hint", return_value="Ты держишь серию — 7 дней подряд!"
        ):
            decisions = plan(user)
        decision = only(decisions, user)
        assert decision.send is False
        assert decision.reason == "outbound_safety_nag"
        assert decision.pref_updates == {}


class TestReasonVocabulary:
    def test_every_emitted_slug_is_declared(self, tenant: Tenant) -> None:
        """The dry run and the tests assert against a stable vocabulary:
        a slug the planner can emit must be listed in BLOCK_REASONS."""
        coach_user(tenant)
        decisions = plan(coach_user(tenant, suffix="b"))
        for decision in decisions:
            assert decision.reason in coach.BLOCK_REASONS


# ---------------------------------------------------------------------------
# The beat task around the planner
# ---------------------------------------------------------------------------


def patch_planner_reads():
    """Deterministic reads for task-level tests: goal, week, profile.

    The task calls the planner with no fetches, so the defaults are
    patched at their modules — this also pins that the task really does
    go through the default read path, not a test-only seam.
    """
    from unittest.mock import patch

    return (
        patch(
            "apps.nutrition_coach.goals.active_goal",
            side_effect=goal_reader(SLEEP_GOAL),
        ),
        patch(
            "apps.nutrition_coach.history.week_picture",
            side_effect=week_reader(late_dinner_week()),
        ),
        patch(
            "apps.nutrition_proactive.coach._fetch_profile",
            side_effect=profile_reader(clean_profile()),
        ),
    )


class TestCoachHintTask:
    def test_disabled_task_touches_nothing(self, tenant: Tenant, settings) -> None:
        from unittest.mock import patch

        coach_user(tenant)
        settings.NUTRITION_COACH_ENABLED = False
        with patch("apps.nutrition_proactive.tasks.send_message") as send:
            result = tasks.send_coach_hints()
        send.assert_not_called()
        assert result["sent"] == 0

    def test_enabled_but_dry_run_sends_nothing(self, tenant: Tenant, settings) -> None:
        from unittest.mock import patch

        coach_user(tenant)
        settings.NUTRITION_COACH_ENABLED = True
        settings.NUTRITION_COACH_DRY_RUN = True
        goal_patch, week_patch, profile_patch = patch_planner_reads()
        with (
            patch("apps.nutrition_proactive.tasks.send_message") as send,
            patch("apps.nutrition_proactive.tasks.dj_timezone.now", return_value=NOON),
            goal_patch,
            week_patch,
            profile_patch,
        ):
            result = tasks.send_coach_hints()
        send.assert_not_called()
        assert result["would_send"] == 1
        assert result["sent"] == 0
        assert result["dry_run"] == 1

    def test_armed_task_sends_and_journals_the_surface(self, tenant: Tenant, settings) -> None:
        from unittest.mock import patch

        user = coach_user(tenant)
        settings.NUTRITION_COACH_ENABLED = True
        settings.NUTRITION_COACH_DRY_RUN = False
        goal_patch, week_patch, profile_patch = patch_planner_reads()
        with (
            patch("apps.nutrition_proactive.tasks.send_message") as send,
            patch("apps.nutrition_proactive.tasks.dj_timezone.now", return_value=NOON),
            goal_patch,
            week_patch,
            profile_patch,
        ):
            result = tasks.send_coach_hints()
        assert result["sent"] == 1
        send.assert_called_once()

        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=user.pk))
        assert prefs.outbox_entries(stored) == [
            {"surface": "coach_hint", "sent_at": NOON.isoformat()}
        ]

    def test_every_hint_send_carries_the_one_tap_unsubscribe(
        self, tenant: Tenant, settings
    ) -> None:
        from unittest.mock import patch

        from apps.nutrition_proactive.tests.test_antinag import button_payloads

        coach_user(tenant)
        settings.NUTRITION_COACH_ENABLED = True
        settings.NUTRITION_COACH_DRY_RUN = False
        goal_patch, week_patch, profile_patch = patch_planner_reads()
        with (
            patch("apps.nutrition_proactive.tasks.send_message") as send,
            patch("apps.nutrition_proactive.tasks.dj_timezone.now", return_value=NOON),
            goal_patch,
            week_patch,
            profile_patch,
        ):
            tasks.send_coach_hints()
        attachments = send.call_args.kwargs["attachments"]
        assert button_payloads(attachments) == ["cb:nutri:stop:coach_hint"]

    def test_a_safety_hit_is_not_journaled(self, tenant: Tenant, settings) -> None:
        """Mandatory case: a guard hit is silence — no send, no journal,
        no spent budget."""
        from unittest.mock import patch

        user = coach_user(tenant)
        settings.NUTRITION_COACH_ENABLED = True
        settings.NUTRITION_COACH_DRY_RUN = False
        goal_patch, week_patch, profile_patch = patch_planner_reads()
        with (
            patch("apps.nutrition_proactive.tasks.send_message") as send,
            patch("apps.nutrition_proactive.tasks.dj_timezone.now", return_value=NOON),
            patch.object(
                coach_copy, "render_hint", return_value="Ты держишь серию — 7 дней подряд!"
            ),
            goal_patch,
            week_patch,
            profile_patch,
        ):
            result = tasks.send_coach_hints()
        assert result["sent"] == 0
        send.assert_not_called()
        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=user.pk))
        # Смысл кейса — пустой журнал после safety-hit; присутствие записи
        # при штатной отправке прибито test_armed_task_sends_and_journals.
        assert prefs.outbox_entries(stored) == []  # empty-assert-ok: пустота и есть проверка

    def test_the_auto_pause_lands_even_in_dry_run(self, tenant: Tenant, settings) -> None:
        """Suppression is persisted though nothing is sent — same contract
        as the report surface (``_run_task``)."""
        from unittest.mock import patch

        user = coach_user(
            tenant,
            extra_prefs={
                prefs.OUTBOX_KEY: [outbox_entry("coach_hint", days_ago=8) for _ in range(2)]
            },
        )
        settings.NUTRITION_COACH_ENABLED = True
        settings.NUTRITION_COACH_DRY_RUN = True
        goal_patch, week_patch, profile_patch = patch_planner_reads()
        with (
            patch("apps.nutrition_proactive.tasks.send_message") as send,
            patch("apps.nutrition_proactive.tasks.dj_timezone.now", return_value=NOON),
            goal_patch,
            week_patch,
            profile_patch,
        ):
            tasks.send_coach_hints()
        send.assert_not_called()
        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=user.pk))
        assert stored["coach_hints"] is False


class TestBeatRegistration:
    def test_the_task_is_registered_and_on_the_schedule(self) -> None:
        """A daily tick, shipped ahead of the flags: no-op until the
        operator opens them, same contract as the sibling beats."""
        from celery.schedules import crontab  # type: ignore[import-untyped]
        from django.conf import settings

        entry = settings.CELERY_BEAT_SCHEDULE["nutrition_proactive.send_coach_hints"]
        assert entry["task"] == "nutrition_proactive.send_coach_hints"
        assert isinstance(entry["schedule"], crontab)
        assert tasks.send_coach_hints.name == "nutrition_proactive.send_coach_hints"


# ---------------------------------------------------------------------------
# The one-tap unsubscribe, generic path (DRF-1468) — no handler.py edits
# ---------------------------------------------------------------------------


class TestStopButtonCoversCoachHint:
    def test_the_surface_has_an_opt_out_pref_and_a_confirmation(self) -> None:
        from apps.nutrition_proactive import optout

        assert optout.SURFACE_OPT_OUT_PREFS["coach_hint"] == {"coach_hints": False}
        assert optout.SURFACE_CONFIRMATIONS["coach_hint"]
        # The confirmation goes out as a chat reply: no guilt, the way
        # back named — and clean under the outbound guard.
        from apps.orchestrator.safety.outbound import evaluate_outbound

        assert evaluate_outbound(optout.SURFACE_CONFIRMATIONS["coach_hint"]).allowed

    def test_the_tap_flips_only_the_coach_pref(self, tenant: Tenant) -> None:
        """One surface, not the platform-wide veto: the tap under a hint
        answers the hint, not every future message."""
        from apps.nutrition_proactive import optout

        user = coach_user(tenant)
        reply = optout.try_handle_surface_stop(text="cb:nutri:stop:coach_hint", bot_user=user)
        assert reply == optout.SURFACE_CONFIRMATIONS["coach_hint"]

        user.refresh_from_db()
        stored = prefs.get_prefs(user)
        assert stored["coach_hints"] is False
        assert user.proactive_messages_opt_out is False

    def test_the_registry_skill_claims_the_tap(self) -> None:
        """Per-tenant surface: ProactiveOptOutSkill.matches sees the
        payload through the generic parse, no per-surface code."""
        from types import SimpleNamespace
        from typing import cast

        from apps.nutrition_proactive.optout_skill import ProactiveOptOutSkill
        from apps.skills.base import SkillContext

        context = cast(SkillContext, SimpleNamespace(message_text="cb:nutri:stop:coach_hint"))
        assert ProactiveOptOutSkill().matches(context) is True

    def test_the_history_resolver_treats_the_tap_as_no_words(self) -> None:
        """The tap is not a phrase (DRF-990 shape): the generic resolver
        already covers the surface, nothing to add handler-side."""
        from apps.orchestrator.nutrition_global import resolve_nutri_stop_tap

        tap = resolve_nutri_stop_tap("cb:nutri:stop:coach_hint")
        assert tap is not None
        assert tap.history_text is None


# ---------------------------------------------------------------------------
# The operator-facing dry run
# ---------------------------------------------------------------------------


class TestDryRunCommand:
    """``nutrition_coach_dryrun`` runs the planner, not a parallel
    selection — the same contract ``TestDryRunCommandShowsTheGate`` pins
    for the report/water surfaces."""

    def _run(self, *args: str) -> str:
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command(
            "nutrition_coach_dryrun",
            "--at",
            NOON.astimezone(MSK).isoformat(),
            "--no-ayla",
            *args,
            stdout=out,
        )
        return out.getvalue()

    @staticmethod
    def _rows(report: str) -> dict[str, dict]:
        import json

        rows: dict[str, dict] = {}
        for line in report.splitlines():
            line = line.strip()
            if line.startswith("{"):
                row = json.loads(line)
                rows[row["bot_user_id"]] = row
        return rows

    def test_the_recipient_and_the_blocked_shapes(self, tenant: Tenant) -> None:
        recipient = coach_user(tenant, suffix="ok")

        no_health = make_user(tenant, suffix="nohealth")  # personal-data only

        opted_out_pref = coach_user(tenant, suffix="pref", extra_prefs={"coach_hints": False})

        rows = self._rows(self._run())
        assert rows[str(recipient.pk)]["send"] is True
        assert rows[str(recipient.pk)]["reason"] == "due"
        assert rows[str(no_health.pk)]["reason"] == "no_health_consent"
        assert rows[str(opted_out_pref.pk)]["reason"] == "hints_off"

    def test_no_message_text_reaches_the_report(self, tenant: Tenant) -> None:
        coach_user(tenant, suffix="ok")
        rows = self._rows(self._run())
        assert rows, "dry-run показал ноль строк — проверять нечего"
        # empty-assert-ok: свойство — отсутствие текста в выводе;
        # присутствие строк прибито ассертом выше на тех же данных.
        assert all("text" not in row for row in rows.values())

    def test_the_flags_are_printed_and_still_closed(self, tenant: Tenant, settings) -> None:
        """The report says out loud that nothing can actually be sent.

        The autouse fixture holds the flag OPEN for the planner tests, so
        the default-closed state is restored here explicitly."""
        settings.NUTRITION_COACH_ENABLED = False
        settings.NUTRITION_COACH_DRY_RUN = True
        report = self._run()
        assert "NUTRITION_COACH_ENABLED=False" in report
        assert "NUTRITION_COACH_DRY_RUN=True" in report
