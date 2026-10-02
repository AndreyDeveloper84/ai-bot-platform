"""A master's empty day says WHY it is empty (DRF-2759).

### What was wrong

«На DD.MM записей нет.» answered four different facts with one sentence: the
day source knew visits and nothing about the schedule. A master on a day off,
a master whose schedule was never set up and a master nobody booked today all
read the same line — and the greeting said «Сегодня у вас 0 записей.» to all
three.

### The owner's ruling (02.10.2026), verbatim

==============================  =============================================
state                           text
==============================  =============================================
day off by the schedule         «Сегодня у вас выходной по графику.»
schedule not set up             «Ваш рабочий график ещё не настроен.
                                Обратитесь к администратору салона, чтобы
                                его настроить.»
working day, no visits          «Сегодня у вас пока нет записей.»
schedule state unavailable      «Не удалось получить информацию о вашем
                                графике. Попробуйте позже.»
==============================  =============================================

The button under each is «Расписание». No visits does not confirm a day off;
no shift on a date does not mean the schedule is not set up; an unavailable
source is not a zero; existing visits are shown whatever the schedule says.
The greeting and «Мой день» must read the states the same way. The D-1
protection of #2273 (a dead VISIT source is its own answer) stays.

### Where the schedule comes from

Not from ``apps/scheduling``'s local tables — that copy is stale (DRF-2014).
The frame comes through ``master_api.services.dashboard._working_block_today_ex``,
the rule the master's Mini App dashboard already uses to tell «выходной» from
«часы не заданы». The seam for most nodes here is that function; one class
runs the real frame on the local tables (flag OFF) so the wiring is exercised
end to end.

### Limits

* With ``BOOKING_VIA_AYLA_REST`` ON the frame is three REST reads of the
  catalog; nothing here calls the catalog. What the wire answers for a master
  without a schedule (no rows, or seven non-working rows) is not measured —
  both shapes read as «not set up» here.
* A full-day exception (vacation, sick leave, a day off) reads as «выходной
  по графику»: the catalog wire does not name the kind, and the owner gave
  one text.
* «No visits» is decided by the visit source for the button and by the day
  mirror for the greeting; both drop cancelled and no-show visits, an
  unknown status could still make them differ.
"""

from __future__ import annotations

from datetime import time, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.channels.bot_registry import BotEntry
from apps.channels.max import salon_greeting, salon_handler, staff_actions
from apps.scheduling.models import ScheduleException, WorkingHours
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant
from apps.tenancy.timezones import salon_zone

pytestmark = pytest.mark.django_db

FRAME_SEAM = "apps.master_api.services.dashboard._working_block_today_ex"
BLOCK = (time(10, 0), time(19, 0))

WITH_APP = BotEntry(
    slug="salon",
    webhook_secret="secret-salon",  # pragma: allowlist secret
    api_token="token-salon",  # pragma: allowlist secret
    tenant_slug="formula-tela",
    stream="max_salon",
    web_app="ayla_salon_bot",
)
WITHOUT_APP = BotEntry(
    slug="salon",
    webhook_secret="secret-salon",  # pragma: allowlist secret
    api_token="token-salon",  # pragma: allowlist secret
    tenant_slug="formula-tela",
    stream="max_salon",
)
MASTER_ROLE = SimpleNamespace(
    is_owner=False, is_admin=False, is_receptionist=False, is_master=True, primary_role="master"
)


@pytest.fixture
def tenant() -> Tenant:
    obj, _ = Tenant.all_objects.get_or_create(
        slug="formula-tela", defaults={"name": "Формула тела", "timezone": "Europe/Moscow"}
    )
    return obj


@pytest.fixture
def master(tenant: Tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        name="Тихонова Ольга",
        external_id=None,
        external_updated_at=timezone.now(),
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        is_active=True,
    )


@pytest.fixture(autouse=True)
def _fresh_cache():
    cache.clear()
    yield
    cache.clear()


def _frame(monkeypatch, answer) -> list:
    """Stand in for the frame reader; returns the list of calls it received."""

    calls: list = []

    def fake(master, today_local, *, tz):
        calls.append((master.id, today_local))
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(FRAME_SEAM, fake)
    return calls


def _day(tenant: Tenant, master: CatalogMaster) -> str:
    with tenant_scope(tenant):
        return staff_actions.master_day(master)


class TestTheOwnersWords:
    def test_verbatim(self) -> None:
        assert staff_actions.MASTER_DAY_OFF == "Сегодня у вас выходной по графику."
        assert staff_actions.MASTER_SCHEDULE_NOT_SET == (
            "Ваш рабочий график ещё не настроен. "
            "Обратитесь к администратору салона, чтобы его настроить."
        )
        assert staff_actions.MASTER_NO_VISITS == "Сегодня у вас пока нет записей."
        assert staff_actions.MASTER_SCHEDULE_UNAVAILABLE == (
            "Не удалось получить информацию о вашем графике. Попробуйте позже."
        )

    def test_the_four_are_four_different_sentences(self) -> None:
        texts = list(staff_actions.EMPTY_DAY_TEXT.values())
        assert len(texts) == 4
        assert len(set(texts)) == 4
        # …and none of them is the answer for a dead VISIT source (D-1, #2273).
        assert staff_actions.DAY_UNAVAILABLE not in texts


