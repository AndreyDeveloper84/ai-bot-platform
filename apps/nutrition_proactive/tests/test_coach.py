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
from zoneinfo import ZoneInfo

import pytest

from apps.consent.models import ConsentRecord
from apps.identity.models import BotUser
from apps.integrations.ayla import ProfileResponse
from apps.nutrition_coach import copy as coach_copy
from apps.nutrition_coach.goals import Goal
from apps.nutrition_coach.history import DayPicture, WeekPicture, WeekStatus
from apps.nutrition_proactive import coach, prefs
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


def coach_user(tenant: Tenant, **kwargs) -> BotUser:
    """A recipient who clears every gate the planner asks about."""
    user = make_user(tenant, **kwargs)
    grant_health(user)
    return user


def clean_profile(**overrides) -> ProfileResponse:
    payload = dict(
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
