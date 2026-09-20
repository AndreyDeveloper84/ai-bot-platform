"""Час отчёта из чата (DRF-2141).

Три команды, ни одна не идёт через модель: «присылай итоги в 21:00»
ставит ``daily_report_time``, «не присылай отчёт» гасит ОДНУ поверхность,
«во сколько ты присылаешь итоги?» читает то, что стоит. Сторожа из листа:

* 21:00 → prefs=21:00, и планировщик ставит отчёт на этот час;
* «не присылай отчёт» → ``off``, вода НЕ выключена (ложный вход в
  :class:`TestOffKeepsWater` показывает, что страж различает);
* текст «21:00» без глагола — не команда;
* тихие часы не изменились.
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from zoneinfo import ZoneInfo

import pytest

from apps.identity.models import BotUser
from apps.nutrition_proactive import optout, prefs, report_hour, tasks
from apps.nutrition_proactive.tests.test_tasks import (
    at_msk,
    make_user,
    only,
    summary_reader,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="np-report-hour", name="Salon", timezone="Europe/Moscow")


@pytest.fixture
def flags_on(settings):
    settings.NUTRITION_ENABLED = True
    settings.NUTRITION_PROACTIVE_ENABLED = True
    return settings


@pytest.fixture
def bot_user(tenant: Tenant, flags_on) -> BotUser:
    """Подписан на воду и на отчёт в 19:00 — есть что переставить и что
    НЕ должно выключиться."""
    return make_user(tenant, suffix="rh", water=True, report="19:00")


# -- matcher -----------------------------------------------------------------


class TestParse:
    @pytest.mark.parametrize(
        ("text", "time"),
        [
            ("присылай итоги в 21:00", "21:00"),
            ("Присылай итоги в 21:00.", "21:00"),
            ("присылай отчёт в 20:30", "20:30"),
            ("присылай отчет в 20.30", "20:30"),
            ("присылай итоги дня в 9", "09:00"),
            ("присылай мне итоги в 21", "21:00"),
            ("присылайте отчёт в 18:00", "18:00"),
            ("присылай итоги в 21 час", "21:00"),
            ("присылай итоги в 7 часов", "07:00"),
            ("отправляй итоги в 21:00", "21:00"),
        ],
    )
    def test_set_variants(self, text: str, time: str) -> None:
        cmd = report_hour.parse_command(text)
        assert cmd is not None
        assert cmd.kind == "set"
        assert cmd.time == time

    @pytest.mark.parametrize(
        "text",
        [
            "не присылай отчёт",
            "не присылай отчет",
            "не присылай итоги",
            "Не присылай итоги дня!",
            "не присылай мне отчёт",
            "больше не присылай итоги",
            "не присылай итоги больше",
            "не присылайте отчёт",
        ],
    )
    def test_off_variants(self, text: str) -> None:
        cmd = report_hour.parse_command(text)
        assert cmd is not None
        assert cmd.kind == "off"

    @pytest.mark.parametrize(
        "text",
        [
            "во сколько ты присылаешь итоги?",
            "Во сколько ты присылаешь итоги",
            "во сколько присылаешь отчёт?",
            "во сколько ты присылаешь итоги дня?",
            "во сколько вы присылаете отчет?",
            "когда ты присылаешь итоги?",
        ],
    )
    def test_ask_variants(self, text: str) -> None:
        cmd = report_hour.parse_command(text)
        assert cmd is not None
        assert cmd.kind == "ask"

    @pytest.mark.parametrize(
        "text",
        [
            # лист: «21:00» без глагола — не команда
            "21:00",
            "21",
            "в 21:00",
            "итоги в 21:00",
            # соседние намерения, которые обязаны остаться у своих веток
            "пришли итоги",
            "покажи итоги дня",
            "запиши меня в 21:00",
            "во сколько вы работаете?",
            "во сколько мне прийти?",
            "не пиши мне",
            "не присылай ничего",
            "присылай итоги",
            "присылай итоги в 25:99",
            "",
            "   ",
        ],
    )
    def test_not_a_command(self, text: str) -> None:
        assert report_hour.parse_command(text) is None

    def test_a_paragraph_that_quotes_the_command_is_not_the_command(self) -> None:
        text = "присылай итоги в 21:00 " + "и ещё много всего " * 20
        assert report_hour.parse_command(text) is None

    def test_no_opt_out_phrase_is_claimed(self) -> None:
        """«не пиши мне» и семья остаются у выключателя всего (optout)."""
        claimed = [p for p in sorted(optout.OPT_OUT_PHRASES) if report_hour.parse_command(p)]
        assert claimed == []

    def test_no_booking_cancellation_phrase_is_claimed(self) -> None:
        """Тот же корпус, что охраняет optout: отмена визита — не про отчёт."""
        from apps.skills.booking.tests.test_lookup_routing import OD_IR1_CANCEL_CORPUS

        assert len(OD_IR1_CANCEL_CORPUS) > 0
        stolen = [p for p in OD_IR1_CANCEL_CORPUS if report_hour.parse_command(p)]
        assert stolen == []


# -- effect ------------------------------------------------------------------


class TestSet:
    def test_2100_lands_in_prefs_through_the_shared_writer(self, bot_user: BotUser) -> None:
        reply = report_hour.try_handle_report_hour(text="присылай итоги в 21:00", bot_user=bot_user)

        assert reply == report_hour.SET_CONFIRMATION.format(time="21:00")
        fresh = BotUser.all_tenants.get(pk=bot_user.pk)
        stored = prefs.get_prefs(fresh)
        assert stored["daily_report_time"] == "21:00"
        # In-memory instance kept honest for the rest of the turn.
        assert prefs.get_prefs(bot_user)["daily_report_time"] == "21:00"
        # Nothing else moved.
        assert stored["water_reminders"] is True
        assert fresh.proactive_messages_opt_out is False

    def test_the_beat_plans_the_report_at_that_hour(self, bot_user: BotUser) -> None:
        """Узел из листа: prefs=21:00 → отчёт планируется в этот час."""
        report_hour.try_handle_report_hour(text="присылай итоги в 21:00", bot_user=bot_user)

        decision = only(
            tasks.plan_daily_reports(now_utc=at_msk(21), fetch=summary_reader()), bot_user
        )
        assert decision.send is True
        assert decision.reason == "due"
        # Positive control for the guard: one hour earlier it is NOT due.
        earlier = only(
            tasks.plan_daily_reports(now_utc=at_msk(20), fetch=summary_reader()), bot_user
        )
        assert earlier.send is False
        assert earlier.reason == "not_report_hour"

    def test_the_hour_is_the_persons_local_hour(self, tenant: Tenant, flags_on) -> None:
        """«по твоему времени»: 21:00 во Владивостоке, а не в Москве."""
        user = make_user(tenant, suffix="vl", water=True, report="19:00")
        BotUser.all_tenants.filter(pk=user.pk).update(timezone="Asia/Vladivostok")
        user = BotUser.all_tenants.get(pk=user.pk)

        report_hour.try_handle_report_hour(text="присылай итоги в 21:00", bot_user=user)

        vlad_2100 = datetime(2026, 8, 23, 21, 0, tzinfo=ZoneInfo("Asia/Vladivostok")).astimezone(
            dt_timezone.utc
        )
        decision = only(tasks.plan_daily_reports(now_utc=vlad_2100, fetch=summary_reader()), user)
        assert decision.send is True
        assert decision.tz_source == "botuser"
        # Moscow 21:00 is 04:00 in Vladivostok: quiet, not due.
        msk = only(tasks.plan_daily_reports(now_utc=at_msk(21), fetch=summary_reader()), user)
        assert msk.send is False

    @pytest.mark.parametrize(
        "text", ["присылай итоги в 3:00", "присылай итоги в 5", "присылай отчёт в 0:30"]
    )
    def test_a_night_hour_is_refused_and_nothing_changes(
        self, bot_user: BotUser, text: str
    ) -> None:
        reply = report_hour.try_handle_report_hour(text=text, bot_user=bot_user)

        assert reply == report_hour.NIGHT_REFUSAL
        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=bot_user.pk))
        assert stored["daily_report_time"] == "19:00"

    @pytest.mark.parametrize("hour", [6, 23])
    def test_the_range_edges_are_accepted(self, bot_user: BotUser, hour: int) -> None:
        reply = report_hour.try_handle_report_hour(
            text=f"присылай итоги в {hour}:00", bot_user=bot_user
        )
        assert reply == report_hour.SET_CONFIRMATION.format(time=f"{hour:02d}:00")

    def test_other_context_keys_survive(self, bot_user: BotUser) -> None:
        BotUser.all_tenants.filter(pk=bot_user.pk).update(
            context={**bot_user.context, "last_followup_sent_at": "2026-05-01"}
        )
        bot_user = BotUser.all_tenants.get(pk=bot_user.pk)

        report_hour.try_handle_report_hour(text="присылай итоги в 21:00", bot_user=bot_user)

        fresh = BotUser.all_tenants.get(pk=bot_user.pk)
        assert fresh.context["last_followup_sent_at"] == "2026-05-01"
        assert prefs.get_prefs(fresh)["daily_report_time"] == "21:00"


class TestOffKeepsWater:
    def test_off_silences_the_report_only(self, bot_user: BotUser) -> None:
        reply = report_hour.try_handle_report_hour(text="не присылай отчёт", bot_user=bot_user)

        # Та же кнопка «Не присылать» под отчётом: тот же писатель, тот же текст.
        assert reply == optout.SURFACE_CONFIRMATIONS["report"]
        fresh = BotUser.all_tenants.get(pk=bot_user.pk)
        stored = prefs.get_prefs(fresh)
        assert stored["daily_report_time"] == prefs.REPORT_OFF
        assert stored["water_reminders"] is True
        assert fresh.proactive_messages_opt_out is False

    def test_false_input_the_guard_would_catch(self, bot_user: BotUser) -> None:
        """Ложный вход: сосед, который выключает ВСЁ. Те же три проверки,
        что выше, здесь обязаны провалиться — иначе страж слеп."""
        optout.apply_opt_out(bot_user)

        fresh = BotUser.all_tenants.get(pk=bot_user.pk)
        stored = prefs.get_prefs(fresh)
        assert stored["daily_report_time"] == prefs.REPORT_OFF
        assert stored["water_reminders"] is False
        assert fresh.proactive_messages_opt_out is True

    def test_repeating_it_is_harmless(self, bot_user: BotUser) -> None:
        first = report_hour.try_handle_report_hour(text="не присылай итоги", bot_user=bot_user)
        second = report_hour.try_handle_report_hour(text="не присылай итоги", bot_user=bot_user)
        assert first == second
        assert prefs.get_prefs(BotUser.all_tenants.get(pk=bot_user.pk))["water_reminders"] is True


class TestAsk:
    def test_reads_the_stored_hour(self, bot_user: BotUser) -> None:
        reply = report_hour.try_handle_report_hour(
            text="во сколько ты присылаешь итоги?", bot_user=bot_user
        )
        assert reply == report_hour.ASK_REPLY.format(time="19:00")
        # Reading does not write.
        assert (
            prefs.get_prefs(BotUser.all_tenants.get(pk=bot_user.pk))["daily_report_time"] == "19:00"
        )

    def test_reads_back_what_was_just_set(self, bot_user: BotUser) -> None:
        report_hour.try_handle_report_hour(text="присылай итоги в 21:00", bot_user=bot_user)
        reply = report_hour.try_handle_report_hour(
            text="во сколько ты присылаешь итоги?", bot_user=bot_user
        )
        assert reply == report_hour.ASK_REPLY.format(time="21:00")

    def test_when_off_says_so(self, bot_user: BotUser) -> None:
        report_hour.try_handle_report_hour(text="не присылай отчёт", bot_user=bot_user)
        reply = report_hour.try_handle_report_hour(
            text="во сколько ты присылаешь итоги?", bot_user=bot_user
        )
        assert reply == report_hour.ASK_REPLY_OFF
        assert "21:00" not in reply


class TestGates:
    def test_falls_through_when_nutrition_is_off(self, tenant: Tenant, settings) -> None:
        settings.NUTRITION_ENABLED = False
        settings.NUTRITION_PROACTIVE_ENABLED = True
        user = make_user(tenant, suffix="g1", report="19:00")
        assert (
            report_hour.try_handle_report_hour(text="присылай итоги в 21:00", bot_user=user) is None
        )
        assert prefs.get_prefs(BotUser.all_tenants.get(pk=user.pk))["daily_report_time"] == "19:00"

    def test_falls_through_when_proactive_is_off(self, tenant: Tenant, settings) -> None:
        settings.NUTRITION_ENABLED = True
        settings.NUTRITION_PROACTIVE_ENABLED = False
        user = make_user(tenant, suffix="g2", report="19:00")
        assert report_hour.try_handle_report_hour(text="не присылай отчёт", bot_user=user) is None
        assert prefs.get_prefs(BotUser.all_tenants.get(pk=user.pk))["daily_report_time"] == "19:00"

    def test_falls_through_on_anything_else(self, bot_user: BotUser) -> None:
        assert report_hour.try_handle_report_hour(text="21:00", bot_user=bot_user) is None
        assert report_hour.try_handle_report_hour(text="хочу на маникюр", bot_user=bot_user) is None

    def test_it_never_raises(self, flags_on) -> None:
        class Exploding:
            pk = 1

            @property
            def context(self):
                raise RuntimeError("boom")

        assert (
            report_hour.try_handle_report_hour(text="присылай итоги в 21:00", bot_user=Exploding())
            is None
        )


class TestQuietHoursUnchanged:
    def test_the_window_is_still_2200_0859(self) -> None:
        assert prefs.QUIET_START_HOUR == 22
        assert prefs.QUIET_END_HOUR == 9
        assert prefs.is_quiet_hour(21) is False
        assert prefs.is_quiet_hour(22) is True
        assert prefs.is_quiet_hour(8) is True
        assert prefs.is_quiet_hour(9) is False

    def test_setting_2300_does_not_move_the_window(self, bot_user: BotUser) -> None:
        """Лист принимает 23:00; тихие часы — нет. Планировщик молчит,
        как и до этого листа (``test_report_silent_at_2300_even_when_2300_was_chosen``)."""
        report_hour.try_handle_report_hour(text="присылай итоги в 23:00", bot_user=bot_user)
        decision = only(
            tasks.plan_daily_reports(now_utc=at_msk(23), fetch=summary_reader()), bot_user
        )
        assert decision.send is False
        assert decision.reason == "quiet_hours"


class TestTexts:
    def test_the_texts_are_the_leafs(self) -> None:
        assert report_hour.SET_CONFIRMATION.format(time="21:00") == "Хорошо, итоги дня — в 21:00."
        assert report_hour.ASK_REPLY.format(time="21:00") == (
            "Итоги дня присылаю в 21:00 (по твоему времени). "
            "Скажи „присылай итоги в 20:00“, если хочешь иначе, или „не присылай отчёт“."
        )
        assert report_hour.NIGHT_REFUSAL == "Ночью не пишу — выбери час с 6 до 23."
