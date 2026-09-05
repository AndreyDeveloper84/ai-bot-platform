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

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.core.cache import cache
from freezegun import freeze_time

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


# --- The clock the salon reads ---------------------------------------------
#
# Every «Сегодня» in this file is the SALON's, and the salon is in Moscow.
# Left to the runner's clock these tests assert a condition they never
# created: they asked ``date.today()`` in whatever zone the runner happened
# to sit in, then compared the answer against a product that resolves the day
# in the tenant's zone (``apps.orchestrator.time_preference.local_today``).
# The two agree only while the runner is also Moscow. On a UTC runner the
# file was therefore green by day and red between 21:00 and midnight UTC —
# the hours when the two calendars have already parted. The product was never
# the defect; the assertion was.
#
# So the moment is stated rather than sampled, and it is deliberately a
# moment INSIDE that gap: 21:30 UTC is 00:30 of the NEXT day in Moscow. UTC
# still says the 24th, the salon already says the 25th. A «сегодня» computed
# server-side instead of salon-side now fails this file on every run, in
# every zone, instead of one evening in four.
SALON_TZ = "Europe/Moscow"
FROZEN_NOW = "2026-08-24T21:30:00+00:00"
# Derived, never restated: a hand-written second copy of the same fact is
# free to drift from the first one, and then the file quietly stops testing
# the gap it was written for. Evaluates to 2026-08-25 — the salon's date at
# that instant, one day ahead of UTC's.
SALON_TODAY = datetime.fromisoformat(FROZEN_NOW).astimezone(ZoneInfo(SALON_TZ)).date()


