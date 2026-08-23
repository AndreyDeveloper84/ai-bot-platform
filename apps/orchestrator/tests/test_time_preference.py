"""DRF-1325 — the human-time vocabulary and its single set of boundaries.

The defect these tests stand against is concrete: on 2026-08-23 at 17:48 the
owner wrote «хочу на массаж завтра вечером» and the time half of the sentence
was neither used nor asked back about; the booking that came out of that
dialogue stood five days later at 11:30.

Two properties are worth locking:

1. what a person says is understood at all, and
2. «вечер» means the same number of hours everywhere it appears.

The second is why the boundary assertions below spell out hours instead of
comparing one helper to another — a test that asks the code what it thinks
evening is would follow the code into any drift.
"""

from __future__ import annotations

from datetime import date

import pytest

from apps.orchestrator.time_preference import (
    CONTRACT_SLOT_TO_PART,
    PART_DAY,
    PART_EVENING,
    PART_MORNING,
    PART_ORDER,
    TimePreference,
    day_label,
    describe,
    load_time_preference,
    parse_time_preference,
    part_for_hour,
    part_of_iso_datetime,
    resolve_date,
    save_time_preference,
    tenant_zone,
)


class TestPartBoundaries:
    """Утро / день / вечер — stated in hours, in one place, checked here."""

    @pytest.mark.parametrize(
        ("hour", "part"),
        [
            (0, PART_MORNING),
            (6, PART_MORNING),
            (11, PART_MORNING),
            (12, PART_DAY),
            (14, PART_DAY),
            (16, PART_DAY),
            (17, PART_EVENING),
            (19, PART_EVENING),
            (23, PART_EVENING),
        ],
    )
    def test_hour_maps_to_the_stated_part(self, hour: int, part: str) -> None:
        assert part_for_hour(hour) == part

    def test_every_hour_of_the_day_has_a_part(self) -> None:
        """Totality is the anti-drift property.

        A gap in the buckets would not raise — it would return the wrong
        label, which is precisely the failure the ticket describes («вечер»
        that turns out to be 14:30).
        """
        assert {part_for_hour(h) for h in range(24)} == set(PART_ORDER)

    def test_1430_is_not_an_evening(self) -> None:
        """The live symptom, as a test."""
        assert part_of_iso_datetime("2026-08-28T14:30:00") == PART_DAY
        assert part_of_iso_datetime("2026-08-28T14:30:00") != PART_EVENING

    @pytest.mark.parametrize(
        ("value", "part"),
        [
            ("2026-08-24T19:00:00", PART_EVENING),
            ("2026-08-24T19:00:00+03:00", PART_EVENING),
            ("2026-08-24T09:15:00", PART_MORNING),
            ("09:15", PART_MORNING),
            ("18:45", PART_EVENING),
        ],
    )
    def test_slot_strings_are_bucketed(self, value: str, part: str) -> None:
        assert part_of_iso_datetime(value) == part

    @pytest.mark.parametrize("value", ["", "не время", "потом"])
    def test_unreadable_slot_has_no_part(self, value: str) -> None:
        assert part_of_iso_datetime(value) is None

    def test_personal_context_vocabulary_is_fully_mapped(self) -> None:
        """The five-value contract vocabulary must land on these three.

        ``apps/orchestrator/memory_ask.py`` and
        ``apps/persona/memory_extract.py`` write early_morning / morning /
        afternoon / evening / late_evening. If a sixth value ever appears
        there without landing here, «вечер» starts meaning two things again.
        """
        assert set(CONTRACT_SLOT_TO_PART) == {
            "early_morning",
            "morning",
            "afternoon",
            "evening",
            "late_evening",
        }
        assert set(CONTRACT_SLOT_TO_PART.values()) <= set(PART_ORDER)


