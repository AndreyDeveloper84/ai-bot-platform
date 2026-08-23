"""Selection, quiet hours, thresholds and the two safety switches (DRF-1285).

The four cases the ticket names as mandatory are marked in the class
docstrings below:

* noon, behind the proportional norm  -> remind      (TestWaterThreshold)
* noon, on track                      -> stay quiet  (TestWaterThreshold)
* 23:00, under any circumstances      -> stay quiet  (TestQuietHours)
* ``proactive_messages_opt_out``      -> never       (TestOptOutIsAbsolute)
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from apps.consent.models import ConsentRecord
from apps.identity.models import BotUser
from apps.integrations.ayla import SummaryResponse, WaterTodayResponse
from apps.nutrition_proactive import prefs, selection, tasks
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

MSK = ZoneInfo("Europe/Moscow")


def at_msk(hour: int, minute: int = 0, day: int = 23) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=MSK).astimezone(dt_timezone.utc)


NOON = at_msk(12)
ELEVEN_PM = at_msk(23)


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="np-salon", name="Salon", timezone="Europe/Moscow")


def grant_consent(bot_user: BotUser) -> ConsentRecord:
    """Give ``bot_user`` the active 152-ФЗ record the gate asks for."""
    return ConsentRecord.all_tenants.create(
        tenant=bot_user.tenant,
        bot_user=bot_user,
        consent_type=ConsentRecord.ConsentType.PERSONAL_DATA.value,
        granted=True,
        source="test:fixture",
    )


def make_user(
    tenant: Tenant,
    *,
    suffix: str = "1",
    water: bool = True,
    report: str = prefs.REPORT_OFF,
    opt_out: bool = False,
    consented: bool = True,
    chat_id: str | None = None,
    extra_prefs: dict | None = None,
) -> BotUser:
    """A recipient that clears every gate unless a flag says otherwise.

    ``consented`` builds **both** halves of consent: the ``consent_at``
    stamp and the ``ConsentRecord`` that proves it. Before DRF-1314 the
    stamp alone was enough to pass this module's gate, which is the bug —
    a fixture that still set only the column would be describing a person
    the pilot has four of: stamped, and withdrawn. That the gate bites is
    proven in ``TestConsentGate`` against users built without a record,
    not by leaving this fixture half-built.
    """
    now = datetime(2026, 5, 1, tzinfo=dt_timezone.utc)
    user_prefs = {"water_reminders": water, "daily_report_time": report}
    user_prefs.update(extra_prefs or {})
    user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"np-{suffix}",
        chat_id="chat-np-1" if chat_id is None else chat_id,
        proactive_messages_opt_out=opt_out,
        consent_at=now if consented else None,
        food_scanner_consent_at=now if consented else None,
        context={prefs.CONTEXT_KEY: user_prefs},
    )
    if consented:
        grant_consent(user)
    return user


def water_reader(total_ml: int, norm_ml: int = 2000):
    return lambda _ext: WaterTodayResponse(total_ml=total_ml, norm_ml=norm_ml, entries=[])


def summary_reader(profile=None):
    summary = SummaryResponse(
        date="2026-08-23",
        calories_total=1500.0,
        calories_goal=1900,
        protein_g=80.0,
        fat_g=55.0,
        carbs_g=160.0,
        entries=[],
        raw={},
    )
    water = WaterTodayResponse(total_ml=1200, norm_ml=2000, entries=[])
    return lambda _ext: (summary, water, profile)


def only(decisions, bot_user):
    return next(d for d in decisions if d.bot_user_id == bot_user.pk)


# ---------------------------------------------------------------------------


class TestWaterThreshold:
    """Mandatory cases 1 and 2 — the proportional threshold at noon.

    At 12:00 local the proportional norm of a 2000 ml day is 375 ml and the
    reminder fires below half of that, i.e. below 187.5 ml. Not below half
    the *daily* norm (1000 ml) — that version of the rule tells a person who
    has drunk 900 ml by lunchtime that they are behind, and they turn the
    bot off.
    """

    def test_noon_below_half_proportional_reminds(self, tenant: Tenant) -> None:
        user = make_user(tenant)
        decisions = tasks.plan_water_reminders(now_utc=NOON, fetch=water_reader(100))
        decision = only(decisions, user)
        assert decision.send is True
        assert decision.reason == "behind_proportional_norm"
        assert decision.detail["proportional_ml"] == 375
        assert decision.detail["threshold_ml"] == pytest.approx(187.5)

    def test_noon_on_track_stays_quiet(self, tenant: Tenant) -> None:
        user = make_user(tenant)
        decisions = tasks.plan_water_reminders(now_utc=NOON, fetch=water_reader(400))
        decision = only(decisions, user)
        assert decision.send is False
        assert decision.reason == "on_track"

    def test_noon_below_half_daily_but_above_proportional_stays_quiet(self, tenant: Tenant) -> None:
        """The regression the proportionality exists to prevent.

        900 ml is under half the 2000 ml day, so a naive rule would fire.
        It is far above the 187.5 ml owed by noon, so this one does not.
        """
        user = make_user(tenant)
        decisions = tasks.plan_water_reminders(now_utc=NOON, fetch=water_reader(900))
        assert only(decisions, user).send is False

    def test_exactly_at_threshold_stays_quiet(self, tenant: Tenant) -> None:
        user = make_user(tenant)
        decisions = tasks.plan_water_reminders(now_utc=NOON, fetch=water_reader(188))
        assert only(decisions, user).send is False

    def test_no_norm_means_no_basis_to_nudge(self, tenant: Tenant) -> None:
        user = make_user(tenant)
        decisions = tasks.plan_water_reminders(now_utc=NOON, fetch=water_reader(0, norm_ml=0))
        decision = only(decisions, user)
        assert decision.send is False
        assert decision.reason == "no_norm"


class TestQuietHours:
    """Mandatory case 3 — 23:00 sends nothing, whatever the numbers say."""

    def test_water_silent_at_2300_even_with_zero_intake(self, tenant: Tenant) -> None:
        user = make_user(tenant)
        decisions = tasks.plan_water_reminders(now_utc=ELEVEN_PM, fetch=water_reader(0))
        decision = only(decisions, user)
        assert decision.send is False
        assert decision.reason == "quiet_hours"

    def test_report_silent_at_2300_even_when_2300_was_chosen(self, tenant: Tenant) -> None:
        """A person may pick an hour we will not honour. Silence, not 23:00."""
        user = make_user(tenant, report="23:00")
        decisions = tasks.plan_daily_reports(now_utc=ELEVEN_PM, fetch=summary_reader())
        decision = only(decisions, user)
        assert decision.send is False
        assert decision.reason == "quiet_hours"

    @pytest.mark.parametrize("hour", [22, 23, 0, 3, 8])
    def test_every_quiet_hour_is_silent(self, tenant: Tenant, hour: int) -> None:
        user = make_user(tenant)
        decisions = tasks.plan_water_reminders(now_utc=at_msk(hour), fetch=water_reader(0))
        assert only(decisions, user).reason == "quiet_hours"

    def test_0900_is_the_first_waking_hour(self, tenant: Tenant) -> None:
        user = make_user(tenant)
        decisions = tasks.plan_water_reminders(now_utc=at_msk(9), fetch=water_reader(0))
        decision = only(decisions, user)
        # Awake, but nothing is owed yet at the wake-up hour itself.
        assert decision.reason == "on_track"

    def test_quiet_hours_follow_the_user_timezone_not_the_server(self, tenant: Tenant) -> None:
        """23:00 in Vladivostok is quiet even though it is 16:00 in Moscow."""
        user = make_user(tenant)
        BotUser.all_tenants.filter(pk=user.pk).update(timezone="Asia/Vladivostok")
        vlad_2300 = datetime(2026, 8, 23, 23, 0, tzinfo=ZoneInfo("Asia/Vladivostok")).astimezone(
            dt_timezone.utc
        )
        decisions = tasks.plan_water_reminders(now_utc=vlad_2300, fetch=water_reader(0))
        decision = only(decisions, user)
        assert decision.reason == "quiet_hours"
        assert decision.tz_source == "botuser"


class TestOptOutIsAbsolute:
    """Mandatory case 4 — the opt-out flag excludes a person, always."""

    @pytest.mark.parametrize("hour", list(range(0, 24)))
    def test_never_selected_at_any_hour(self, tenant: Tenant, hour: int) -> None:
        user = make_user(tenant, opt_out=True, report=f"{hour:02d}:00")
        water = tasks.plan_water_reminders(now_utc=at_msk(hour), fetch=water_reader(0))
        report = tasks.plan_daily_reports(now_utc=at_msk(hour), fetch=summary_reader())
        assert all(d.bot_user_id != user.pk or d.send is False for d in water)
        assert all(d.bot_user_id != user.pk or d.send is False for d in report)

    def test_excluded_by_the_queryset_not_merely_by_a_late_check(self, tenant: Tenant) -> None:
        user = make_user(tenant, opt_out=True)

        assert user.pk not in {u.pk for u in selection.base_queryset()}

    def test_opt_out_beats_an_explicit_opt_in(self, tenant: Tenant) -> None:
        """Preferences say yes; the veto says no. The veto wins."""
        user = make_user(tenant, opt_out=True, water=True, report="12:00")
        decisions = tasks.plan_water_reminders(now_utc=NOON, fetch=water_reader(0))
        assert all(d.send is False for d in decisions if d.bot_user_id == user.pk)

    def test_a_non_opted_out_neighbour_still_receives(self, tenant: Tenant) -> None:
        """The exclusion is per person, not a batch-wide abort."""
        silent = make_user(tenant, suffix="silent", opt_out=True)
        talkative = make_user(tenant, suffix="talkative")
        decisions = tasks.plan_water_reminders(now_utc=NOON, fetch=water_reader(50))
        assert only(decisions, talkative).send is True
        assert all(d.bot_user_id != silent.pk for d in decisions if d.send)


class TestDefaultsAreOff:
    def test_untouched_user_gets_nothing(self, tenant: Tenant) -> None:
        """No preferences at all — the common case for every existing row."""
        user = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="np-virgin",
            chat_id="chat-virgin",
            consent_at=datetime(2026, 5, 1, tzinfo=dt_timezone.utc),
            food_scanner_consent_at=datetime(2026, 5, 1, tzinfo=dt_timezone.utc),
            context={},
        )
        grant_consent(user)
        water = tasks.plan_water_reminders(now_utc=NOON, fetch=water_reader(0))
        report = tasks.plan_daily_reports(now_utc=NOON, fetch=summary_reader())
        assert only(water, user).reason == "water_off"
        assert only(report, user).reason == "report_off"

    def test_missing_food_consent_blocks(self, tenant: Tenant) -> None:
        user = make_user(tenant, consented=False)
        decisions = tasks.plan_water_reminders(now_utc=NOON, fetch=water_reader(0))
        assert only(decisions, user).reason == "no_consent"

    def test_no_chat_id_is_not_even_a_candidate(self, tenant: Tenant) -> None:
        user = make_user(tenant, chat_id="")
        decisions = tasks.plan_water_reminders(now_utc=NOON, fetch=water_reader(0))
        assert all(d.bot_user_id != user.pk for d in decisions)


class TestDailyReportSchedule:
    def test_fires_at_the_chosen_local_hour(self, tenant: Tenant) -> None:
        user = make_user(tenant, report="19:00")
        decisions = tasks.plan_daily_reports(now_utc=at_msk(19), fetch=summary_reader())
        decision = only(decisions, user)
        assert decision.send is True
        assert "Итоги дня" in decision.text

    def test_silent_at_every_other_hour(self, tenant: Tenant) -> None:
        user = make_user(tenant, report="19:00")
        for hour in range(9, 22):
            if hour == 19:
                continue
            decisions = tasks.plan_daily_reports(now_utc=at_msk(hour), fetch=summary_reader())
            assert only(decisions, user).reason == "not_report_hour"

    def test_once_per_local_day(self, tenant: Tenant) -> None:
        user = make_user(tenant, report="19:00", extra_prefs={"last_report_date": "2026-08-23"})
        decisions = tasks.plan_daily_reports(now_utc=at_msk(19), fetch=summary_reader())
        assert only(decisions, user).reason == "already_sent_today"

    def test_ayla_failure_skips_rather_than_crashes(self, tenant: Tenant) -> None:
        from apps.integrations.ayla import NutritionUnavailableError

        user = make_user(tenant, report="19:00")

        def boom(_ext):
            raise NutritionUnavailableError("circuit_open")

        decisions = tasks.plan_daily_reports(now_utc=at_msk(19), fetch=boom)
        assert only(decisions, user).reason == "ayla_unavailable"

    def test_report_body_carries_no_scolding(self, tenant: Tenant) -> None:
        make_user(tenant, report="19:00")
        decisions = tasks.plan_daily_reports(now_utc=at_msk(19), fetch=summary_reader())
        text = next(d.text for d in decisions if d.send)
        assert "Калории: 1500 из 1900 ккал." in text
        assert "Вода: 1200 из 2000 мл." in text
        assert "не пиши мне" in text


class TestQuotaAndAutoDisable:
    def test_daily_quota_caps_the_reminders(self, tenant: Tenant) -> None:
        user = make_user(
            tenant,
            extra_prefs={
                "water": {
                    "date": "2026-08-23",
                    "sent": prefs.MAX_WATER_REMINDERS_PER_DAY,
                    "last_total_ml": 0,
                    "ignored_streak": 0,
                }
            },
        )
        decisions = tasks.plan_water_reminders(now_utc=NOON, fetch=water_reader(0))
        assert only(decisions, user).reason == "daily_quota"

    def test_ignored_streak_turns_the_feature_off(self, tenant: Tenant) -> None:
        """Two unheeded reminders and a third that changed nothing -> off."""
        user = make_user(
            tenant,
            extra_prefs={
                "water": {
                    "date": "2026-08-23",
                    "sent": 1,
                    "last_total_ml": 100,
                    "ignored_streak": prefs.IGNORED_STREAK_LIMIT - 1,
                }
            },
        )
        decisions = tasks.plan_water_reminders(now_utc=NOON, fetch=water_reader(100))
        decision = only(decisions, user)
        assert decision.send is False
        assert decision.reason == "auto_disabled"
        assert decision.pref_updates["water_reminders"] is False

    def test_drinking_resets_the_streak(self, tenant: Tenant) -> None:
        user = make_user(
            tenant,
            extra_prefs={
                "water": {
                    "date": "2026-08-23",
                    "sent": 1,
                    "last_total_ml": 100,
                    "ignored_streak": prefs.IGNORED_STREAK_LIMIT - 1,
                }
            },
        )
        decisions = tasks.plan_water_reminders(now_utc=NOON, fetch=water_reader(150))
        decision = only(decisions, user)
        assert decision.send is True
        assert decision.pref_updates["water"]["ignored_streak"] == 0


class TestOutboundSafety:
    """The report passes Ayla's ``ai_comment`` through verbatim, and that
    sentence is written by a model nothing on our side reviewed. A proactive
    message is the worst place for it to land unchecked: the person asked
    nothing, so they have no reason to read it sceptically.
    """

    def _reader_with_comment(self, comment: str):
        summary = SummaryResponse(
            date="2026-08-23",
            calories_total=1500.0,
            calories_goal=1900,
            protein_g=80.0,
            fat_g=55.0,
            carbs_g=160.0,
            entries=[{"id": 1}],
            raw={},
            ai_comment=comment,
        )
        water = WaterTodayResponse(total_ml=1200, norm_ml=2000, entries=[])
        return lambda _ext: (summary, water, None)

    def test_a_medical_claim_from_ayla_stops_the_send(self, tenant: Tenant) -> None:
        user = make_user(tenant, report="19:00")
        decisions = tasks.plan_daily_reports(
            now_utc=at_msk(19),
            fetch=self._reader_with_comment("У вас аллергия на глютен, примите антибиотик."),
        )
        decision = only(decisions, user)
        assert decision.send is False
        assert decision.reason.startswith("outbound_safety")

    def test_a_blocked_report_is_dropped_not_replaced(self, tenant: Tenant) -> None:
        """The pipeline swaps in «тут нужен человек» because someone is
        waiting for an answer. Unsolicited, that line is a non-sequitur —
        silence is the correct outcome."""
        from apps.orchestrator.safety.outbound import REPLACEMENT_TEXT

        make_user(tenant, report="19:00")
        decisions = tasks.plan_daily_reports(
            now_utc=at_msk(19),
            fetch=self._reader_with_comment("Гарантирую результат, вернём деньги."),
        )
        assert all(REPLACEMENT_TEXT not in d.text for d in decisions)
        assert all(d.send is False for d in decisions)

    def test_a_blocked_report_does_not_burn_the_day(self, tenant: Tenant) -> None:
        """No idempotency bump — tomorrow's report is a different text and
        deserves its own evaluation."""
        user = make_user(tenant, report="19:00")
        decisions = tasks.plan_daily_reports(
            now_utc=at_msk(19),
            fetch=self._reader_with_comment("У вас инфекция."),
        )
        assert only(decisions, user).pref_updates == {}

    def test_a_clean_comment_goes_through(self, tenant: Tenant) -> None:
        user = make_user(tenant, report="19:00")
        decisions = tasks.plan_daily_reports(
            now_utc=at_msk(19),
            fetch=self._reader_with_comment("Сегодня в рационе много овощей."),
        )
        decision = only(decisions, user)
        assert decision.send is True
        assert "много овощей" in decision.text

    def test_our_own_copy_passes_the_gate(self, tenant: Tenant) -> None:
        """Regression guard on the copy this module writes: if a future
        edit puts a blocked shape into the report or the nudge, this fails
        here rather than going silent on the pilot."""
        from apps.orchestrator.safety.outbound import evaluate_outbound

        make_user(tenant, report="19:00")
        report = tasks.plan_daily_reports(now_utc=at_msk(19), fetch=summary_reader())
        water = tasks.plan_water_reminders(now_utc=NOON, fetch=water_reader(50))
        for decision in [d for d in report + water if d.send]:
            assert evaluate_outbound(decision.text).allowed is True


class TestSwitches:
    def test_disabled_task_touches_nothing(self, tenant: Tenant, settings) -> None:
        make_user(tenant)
        settings.NUTRITION_PROACTIVE_ENABLED = False
        with patch("apps.nutrition_proactive.tasks.send_message") as send:
            assert tasks.send_water_reminders()["sent"] == 0
            assert tasks.send_daily_reports()["sent"] == 0
        send.assert_not_called()

    def test_enabled_but_dry_run_sends_nothing(self, tenant: Tenant, settings) -> None:
        make_user(tenant)
        settings.NUTRITION_PROACTIVE_ENABLED = True
        settings.NUTRITION_PROACTIVE_DRY_RUN = True
        with (
            patch("apps.nutrition_proactive.tasks.send_message") as send,
            patch(
                "apps.nutrition_proactive.tasks._fetch_water",
                side_effect=water_reader(0),
            ),
            patch("apps.nutrition_proactive.tasks.dj_timezone.now", return_value=NOON),
        ):
            result = tasks.send_water_reminders()
        send.assert_not_called()
        assert result["would_send"] == 1
        assert result["sent"] == 0
        assert result["dry_run"] == 1

    def test_enabled_and_armed_sends_once_and_records_it(self, tenant: Tenant, settings) -> None:
        user = make_user(tenant)
        settings.NUTRITION_PROACTIVE_ENABLED = True
        settings.NUTRITION_PROACTIVE_DRY_RUN = False
        with (
            patch("apps.nutrition_proactive.tasks.send_message") as send,
            patch(
                "apps.nutrition_proactive.tasks._fetch_water",
                side_effect=water_reader(0),
            ),
            patch("apps.nutrition_proactive.tasks.dj_timezone.now", return_value=NOON),
        ):
            result = tasks.send_water_reminders()
        assert result["sent"] == 1
        send.assert_called_once()
        assert send.call_args.kwargs["chat_id"] == "chat-np-1"

        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=user.pk))
        assert stored["water"]["sent"] == 1

    def test_send_failure_leaves_the_counter_alone(self, tenant: Tenant, settings) -> None:
        from apps.channels.max.outbound import MaxAPIError

        user = make_user(tenant)
        settings.NUTRITION_PROACTIVE_ENABLED = True
        settings.NUTRITION_PROACTIVE_DRY_RUN = False
        with (
            patch(
                "apps.nutrition_proactive.tasks.send_message",
                side_effect=MaxAPIError(502, "bad gateway"),
            ),
            patch(
                "apps.nutrition_proactive.tasks._fetch_water",
                side_effect=water_reader(0),
            ),
            patch("apps.nutrition_proactive.tasks.dj_timezone.now", return_value=NOON),
        ):
            result = tasks.send_water_reminders()
        assert result["failed"] == 1
        assert result["sent"] == 0
        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=user.pk))
        assert stored.get("water", {}).get("sent", 0) == 0


# ────────────────────────────────────────────────────────────────────
# DRF-1314 — who may be written to first
# ────────────────────────────────────────────────────────────────────


class TestConsentGate:
    """Who may be written to first, and why not.

    The bug this suite exists for: :func:`selection.check_common` read
    ``BotUser.consent_at`` and never ``ConsentRecord``.
    :func:`apps.consent.services.withdraw` stamps ``withdrawn_at`` on the
    record and deliberately leaves the column set, so somebody who
    explicitly withdrew still read as consenting. Measured on the pilot
    on 2026-08-23: five of twelve reachable rows had the stamp, and four
    of those five had withdrawn.

    Every case below builds a person who differs from :func:`make_user`
    in exactly one respect, so a failure names the condition that broke.
    Both planners are asserted on, not just one: the gate is called from
    two places and a fix applied to one of them is not a fix.
    """

    def _both(self, tenant: Tenant, user: BotUser):
        """Every decision either planner reaches about ``user``."""
        water = tasks.plan_water_reminders(now_utc=NOON, fetch=water_reader(0))
        report = tasks.plan_daily_reports(now_utc=NOON, fetch=summary_reader())
        return [d for d in [*water, *report] if d.bot_user_id == user.pk]

    def test_withdrawn_consent_is_not_written_to(self, tenant: Tenant) -> None:
        """The case ``consent_at`` alone cannot see. The live pilot's shape.

        Four people on the pilot are in exactly this state today, and the
        layer that would have written to them was deployed and gated only
        by a feature flag.
        """
        user = make_user(tenant, water=True, report="12:00")
        ConsentRecord.all_tenants.filter(bot_user=user).update(withdrawn_at=NOON)
        user.refresh_from_db()
        assert user.consent_at is not None, "the column stays set — that is the trap"

        decisions = self._both(tenant, user)
        assert len(decisions) == 2
        assert [d.reason for d in decisions] == ["consent_withdrawn"] * 2
        assert all(d.send is False for d in decisions)

    def test_consent_stamp_without_a_record_is_reported_as_unproven(self, tenant: Tenant) -> None:
        """A stamp with no proof behind it is not consent we can show.

        A distinct slug from ``consent_withdrawn`` on purpose: this is a
        provenance gap left by grants predating #1074 (which made the
        stamp and the record atomic), and it is a different operator
        problem from somebody having said no.
        """
        user = make_user(tenant, water=True, report="12:00")
        ConsentRecord.all_tenants.filter(bot_user=user).delete()

        decisions = self._both(tenant, user)
        assert [d.reason for d in decisions] == ["consent_unproven"] * 2

    def test_never_consented_is_not_written_to(self, tenant: Tenant) -> None:
        """``consent_at IS NULL`` — six of the pilot's twelve rows."""
        user = make_user(tenant, consented=False, water=True, report="12:00")

        decisions = self._both(tenant, user)
        assert [d.reason for d in decisions] == ["no_consent"] * 2

    def test_erased_user_is_not_written_to(self, tenant: Tenant) -> None:
        """``soft_delete_user()`` does not clear ``chat_id``.

        An erased row stays addressable, which is the whole reason this
        condition is in the gate rather than left to the queryset. One
        such row exists on the pilot.
        """
        user = make_user(tenant, water=True, report="12:00")
        BotUser.all_tenants.filter(pk=user.pk).update(deleted_at=NOON)
        user.refresh_from_db()
        assert (user.chat_id or "").strip(), "still addressable — that is why this gate exists"

        assert self._both(tenant, user) == []
        assert selection.check_common(user) == "deleted"

    def test_opt_out_is_still_the_first_veto(self, tenant: Tenant) -> None:
        """Delegation must not demote the one veto whose failure is a trust break.

        Asserted against a row that would fail a *later* condition too:
        the reason must be ``opt_out``, which is only true if the veto is
        evaluated before consent is even looked at.
        """
        user = make_user(tenant, opt_out=True, consented=False)
        assert selection.check_common(user) == "opt_out"

    def test_missing_food_consent_blocks_a_fully_consenting_person(self, tenant: Tenant) -> None:
        """The nutrition-specific condition survived the delegation.

        ``food_scanner_consent_at`` has no ``ConsentRecord`` behind it —
        ``ConsentType`` has no food-scanner member — so it is still read
        from the column, and it must still bite for somebody who cleared
        the shared gate completely.
        """
        user = make_user(tenant, water=True, report="12:00")
        BotUser.all_tenants.filter(pk=user.pk).update(food_scanner_consent_at=None)
        user.refresh_from_db()

        decisions = self._both(tenant, user)
        assert [d.reason for d in decisions] == ["no_food_consent"] * 2

    def test_a_consenting_person_still_receives_both_messages(self, tenant: Tenant) -> None:
        """The other half of the proof.

        Every assertion above is satisfied by a layer that writes to
        nobody at all, which is exactly the ambiguity a zero-recipient
        dry run carries. This one fails if the gate has been tightened
        into a wall, so "nobody was written to" can be read as protection
        rather than as breakage.
        """
        user = make_user(tenant, water=True, report="12:00")

        decisions = self._both(tenant, user)
        assert len(decisions) == 2
        assert all(d.send is True for d in decisions), [d.reason for d in decisions]
        assert {d.reason for d in decisions} == {"behind_proportional_norm", "due"}

    def test_the_gate_is_the_shared_one_not_a_local_copy(self, tenant: Tenant) -> None:
        """The four conditions are borrowed, not re-inlined.

        The regression guarded against is a fourth copy of the gate.
        Three things have to hold: the name this module calls **is** the
        shared function, its whole vocabulary is declared here, and the
        call actually happens on the live path — the last checked by
        replacing it and watching ``check_common`` change its answer.
        """
        from apps.notifications import proactive

        assert selection.consent_blocker is proactive.consent_blocker
        assert set(proactive.BLOCK_REASONS) <= set(selection.BLOCK_REASONS)

        user = make_user(tenant)
        assert selection.check_common(user) is None
        with patch.object(selection, "consent_blocker", return_value="opt_out") as gate:
            assert selection.check_common(user) == "opt_out"
        gate.assert_called_once_with(user)