STATES = [
    # (what the frame says, state, the owner's text)
    ((BLOCK, True, True), staff_actions.EMPTY_WORKING, "Сегодня у вас пока нет записей."),
    ((None, True, True), staff_actions.EMPTY_DAY_OFF, "Сегодня у вас выходной по графику."),
    (
        (None, True, False),
        staff_actions.EMPTY_NOT_SET,
        "Ваш рабочий график ещё не настроен. "
        "Обратитесь к администратору салона, чтобы его настроить.",
    ),
    (
        (None, False, False),
        staff_actions.EMPTY_UNKNOWN,
        "Не удалось получить информацию о вашем графике. Попробуйте позже.",
    ),
]


class TestEachStateHasItsOwnAnswer:
    @pytest.mark.parametrize(("frame", "state", "text"), STATES)
    def test_my_day_button(self, monkeypatch, tenant, master, frame, state, text) -> None:
        _frame(monkeypatch, frame)
        assert staff_actions.empty_day_state(master) == state
        assert _day(tenant, master) == text

    def test_custom_hours_today_on_an_empty_template_is_a_working_day(
        self, monkeypatch, tenant, master
    ) -> None:
        """No weekly hours, but a shift set for today: «not set up» would be false."""
        _frame(monkeypatch, (BLOCK, True, False))
        assert _day(tenant, master) == staff_actions.MASTER_NO_VISITS

    def test_a_frame_that_raises_is_unknown_not_a_crash(self, monkeypatch, tenant, master) -> None:
        _frame(monkeypatch, RuntimeError("catalog is down"))
        assert _day(tenant, master) == staff_actions.MASTER_SCHEDULE_UNAVAILABLE

    def test_unknown_is_neither_a_day_off_nor_not_set_up(self, monkeypatch, master) -> None:
        """«Не знаю» must not be turned into a statement about the schedule."""
        _frame(monkeypatch, (None, True, False))
        assert staff_actions.empty_day_state(master) == staff_actions.EMPTY_NOT_SET
        cache.clear()
        _frame(monkeypatch, (None, False, False))
        assert staff_actions.empty_day_state(master) == staff_actions.EMPTY_UNKNOWN


class TestVisitsComeFirst:
    def test_existing_visits_are_shown_and_the_schedule_is_not_asked(
        self, monkeypatch, tenant, master
    ) -> None:
        """«Показывать и при конфликте графика» — by construction: the frame is not read."""
        calls = _frame(monkeypatch, (None, True, True))  # the schedule says: day off
        # Positive pair first: with no visits this frame IS read and answers «выходной».
        monkeypatch.setattr(
            "apps.master_api.services.visit_source.master_visits", lambda *a, **kw: []
        )
        assert _day(tenant, master) == staff_actions.MASTER_DAY_OFF
        assert len(calls) == 1

        cache.clear()  # so that a second read, if it happened, would be counted
        visit = SimpleNamespace(visit_at=timezone.now(), service_name="массаж", client_name="Мария")
        monkeypatch.setattr(
            "apps.master_api.services.visit_source.master_visits", lambda *a, **kw: [visit]
        )
        text = _day(tenant, master)
        assert "Мария" in text and "массаж" in text
        assert len(calls) == 1

    def test_a_dead_visit_source_keeps_its_own_answer(self, monkeypatch, tenant, master) -> None:
        """D-1 (#2273) stays: no visits source — no claim about the schedule either."""
        calls = _frame(monkeypatch, (BLOCK, True, True))
        # Positive pair first: with a live, empty visit source the frame is read.
        monkeypatch.setattr(
            "apps.master_api.services.visit_source.master_visits", lambda *a, **kw: []
        )
        assert _day(tenant, master) == staff_actions.MASTER_NO_VISITS
        assert len(calls) == 1

        cache.clear()

        def boom(*a, **kw):
            raise RuntimeError("mirror down")

        monkeypatch.setattr("apps.master_api.services.visit_source.master_visits", boom)
        assert _day(tenant, master) == staff_actions.DAY_UNAVAILABLE
        assert len(calls) == 1