class TestParsing:
    """What the pilot actually typed, and its near neighbours."""

    def test_the_live_message(self) -> None:
        """«хочу на массаж завтра вечером» — 2026-08-23 17:48:36."""
        pref = parse_time_preference("хочу на массаж ЗАВТРА ВЕЧЕРОМ")
        assert pref is not None
        assert pref.day_offset == 1
        assert pref.part == PART_EVENING

    @pytest.mark.parametrize(
        ("text", "offset"),
        [
            ("запиши на сегодня", 0),
            ("давай завтра", 1),
            ("можно послезавтра", 2),
        ],
    )
    def test_relative_days(self, text: str, offset: int) -> None:
        pref = parse_time_preference(text)
        assert pref is not None and pref.day_offset == offset

    def test_poslezavtra_is_not_read_as_zavtra(self) -> None:
        """Substring order matters: «послезавтра» contains «завтра»."""
        pref = parse_time_preference("послезавтра")
        assert pref is not None and pref.day_offset == 2

    @pytest.mark.parametrize(
        ("text", "part"),
        [
            ("хочу утром", PART_MORNING),
            ("можно днём", PART_DAY),
            ("лучше вечером", PART_EVENING),
            ("после работы", PART_EVENING),
            ("после шести", PART_EVENING),
            ("после 18", PART_EVENING),
            ("после 13", PART_DAY),
            ("поздним вечером", PART_EVENING),
            ("рано утром", PART_MORNING),
        ],
    )
    def test_parts_of_day(self, text: str, part: str) -> None:
        pref = parse_time_preference(text)
        assert pref is not None and pref.part == part

    def test_after_an_hour_goes_through_the_single_boundary(self) -> None:
        """«после 11» is a morning because 11 is, not because of a word list."""
        pref = parse_time_preference("после 11")
        assert pref is not None and pref.part == part_for_hour(11)

    def test_weekday_needs_the_local_weekday(self) -> None:
        """Without today's weekday the branch is skipped, not guessed.

        Guessing would mean reaching for the server's date, which is exactly
        the class of bug this module exists to avoid.
        """
        assert parse_time_preference("в субботу") is None
        # Monday=0 … so on a Monday, Saturday is five days out.
        pref = parse_time_preference("в субботу", weekday_today=0)
        assert pref is not None and pref.day_offset == 5

    def test_named_weekday_is_always_ahead(self) -> None:
        """Said ON a Saturday, «в субботу» means the NEXT one.

        Somebody already inside Saturday says «сегодня».
        """
        pref = parse_time_preference("в субботу", weekday_today=5)
        assert pref is not None and pref.day_offset == 7

    def test_weekend_picks_the_nearer_weekend_day(self) -> None:
        # Wednesday → Saturday is three days out.
        pref = parse_time_preference("давай в выходные", weekday_today=2)
        assert pref is not None and pref.day_offset == 3
        # Already Saturday → Sunday, not next week.
        pref = parse_time_preference("давай в выходные", weekday_today=5)
        assert pref is not None and pref.day_offset == 1

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "запиши на массаж",
            "хочу лимфодренаж",
            "сколько стоит массаж",
        ],
    )
    def test_no_time_means_no_preference(self, text: str) -> None:
        """``None``, not an empty preference — the caller must be able to
        tell "nothing was said" from "something was said and lost"."""
        assert parse_time_preference(text) is None

    def test_day_without_part_and_part_without_day(self) -> None:
        day_only = parse_time_preference("завтра")
        assert day_only is not None
        assert day_only.day_offset == 1 and day_only.part is None
        part_only = parse_time_preference("вечером")
        assert part_only is not None
        assert part_only.day_offset is None and part_only.part == PART_EVENING


