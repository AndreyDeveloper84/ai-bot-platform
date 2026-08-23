"""DRF-1325 — the booking flow answers in human time.

The live evidence this file stands on (pilot, 2026-08-23):

    17:48:36  владелец:  хочу на массаж ЗАВТРА ВЕЧЕРОМ
    17:48:36  бот:       [список мастеров, про время ни слова]
    …
    17:50:45  бот:       Записываю: 28.08 11:30

Two defects in one dialogue. The request named a day and a part of the day,
and neither was used nor asked back about; and what the bot offered instead
was a bare calendar — «Выберите дату», then «Выберите время».

The tests below pin the two halves separately, because they can regress
separately: the chips can go on working while the parse rots, and the parse
can go on working while the chips drift back into a wall of ISO dates.

Nothing here asserts that a slot is FREE. There is no authoritative
availability contract (``docs/OD_SALON_P0_CONTRACT.md``) and ``create``'s
409 remains the last word. What IS asserted is the weaker, checkable
property the ticket demands: every chip the bot renders leads to something
the schedule read actually returned.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.core.cache import cache

from apps.bookings.keyboards import (
    CALLBACK_BOOK_MORE_DATES_PREFIX,
    CALLBACK_BOOK_PICK_PART_PREFIX,
)
from apps.orchestrator.time_preference import (
    PART_EVENING,
    TimePreference,
    save_time_preference,
)
from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.integrations.yclients.client import AvailableTime
from apps.llm.router import reset_router_cache
from apps.skills.base import SkillContext, SkillResult
from apps.skills.booking.skill import BookingSkill

# Fakes and builders only — the fixtures below are defined here rather than
# imported, because a fixture imported into a second module is redefined by
# every test that names it as a parameter (F811) and the noise buries real
# findings.
from apps.skills.booking.tests.test_skill import (
    FakeYClients,
    _patch_provider_complete,
    _patch_yclients,
    _service,
    _staff,
)
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, settings):  # noqa: ANN001, ANN201
    settings.BASE_DIR = tmp_path
    settings.LLM_PROVIDER = "openai"
    settings.SKILL_LLM_PROVIDER = {}
    reset_router_cache()
    cache.clear()
    yield
    cache.clear()
    reset_router_cache()


@pytest.fixture
def tenant(db) -> Tenant:  # noqa: ANN001
    return Tenant.objects.create(slug="booking-time-chips", name="Time Chips")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="u1",
        chat_id="u1",
        phone="79991234567",
        client_name="Anna",
    )


@pytest.fixture
def context(tenant: Tenant, bot_user: BotUser) -> SkillContext:
    conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)
    return SkillContext(
        conversation=conv,
        bot_user=bot_user,
        message_text="запиши на массаж",
        trace_id="t-drf1325",
    )


def _at(day: str, hhmm: str) -> AvailableTime:
    return AvailableTime(time=hhmm, datetime=f"{day}T{hhmm}:00", seance_length_s=3600)


def _callbacks(result: SkillResult) -> list[str]:
    assert result.action_data is not None
    return [b["callback"] for b in result.action_data["attachments"][0]["payload"]["buttons"]]


def _labels(result: SkillResult) -> list[str]:
    assert result.action_data is not None
    return [b["label"] for b in result.action_data["attachments"][0]["payload"]["buttons"]]


def _tap(context: SkillContext, payload: str) -> SkillContext:
    return SkillContext(
        conversation=context.conversation,
        bot_user=context.bot_user,
        message_text=payload,
    )


def _iso(offset: int) -> str:
    """A date ``offset`` days from today, in the salon's zone.

    The tests run against the real clock because the code under test does:
    «Завтра» is only meaningful relative to now, and freezing time here would
    hide exactly the drift these assertions exist to catch.
    """
    return (date.today() + timedelta(days=offset)).isoformat()


class TestDayChips:
    """«Выберите дату» → «Сегодня / Завтра / Послезавтра / Выбрать дату»."""

    def test_master_pick_renders_relative_day_chips(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = [_iso(0), _iso(1), _iso(2), _iso(5)]
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(_tap(context, "cb:book:pick_master:11:22"))
        labels = _labels(result)
        assert labels[:3] == ["Сегодня", "Завтра", "Послезавтра"]
        # The fourth day does not get a fourth chip — it goes behind the
        # escape hatch, so the keyboard stays the size of a decision.
        assert labels[3] == "Выбрать дату"
        assert _callbacks(result)[3] == f"{CALLBACK_BOOK_MORE_DATES_PREFIX}11:22"

    def test_chips_are_the_existing_date_buttons_in_human_clothes(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """The callback grammar is unchanged — a chip is a relabelled date.

        This matters beyond tidiness: ``cb:book:pick_date:`` is routed by the
        global handler, the handoff layer and the per-tenant handler alike. A
        new grammar for the chips would have needed all three to agree.
        """
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = [_iso(0), _iso(1)]
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(_tap(context, "cb:book:pick_master:11:22"))
        assert _callbacks(result) == [
            f"cb:book:pick_date:11:{_iso(0)}:22",
            f"cb:book:pick_date:11:{_iso(1)}:22",
        ]

    def test_no_escape_hatch_when_everything_already_fits(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """«Выбрать дату» that opens the same three days is a lie about
        there being more."""
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = [_iso(0), _iso(1), _iso(2)]
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(_tap(context, "cb:book:pick_master:11:22"))
        assert "Выбрать дату" not in _labels(result)

    def test_more_dates_expands_to_every_free_day(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = [_iso(i) for i in range(6)]
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(
                    _tap(context, f"{CALLBACK_BOOK_MORE_DATES_PREFIX}11:22")
                )
        assert result.reply_text == "Выберите дату:"
        assert len(_callbacks(result)) == 6
        # Beyond the third day a count stops being readable, so those wear a
        # date — the relative words are a convenience, not a rule.
        assert _labels(result)[:3] == ["Сегодня", "Завтра", "Послезавтра"]
        assert all(w not in _labels(result)[3:] for w in ("Сегодня", "Завтра"))


class TestPartChips:
    """«Выберите время» → «Утро / День / Вечер / Точное время»."""

    def test_date_tap_asks_for_a_part_of_day(self, context: SkillContext, tenant: Tenant) -> None:
        day = _iso(1)
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = [day]
        client.times = [_at(day, "09:00"), _at(day, "14:00"), _at(day, "19:00")]
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(_tap(context, f"cb:book:pick_date:11:{day}:22"))
        assert result.reply_text == "Когда удобно завтра?"
        labels = _labels(result)
        # The boundary travels WITH the word: the user must be able to see
        # what «Вечер» means before tapping it, or the chip and the slots it
        # opens are free to disagree.
        assert labels == [
            "Утро (до 12:00)",
            "День (12:00–17:00)",
            "Вечер (с 17:00)",
            "Точное время",
        ]
        assert _callbacks(result)[2] == (f"{CALLBACK_BOOK_PICK_PART_PREFIX}11:{day}:evening:22")

    def test_only_parts_with_slots_get_a_chip(self, context: SkillContext, tenant: Tenant) -> None:
        """A chip must lead to something.

        «Вечер» on a day whose last slot is 15:00 is a button into a dead
        end, and the ticket is explicit that a button into «я вас не понял»
        is worse than no button.
        """
        day = _iso(1)
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = [day]
        client.times = [_at(day, "09:00"), _at(day, "15:00")]
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(_tap(context, f"cb:book:pick_date:11:{day}:22"))
        labels = _labels(result)
        assert "Вечер (с 17:00)" not in labels
        assert labels == ["Утро (до 12:00)", "День (12:00–17:00)", "Точное время"]

    def test_a_single_part_is_not_worth_a_question(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        day = _iso(1)
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = [day]
        client.times = [_at(day, "18:00"), _at(day, "19:00")]
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(_tap(context, f"cb:book:pick_date:11:{day}:22"))
        assert result.reply_text == "Завтра, вечером — выберите время:"
        assert _callbacks(result) == [
            f"cb:book:pick_slot:11:22:{day}T18:00:00",
            f"cb:book:pick_slot:11:22:{day}T19:00:00",
        ]

    def test_part_tap_narrows_the_slot_list(self, context: SkillContext, tenant: Tenant) -> None:
        day = _iso(1)
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = [day]
        client.times = [_at(day, "09:00"), _at(day, "14:00"), _at(day, "19:00")]
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(
                    _tap(context, f"{CALLBACK_BOOK_PICK_PART_PREFIX}11:{day}:evening:22")
                )
        assert _callbacks(result) == [f"cb:book:pick_slot:11:22:{day}T19:00:00"]

    def test_exact_time_returns_the_whole_day(self, context: SkillContext, tenant: Tenant) -> None:
        """The chips are a shortcut, never a cage: somebody who wants 14:00
        specifically has to be able to reach it."""
        day = _iso(1)
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = [day]
        client.times = [_at(day, "09:00"), _at(day, "14:00"), _at(day, "19:00")]
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(
                    _tap(context, f"{CALLBACK_BOOK_PICK_PART_PREFIX}11:{day}:any:22")
                )
        assert result.reply_text == "Выберите время:"
        assert len(_callbacks(result)) == 3


class TestStatedPreferenceIsHonoured:
    """«хочу на массаж завтра вечером» — the live defect, end to end."""

    def _remember(self, context: SkillContext, pref: TimePreference) -> None:
        save_time_preference(context.conversation, pref)

    def test_tomorrow_evening_skips_the_calendar_entirely(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """The whole ticket in one assertion.

        Before: the master tap opened a 14-date calendar and the request was
        already gone. After: the day question and the part question are both
        answered by what the user said, and the reply is tomorrow's evening.
        """
        tomorrow = _iso(1)
        self._remember(
            context, TimePreference(day_offset=1, part=PART_EVENING, said="завтра вечером")
        )
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = [_iso(0), tomorrow, _iso(5)]
        client.times = [_at(tomorrow, "11:30"), _at(tomorrow, "18:00"), _at(tomorrow, "20:00")]
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(_tap(context, "cb:book:pick_master:11:22"))
        # Read back in the user's own words — the request is visibly heard,
        # which is the half of the defect that «молча» names.
        assert result.reply_text == "Вы просили завтра вечером — вот что есть:"
        # And 11:30 — the time the pilot booking actually landed on — is NOT
        # among the offers, because 11:30 is not an evening.
        assert _callbacks(result) == [
            f"cb:book:pick_slot:11:22:{tomorrow}T18:00:00",
            f"cb:book:pick_slot:11:22:{tomorrow}T20:00:00",
        ]

    def test_a_day_the_master_does_not_work_is_said_out_loud(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """Silence is the defect; an honest "no" plus alternatives is not."""
        self._remember(context, TimePreference(day_offset=1, said="завтра"))
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = [_iso(2), _iso(3), _iso(4), _iso(9)]
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(_tap(context, "cb:book:pick_master:11:22"))
        assert result.reply_text == (
            "На завтра у мастера свободного времени нет. Вот ближайшие дни:"
        )
        assert _labels(result)[0] == "Послезавтра"

    def test_a_part_the_day_does_not_have_is_said_out_loud(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """Asked for an evening, offered a morning — but never silently."""
        tomorrow = _iso(1)
        self._remember(
            context, TimePreference(day_offset=1, part=PART_EVENING, said="завтра вечером")
        )
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = [tomorrow]
        client.times = [_at(tomorrow, "09:00"), _at(tomorrow, "13:00")]
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(_tap(context, "cb:book:pick_master:11:22"))
        assert result.reply_text == "Завтра вечером у мастера свободного времени нет. Есть так:"
        assert _labels(result) == ["Утро (до 12:00)", "День (12:00–17:00)", "Точное время"]

    def test_a_stale_preference_does_not_hijack_a_later_booking(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """A «завтра» from an hour ago may mean a different day now, and a
        silently applied stale narrowing is the same class of bug as a
        silently dropped one."""
        context.conversation.skill_state = {
            "time_pref": {
                "day_offset": 1,
                "part": PART_EVENING,
                "said": "завтра вечером",
                "at": "2020-01-01T00:00:00+00:00",
            }
        }
        context.conversation.save(update_fields=["skill_state"])
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = [_iso(0), _iso(1), _iso(2), _iso(4)]
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(_tap(context, "cb:book:pick_master:11:22"))
        assert result.reply_text == "Выберите дату:"
        assert _labels(result)[:3] == ["Сегодня", "Завтра", "Послезавтра"]