@pytest.fixture(autouse=True)
def _salon_clock():  # noqa: ANN201
    """One answer to «какое сегодня» for the whole test, gap included.

    Freezing also closes a second, rarer hole: an unfrozen run that crosses
    midnight between building the fixture dates and asserting on the labels
    disagrees with itself.
    """
    with freeze_time(FROZEN_NOW):
        yield


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
    # Stated, not inherited: the model default is Moscow today, and a test
    # that leans on a default is one migration away from testing nothing.
    return Tenant.objects.create(slug="booking-time-chips", name="Time Chips", timezone=SALON_TZ)


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
    """A date ``offset`` days from the salon's today.

    Anchored on :data:`SALON_TODAY`, never on ``date.today()``: the runner's
    calendar is not the salon's, and the booking path is right to prefer the
    salon's. See the note beside :data:`FROZEN_NOW`.
    """
    return (SALON_TODAY + timedelta(days=offset)).isoformat()


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
        # DRF-1474 — was «Выберите дату:», the collapsed picker's own header.
        # See test_expansion_does_not_repeat_the_collapsed_header below for
        # the live transcript that reads as the bot saying it twice.
        assert result.reply_text == "Все свободные даты:"
        assert len(_callbacks(result)) == 6
        # Beyond the third day a count stops being readable, so those wear a
        # date — the relative words are a convenience, not a rule.
        assert _labels(result)[:3] == ["Сегодня", "Завтра", "Послезавтра"]
        assert all(w not in _labels(result)[3:] for w in ("Сегодня", "Завтра"))

    def test_expansion_does_not_repeat_the_collapsed_header(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """DRF-1474 — the «Выберите дату:» that arrived twice.

        Live pilot 04.09::

            12:15:13  бот  Выберите дату:   [Сегодня · Завтра · 7 сен · Выбрать дату]
            12:15:17  бот  Выберите дату:   [Сегодня … 17 сен — 12 кнопок]

        Nothing was sent twice and nothing was retried: the second message is
        the answer to a «Выбрать дату» tap. But the transcript shows text, not
        keyboards, so an expansion wearing the collapsed picker's words is
        indistinguishable from a duplicate — and the owner filed it as one.

        The two messages this test builds are the two the owner saw, and the
        assertion is the whole fix: they may not read the same.
        """
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = [_iso(i) for i in range(6)]
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                collapsed = BookingSkill().handle(_tap(context, "cb:book:pick_master:11:22"))
                expanded = BookingSkill().handle(
                    _tap(context, f"{CALLBACK_BOOK_MORE_DATES_PREFIX}11:22")
                )

        assert collapsed.reply_text != expanded.reply_text
        # And the keyboards really did differ — otherwise the right fix would
        # have been to suppress the second message, not to rename it.
        assert len(_callbacks(collapsed)) < len(_callbacks(expanded))


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


class TestDeadEndDaysStillOfferDays:
    """DRF-1490 — «Вот ближайшие дни:» and then no days.

    Two branches say the same sentence about a day that holds nothing. One
    of them (a day the user NAMED that the master does not work,
    ``_render_date_picker``) has always attached the picker the sentence
    promises. The other (a day the user TAPPED whose slots turned out to be
    empty, ``_render_part_picker``) ended on the colon and sent nothing
    under it.

    Nothing pinned either half, which is the only reason the two could
    drift: the tests below assert the sentence AND the keyboard together,
    on both branches, so neither can lose the other again.
    """

    def _remember(self, context: SkillContext, pref: TimePreference) -> None:
        save_time_preference(context.conversation, pref)

    def test_a_tapped_day_with_no_slots_delivers_the_days_it_promises(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """The defect itself: the colon now has a list under it."""
        day = _iso(1)
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        # The day is on the master's calendar — it just has no free times
        # left on it, which is the state that produced the empty message.
        client.dates = [day, _iso(3), _iso(4), _iso(6)]
        client.times = []
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(_tap(context, f"cb:book:pick_date:11:{day}:22"))
        assert result.reply_text == (
            "На завтра у мастера свободного времени нет. Вот ближайшие дни:"
        )
        assert result.action_data is not None
        assert result.action_data["kind"] == "date_pick"
        assert _callbacks(result) == [
            f"cb:book:pick_date:11:{_iso(3)}:22",
            f"cb:book:pick_date:11:{_iso(4)}:22",
            f"cb:book:pick_date:11:{_iso(6)}:22",
        ]

    def test_the_empty_day_is_not_offered_back(self, context: SkillContext, tenant: Tenant) -> None:
        """Offering the dead-ended day again is a loop: the tap lands back
        on the branch that produced this very message."""
        day = _iso(1)
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = [day, _iso(3)]
        client.times = []
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(_tap(context, f"cb:book:pick_date:11:{day}:22"))
        assert f"cb:book:pick_date:11:{day}:22" not in _callbacks(result)
        # Positive half of the same assertion: the days that ARE free did
        # arrive. A "not in" that passes on an empty keyboard tests nothing.
        assert _callbacks(result) == [f"cb:book:pick_date:11:{_iso(3)}:22"]

    def test_a_day_with_slots_is_untouched_by_the_fix(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """The positive guard for the two assertions above.

        Same tap, same master, same service — only the slots differ. A day
        that HAS times must still get the part question and must NOT be
        handed a date picker: the fix may only reach the empty case.
        """
        day = _iso(1)
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = [day, _iso(3), _iso(4), _iso(6)]
        client.times = [_at(day, "09:00"), _at(day, "14:00"), _at(day, "19:00")]
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(_tap(context, f"cb:book:pick_date:11:{day}:22"))
        assert result.reply_text == "Когда удобно завтра?"
        assert result.action_data is not None
        assert result.action_data["kind"] == "part_pick"
        # And no schedule read for other dates was made — the branch that
        # needs them is the empty one, and only it.
        assert client.dates_calls == []

    def test_a_master_with_no_other_day_stops_promising_one(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """A keyboard is the fix, not the goal.

        When the only day the master has is the empty one there is nothing
        to put under a colon, so the sentence must change rather than the
        keyboard appear. Silence is a defect; an unkeepable promise is the
        same defect wearing punctuation.
        """
        day = _iso(1)
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = [day]
        client.times = []
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(_tap(context, f"cb:book:pick_date:11:{day}:22"))
        assert result.reply_text == (
            "На завтра у мастера свободного времени нет, "
            "других свободных дней у него сейчас не вижу. Выберите другого мастера."
        )
        assert "Вот ближайшие дни:" not in result.reply_text
        assert result.action_data is None
        assert result.should_handoff is False

    def test_both_dead_end_branches_answer_the_same_way(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """The two branches that share a sentence must share a keyboard.

        The left-hand result comes from the day the user NAMED («завтра»,
        which the master does not work); the right-hand one from the day
        the user TAPPED whose slots came back empty. Before DRF-1490 the
        first carried a picker and the second carried nothing, and the only
        thing holding the difference in place was that no test looked at
        both at once.
        """
        tomorrow = _iso(1)
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.times = []

        self._remember(context, TimePreference(day_offset=1, said="завтра"))
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                # Branch 1 reaches its «no such day» line only when the day
                # is absent from the calendar entirely.
                client.dates = [_iso(3), _iso(4)]
                named = BookingSkill().handle(_tap(context, "cb:book:pick_master:11:22"))
                # Branch 2: the day exists but holds no times.
                client.dates = [tomorrow, _iso(3), _iso(4)]
                tapped = BookingSkill().handle(_tap(context, f"cb:book:pick_date:11:{tomorrow}:22"))

        assert named.reply_text == tapped.reply_text
        assert named.action_data is not None
        assert tapped.action_data is not None
        assert named.action_data["kind"] == "date_pick"
        assert tapped.action_data["kind"] == "date_pick"
        expected = [
            f"cb:book:pick_date:11:{_iso(3)}:22",
            f"cb:book:pick_date:11:{_iso(4)}:22",
        ]
        assert _callbacks(named) == expected
        assert _callbacks(tapped) == expected


class TestSlotKeyboardHasACeiling:
    """DRF-1490 — the slot picker used to have no upper bound.

    ``_action_data_for_slot_pick`` drew one row per slot and stopped only
    when the MAX transport truncated the payload at
    :data:`~apps.channels.max.outbound.MAX_KEYBOARD_ROWS` rows, logging one
    WARNING nobody reads. The person saw a keyboard that looked whole and
    had no way to know that times had been withheld — the bot understating
    a salon's availability without saying so.

    The date picker has had a cap since it was written. These tests give
    the slot picker the same one, plus the thing a cap alone does not buy:
    a reply that admits it.
    """

    def _grid(self, day: str, count: int, start_hour: int = 9) -> list:
        """``count`` quarter-hourly slots from ``start_hour`` — the shape
        that overflowed on the pilot."""
        out = []
        minutes = start_hour * 60
        for _ in range(count):
            out.append(_at(day, f"{minutes // 60:02d}:{minutes % 60:02d}"))
            minutes += 15
        return out

    def test_a_normal_day_renders_every_slot_and_says_nothing_extra(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """The positive guard: below the cap nothing changed.

        Same code path, same builder, same tap as the overflow test below —
        only the slot count differs. Twenty times still come out as twenty
        buttons under the unchanged header.
        """
        day = _iso(1)
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = [day]
        client.times = self._grid(day, 20)
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(
                    _tap(context, f"{CALLBACK_BOOK_PICK_PART_PREFIX}11:{day}:any:22")
                )
        assert result.reply_text == "Выберите время:"
        assert len(_callbacks(result)) == 20

    def test_an_overlong_day_is_capped_and_the_reply_admits_it(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        day = _iso(1)
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = [day]
        client.times = self._grid(day, 40)
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(
                    _tap(context, f"{CALLBACK_BOOK_PICK_PART_PREFIX}11:{day}:any:22")
                )
        callbacks = _callbacks(result)
        assert len(callbacks) == 24
        # Predictable, not arbitrary: the earliest 24, in order. A cap that
        # kept a random 24 would be as opaque as the transport's truncation.
        assert callbacks[0] == f"cb:book:pick_slot:11:22:{day}T09:00:00"
        assert callbacks[-1] == f"cb:book:pick_slot:11:22:{day}T14:45:00"
        # The half a silent cap does not buy: the person is told the list is
        # partial, and told how to reach the rest.
        assert "Показываю первые 24 из 40" in result.reply_text
        assert "напишите, во сколько вам удобно" in result.reply_text
        # The header the truncated list sits under is still the header.
        assert result.reply_text.startswith("Выберите время:")

    def test_the_cap_keeps_the_keyboard_inside_what_max_accepts(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """The cap exists to make the transport's clamp unreachable.

        Rendering the same buttons through the MAX adapter must not trip
        ``_clamp_keyboard_rows`` — if it does, the tail is still being
        dropped downstream and the note above is describing the wrong
        number.
        """
        from apps.channels.max.outbound import (
            MAX_KEYBOARD_ROWS,
            make_inline_keyboard_attachment,
        )

        day = _iso(1)
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = [day]
        client.times = self._grid(day, 60)
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(
                    _tap(context, f"{CALLBACK_BOOK_PICK_PART_PREFIX}11:{day}:any:22")
                )
        assert result.action_data is not None
        buttons = result.action_data["attachments"][0]["payload"]["buttons"]
        assert len(buttons) <= MAX_KEYBOARD_ROWS
        rows = make_inline_keyboard_attachment(buttons)["payload"]["buttons"]
        assert len(rows) == len(buttons)

    def test_the_cap_reaches_the_narrowed_lists_too(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """A part chip is a smaller list, not an exempt one.

        The «Точное время» path is the obvious overflow, but a salon with a
        long evening can overflow a single bucket as well, and the branch
        that renders it is a different one.
        """
        day = _iso(1)
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = [day]
        # 17:00 → 23:45, quarter-hourly: 28 evening slots, all one bucket.
        client.times = self._grid(day, 28, start_hour=17)
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(
                    _tap(context, f"{CALLBACK_BOOK_PICK_PART_PREFIX}11:{day}:evening:22")
                )
        assert len(_callbacks(result)) == 24
        assert "Показываю первые 24 из 28" in result.reply_text