class TestRendering:
    TODAY = date(2026, 8, 23)

    def test_relative_words_for_the_first_three_days(self) -> None:
        assert day_label("2026-08-23", self.TODAY) == "Сегодня"
        assert day_label("2026-08-24", self.TODAY) == "Завтра"
        assert day_label("2026-08-25", self.TODAY) == "Послезавтра"

    def test_a_further_day_gets_a_date_not_a_count(self) -> None:
        """«Через 5 дней» is not how anybody reads a calendar."""
        assert day_label("2026-08-28", self.TODAY) == "28 авг (Пт)"

    def test_a_past_date_never_wears_a_relative_word(self) -> None:
        """The server runs on UTC and the salon on Moscow time; between
        midnight and 03:00 local a server-derived window can open on
        yesterday. Whatever else that costs, it must not print «Сегодня»
        over a day that is gone."""
        assert day_label("2026-08-22", self.TODAY) == "22 авг (Сб)"

    def test_readback_uses_the_users_own_words(self) -> None:
        pref = parse_time_preference("завтра вечером")
        assert describe(pref, "2026-08-24", self.TODAY) == "завтра вечером"

    def test_readback_without_a_day(self) -> None:
        pref = parse_time_preference("вечером")
        assert describe(pref, None, self.TODAY) == "вечером"

    def test_resolve_date_counts_from_the_given_today(self) -> None:
        pref = parse_time_preference("завтра вечером")
        assert resolve_date(pref, self.TODAY) == "2026-08-24"

    def test_resolve_date_is_none_without_a_day(self) -> None:
        assert resolve_date(parse_time_preference("вечером"), self.TODAY) is None
        assert resolve_date(None, self.TODAY) is None


class TestTenantZone:
    """Where the local date comes from — and where it does NOT come from."""

    class _Tenant:
        def __init__(self, tz: str) -> None:
            self.timezone = tz

    def test_tenant_timezone_is_used(self) -> None:
        assert str(tenant_zone(self._Tenant("Asia/Yekaterinburg"))) == "Asia/Yekaterinburg"

    def test_missing_tenant_falls_back_to_moscow(self) -> None:
        """The global bot runs tenant-less; the pilot is Europe/Moscow."""
        assert str(tenant_zone(None)) == "Europe/Moscow"

    def test_garbage_timezone_falls_back_rather_than_raising(self) -> None:
        assert str(tenant_zone(self._Tenant("Not/AZone"))) == "Europe/Moscow"


class TestConversationState:
    """Carrying the preference from the turn that said it to the turn that
    uses it — two different Conversation rows, across a tenant boundary."""

    class _Conversation:
        def __init__(self, state: dict | None = None) -> None:
            self.skill_state = state or {}
            self.saved: list[list[str]] = []

        def save(self, update_fields=None):  # noqa: ANN001, ANN202
            self.saved.append(list(update_fields or []))

    @pytest.mark.django_db
    def test_round_trip(self) -> None:
        conv = self._Conversation()
        pref = parse_time_preference("завтра вечером")
        save_time_preference(conv, pref)
        assert conv.saved == [["skill_state"]]
        loaded = load_time_preference(conv)
        assert loaded is not None
        assert loaded.day_offset == 1 and loaded.part == PART_EVENING

    @pytest.mark.django_db
    def test_stale_preference_is_dropped(self) -> None:
        """A «завтра» from an hour ago may well mean a different day now."""
        conv = self._Conversation(
            {
                "time_pref": {
                    "day_offset": 1,
                    "part": PART_EVENING,
                    "at": "2020-01-01T00:00:00+00:00",
                }
            }
        )
        assert load_time_preference(conv) is None

    @pytest.mark.django_db
    def test_corrupt_state_is_ignored_not_raised(self) -> None:
        assert load_time_preference(self._Conversation({"time_pref": "вечером"})) is None
        assert load_time_preference(self._Conversation({"time_pref": {"part": "ночь"}})) is None
        assert load_time_preference(self._Conversation({})) is None
        assert load_time_preference(None) is None

    @pytest.mark.django_db
    def test_saving_none_clears_without_a_write_when_empty(self) -> None:
        conv = self._Conversation()
        save_time_preference(conv, None)
        assert conv.saved == []

    def test_empty_preference_is_falsy(self) -> None:
        assert not TimePreference()
        assert TimePreference(day_offset=0)
        assert TimePreference(part=PART_MORNING)