class TestTheRealFrame:
    """Flag OFF — the frame reads the local tables. The wiring, end to end."""

    @pytest.fixture(autouse=True)
    def _local_frame(self, settings):
        settings.BOOKING_VIA_AYLA_REST = False

    @staticmethod
    def _today(tenant: Tenant):
        return timezone.now().astimezone(salon_zone(tenant)).date()

    @staticmethod
    def _hours(tenant, master, weekday: int, *, working: bool) -> None:
        WorkingHours.all_tenants.create(
            tenant=tenant,
            master=master,
            day_of_week=weekday,
            is_working=working,
            start_time=time(10, 0) if working else None,
            end_time=time(19, 0) if working else None,
        )

    def test_no_weekly_hours_at_all_is_not_set_up(self, tenant, master) -> None:
        assert _day(tenant, master) == staff_actions.MASTER_SCHEDULE_NOT_SET

    def test_seven_non_working_rows_are_not_a_schedule_either(self, tenant, master) -> None:
        for weekday in range(7):
            self._hours(tenant, master, weekday, working=False)
        assert _day(tenant, master) == staff_actions.MASTER_SCHEDULE_NOT_SET

    def test_a_shift_today_and_nobody_booked(self, tenant, master) -> None:
        self._hours(tenant, master, self._today(tenant).weekday(), working=True)
        assert _day(tenant, master) == staff_actions.MASTER_NO_VISITS

    def test_no_shift_today_but_the_week_has_working_days(self, tenant, master) -> None:
        """«Отсутствие смены на дату ≠ ненастроенный график»."""
        today = self._today(tenant).weekday()
        self._hours(tenant, master, (today + 1) % 7, working=True)
        assert _day(tenant, master) == staff_actions.MASTER_DAY_OFF

    def test_a_full_day_exception_on_a_working_weekday_is_a_day_off(self, tenant, master) -> None:
        today = self._today(tenant)
        self._hours(tenant, master, today.weekday(), working=True)
        assert _day(tenant, master) == staff_actions.MASTER_NO_VISITS
        cache.clear()
        ScheduleException.all_tenants.create(
            tenant=tenant, master=master, date=today, type=ScheduleException.Type.VACATION
        )
        assert _day(tenant, master) == staff_actions.MASTER_DAY_OFF


class TestTheFrameIsNotReadTwice:
    def test_the_cache_window_is_a_decided_number(self) -> None:
        assert staff_actions.EMPTY_DAY_CACHE_SECONDS == 120

    def test_a_read_state_is_kept_for_the_next_question(self, monkeypatch, tenant, master) -> None:
        calls = _frame(monkeypatch, (None, True, True))
        assert _day(tenant, master) == staff_actions.MASTER_DAY_OFF
        assert _day(tenant, master) == staff_actions.MASTER_DAY_OFF
        assert len(calls) == 1

    def test_unavailable_is_asked_again(self, monkeypatch, tenant, master) -> None:
        """A failure must not be remembered: the next question has to ask the source."""
        calls = _frame(monkeypatch, (None, False, False))
        assert _day(tenant, master) == staff_actions.MASTER_SCHEDULE_UNAVAILABLE
        assert _day(tenant, master) == staff_actions.MASTER_SCHEDULE_UNAVAILABLE
        assert len(calls) == 2
        # …and once the source answers, the answer is the real one.
        _frame(monkeypatch, (BLOCK, True, True))
        assert _day(tenant, master) == staff_actions.MASTER_NO_VISITS

    def test_one_masters_state_is_not_another_masters(self, monkeypatch, tenant, master) -> None:
        other = CatalogMaster.all_tenants.create(
            tenant=tenant,
            name="Архипкин Денис",
            external_id=None,
            external_updated_at=timezone.now(),
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            is_active=True,
        )
        _frame(monkeypatch, (None, True, True))
        assert _day(tenant, master) == staff_actions.MASTER_DAY_OFF
        _frame(monkeypatch, (BLOCK, True, True))
        assert _day(tenant, other) == staff_actions.MASTER_NO_VISITS


def _buttons(attachments) -> list[dict]:
    out: list[dict] = []
    for attachment in attachments or []:
        for row in (attachment.get("payload") or {}).get("buttons") or []:
            out.extend(row)
    return out


class TestTheScheduleButton:
    @pytest.mark.parametrize("text", list(staff_actions.EMPTY_DAY_TEXT.values()))
    def test_every_empty_day_answer_carries_it_first(self, text: str) -> None:
        buttons = _buttons(salon_handler._after_action_attachments(text, MASTER_ROLE, WITH_APP))
        assert buttons[0]["text"] == "Расписание"
        # The existing route of the master's Mini App — no new right, no new screen.
        assert buttons[0]["type"] == "open_app"
        assert buttons[0]["payload"] == "open_master_schedule"
        # The menu stays under it: the answer must not lock the person in.
        assert [b["text"] for b in buttons[1:]] == ["📅 Мой день", "🏠 Мой кабинет"]

    def test_a_day_with_visits_does_not_get_it(self) -> None:
        buttons = _buttons(
            salon_handler._after_action_attachments("*02.10* — 1:", MASTER_ROLE, WITH_APP)
        )
        assert [b["text"] for b in buttons] == ["📅 Мой день", "🏠 Мой кабинет"]

    def test_the_dead_visit_source_does_not_get_it_either(self) -> None:
        """«Расписание» answers «why is my day empty»; a dead mirror is not that question."""
        buttons = _buttons(
            salon_handler._after_action_attachments(
                staff_actions.DAY_UNAVAILABLE, MASTER_ROLE, WITH_APP
            )
        )
        assert [b["text"] for b in buttons] == ["📅 Мой день", "🏠 Мой кабинет"]

    def test_a_bot_without_a_mini_app_has_no_door_to_draw(self) -> None:
        with_app = _buttons(
            salon_handler._after_action_attachments(
                staff_actions.MASTER_DAY_OFF, MASTER_ROLE, WITH_APP
            )
        )
        assert with_app[0]["text"] == "Расписание"
        without = _buttons(
            salon_handler._after_action_attachments(
                staff_actions.MASTER_DAY_OFF, MASTER_ROLE, WITHOUT_APP
            )
        )
        assert [b["text"] for b in without] == ["📅 Мой день"]


class TestTheGreetingReadsTheSameStates:
    @pytest.mark.parametrize(("frame", "state", "text"), STATES)
    def test_the_greeting_line_is_the_buttons_answer(
        self, monkeypatch, tenant, master, frame, state, text
    ) -> None:
        _frame(monkeypatch, frame)
        role = SimpleNamespace(is_master=True, master_id=master.id)
        with tenant_scope(tenant):
            line = salon_greeting._my_empty_day(role, timezone.now())
        assert line == text
        cache.clear()
        assert line == _day(tenant, master)

    def test_the_schedule_is_read_only_when_the_day_is_empty(
        self, monkeypatch, tenant, master
    ) -> None:
        """The frame costs three catalog reads; a greeting on a busy day must not pay them."""
        from datetime import date

        from apps.admin_api.services.salon_day import DayMaster, DaySummary, DayVisit, SalonDay

        now = timezone.now().astimezone(salon_zone(tenant))

        def day_with(n: int) -> SalonDay:
            visits = [
                DayVisit(
                    id=str(uuid4()),
                    start_at=now + timedelta(hours=1 + i),
                    end_at=now + timedelta(hours=2 + i),
                    duration_min=60,
                    status="confirmed",
                    service_id="svc",
                    service_name="массаж",
                    client_first_name="Мария",
                    client_last_initial="К.",
                    is_in_progress=False,
                )
                for i in range(n)
            ]
            return SalonDay(
                date=date(now.year, now.month, now.day),
                timezone_name="Europe/Moscow",
                masters=[
                    DayMaster(
                        master_id=str(master.id), name=master.name, is_active=True, visits=visits
                    )
                ],
                summary=DaySummary(total=n, upcoming=n, completed=0, released=0),
            )

        calls = _frame(monkeypatch, (None, True, True))
        role = SimpleNamespace(is_master=True, is_owner=False, is_admin=False, master_id=master.id)

        # Positive pair first: an empty day DOES read the frame, once.
        monkeypatch.setattr(salon_greeting, "_salon_day", lambda tenant, now: day_with(0))
        empty = salon_greeting.gather(tenant, role, now=now)
        assert empty.my_records == 0
        assert empty.my_empty_day == staff_actions.MASTER_DAY_OFF
        assert len(calls) == 1

        cache.clear()  # so that a second read, if it happened, would be counted
        monkeypatch.setattr(salon_greeting, "_salon_day", lambda tenant, now: day_with(2))
        busy = salon_greeting.gather(tenant, role, now=now)
        assert busy.my_records == 2
        assert busy.my_empty_day is None
        assert len(calls) == 1

    def test_zero_visits_reads_as_the_state_not_as_a_zero(self) -> None:
        data = salon_greeting.GreetingData(my_records=0, my_empty_day=staff_actions.MASTER_DAY_OFF)
        text = salon_greeting.render_master("Ольга", "Формула тела", data)
        assert text.splitlines()[-1] == "Сегодня у вас выходной по графику."
        assert "0 записей" not in text

    def test_a_day_with_visits_keeps_the_owners_count_line(self) -> None:
        data = salon_greeting.GreetingData(my_records=3)
        text = salon_greeting.render_master("Ольга", "Формула тела", data)
        assert text.splitlines()[-1] == "Сегодня у вас 3 записи."

    def test_an_unknown_master_card_drops_the_line_rather_than_guessing(self, tenant) -> None:
        role = SimpleNamespace(is_master=True, master_id=uuid4())
        with tenant_scope(tenant):
            line = salon_greeting._my_empty_day(role, timezone.now() + timedelta(0))
        assert line is None
