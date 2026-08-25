"""DRF-1126: the master screen's day frame comes from Ayla when the flag is ON.

The defect: the salon edits the schedule in Ayla, the client's slot supply
already reads it there (PR #1186), and the master's own screen kept reading
the local ``apps.scheduling`` tables nobody updates anymore. These tests pin
the switch and the wire mapping — including the failure modes, where the
frame must REFUSE rather than guess.
"""

import datetime as dt
import uuid
from zoneinfo import ZoneInfo

import pytest
from django.test import override_settings

from apps.catalog.models import CatalogMaster
from apps.integrations.ayla.salon_client import SalonNotConfigured, SalonUnavailable
from apps.master_api.services import schedule_frame
from apps.master_api.services.schedule import build_schedule
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

MSK = ZoneInfo("Europe/Moscow")
FROM = dt.date(2026, 8, 24)  # Monday
TO = dt.date(2026, 8, 25)


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="frame-salon", name="Формула тела", timezone="Europe/Moscow")


@pytest.fixture
def master(tenant: Tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=7,
        external_updated_at=dt.datetime.now(tz=dt.timezone.utc),
        name="Тихонова Ольга",
    )


class FakeSalonClient:
    """Answers the three frame reads from scripted rows."""

    def __init__(self, *, template=None, exceptions=None, time_off=None, exc=None):
        self._template = template or []
        self._exceptions = exceptions or []
        self._time_off = time_off or []
        self._exc = exc

    def get_master_schedule(self, **kwargs):
        if self._exc:
            raise self._exc
        return self._template

    def list_schedule_exceptions(self, **kwargs):
        return self._exceptions

    def list_time_off(self, **kwargs):
        return self._time_off


def _staff(tenant: Tenant) -> None:
    """An active owner — the person Ayla checks the read's rights against."""
    from apps.identity.models import BotUser

    bot_user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"owner-{uuid.uuid4().hex[:8]}",
        ayla_user_id=str(uuid.uuid4()),
    )
    TenantStaff.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        role=TenantStaff.Role.OWNER,
    )


def _working_template() -> list[dict]:
    return [
        {
            "day_of_week": i,
            "day_name": "",
            "is_working_day": i != 6,  # Sunday off
            "start_time": "10:00" if i != 6 else None,
            "end_time": "19:00" if i != 6 else None,
            "break_start": None,
            "break_end": None,
        }
        for i in range(7)
    ]


def _use_client(monkeypatch, client) -> None:
    monkeypatch.setattr(schedule_frame, "get_salon_client", lambda: client)


class TestLocalSourceUnchangedWhenFlagOff:
    def test_flag_off_reads_local_tables(self, tenant: Tenant, master: CatalogMaster) -> None:
        from apps.scheduling.models import WorkingHours

        WorkingHours.all_tenants.create(
            tenant=tenant,
            master=master,
            day_of_week=0,
            is_working=True,
            start_time=dt.time(9, 0),
            end_time=dt.time(17, 0),
        )
        with override_settings(BOOKING_VIA_AYLA_REST=False):
            wh, exc, extra = schedule_frame.load_day_frame(
                master, from_date=FROM, to_date=TO, tz=MSK
            )
        assert wh[0].start_time == dt.time(9, 0)
        assert exc == {}
        assert extra == {}


class TestAylaSourceWhenFlagOn:
    def test_template_and_exceptions_come_from_the_wire(
        self, tenant: Tenant, master: CatalogMaster, monkeypatch
    ) -> None:
        _staff(tenant)
        client = FakeSalonClient(
            template=_working_template(),
            exceptions=[
                {
                    "id": "e-1",
                    "date": "2026-08-24",
                    "is_working_day": True,
                    "start_time": "12:00",
                    "end_time": "16:00",
                    "break_start": None,
                    "break_end": None,
                    "note": "",
                }
            ],
        )
        _use_client(monkeypatch, client)
        with override_settings(BOOKING_VIA_AYLA_REST=True):
            wh, exc, extra = schedule_frame.load_day_frame(
                master, from_date=FROM, to_date=TO, tz=MSK
            )
        # Template: Monday working 10–19, Sunday off.
        assert wh[0].is_working and str(wh[0].start_time) == "10:00:00"
        assert wh[6].is_working is False
        # The per-date override wins the 24th with custom hours.
        assert str(exc[dt.date(2026, 8, 24)].start_time) == "12:00:00"
        assert extra == {}

    def test_full_day_exception_is_a_day_off_without_a_kind_lie(
        self, tenant: Tenant, master: CatalogMaster, monkeypatch
    ) -> None:
        """The wire carries no exception KIND — the frame reports the M3
        ``other`` (EVENT), never a guessed «personal»/«vacation»."""
        from apps.scheduling.models import ScheduleException

        _staff(tenant)
        client = FakeSalonClient(
            template=_working_template(),
            exceptions=[
                {
                    "id": "e-2",
                    "date": "2026-08-24",
                    "is_working_day": False,
                    "start_time": None,
                    "end_time": None,
                    "break_start": None,
                    "break_end": None,
                    "note": "отпуск",
                }
            ],
        )
        _use_client(monkeypatch, client)
        with override_settings(BOOKING_VIA_AYLA_REST=True):
            _, exc, _ = schedule_frame.load_day_frame(master, from_date=FROM, to_date=TO, tz=MSK)
        assert exc[dt.date(2026, 8, 24)].type == ScheduleException.Type.EVENT

    def test_time_off_partial_becomes_a_block_and_full_day_a_day_off(
        self, tenant: Tenant, master: CatalogMaster, monkeypatch
    ) -> None:
        _staff(tenant)
        client = FakeSalonClient(
            template=_working_template(),
            time_off=[
                {  # «ушла раньше» on Monday: 15:00–19:00 MSK
                    "id": "t-1",
                    "start_at": "2026-08-24T15:00:00+03:00",
                    "end_at": "2026-08-24T19:00:00+03:00",
                    "reason": "",
                    "created_at": "2026-08-24T10:00:00+03:00",
                },
                {  # all of Tuesday
                    "id": "t-2",
                    "start_at": "2026-08-25T00:00:00+03:00",
                    "end_at": "2026-08-25T23:59:59+03:00",
                    "reason": "",
                    "created_at": "2026-08-24T10:00:00+03:00",
                },
            ],
        )
        _use_client(monkeypatch, client)
        with override_settings(BOOKING_VIA_AYLA_REST=True):
            _, exc, extra = schedule_frame.load_day_frame(
                master, from_date=FROM, to_date=TO, tz=MSK
            )
        monday = extra[dt.date(2026, 8, 24)]
        assert [(str(b.start_local), str(b.end_local)) for b in monday] == [
            ("15:00:00", "19:00:00")
        ]
        assert dt.date(2026, 8, 25) in exc  # full-day absence → day off


class TestFailureModesRefuseInsteadOfGuessing:
    def test_no_staff_actor_is_a_config_refusal_not_an_empty_frame(
        self, tenant: Tenant, master: CatalogMaster, monkeypatch
    ) -> None:
        _use_client(monkeypatch, FakeSalonClient(template=_working_template()))
        with override_settings(BOOKING_VIA_AYLA_REST=True):
            with pytest.raises(SalonNotConfigured):
                schedule_frame.load_day_frame(master, from_date=FROM, to_date=TO, tz=MSK)

    def test_outage_propagates(self, tenant: Tenant, master: CatalogMaster, monkeypatch) -> None:
        _staff(tenant)
        _use_client(monkeypatch, FakeSalonClient(exc=SalonUnavailable("network: boom")))
        with override_settings(BOOKING_VIA_AYLA_REST=True):
            with pytest.raises(SalonUnavailable):
                schedule_frame.load_day_frame(master, from_date=FROM, to_date=TO, tz=MSK)

    def test_an_unparseable_wire_row_is_loud(
        self, tenant: Tenant, master: CatalogMaster, monkeypatch
    ) -> None:
        _staff(tenant)
        bad = _working_template()
        bad[0]["start_time"] = "soon-ish"
        _use_client(monkeypatch, FakeSalonClient(template=bad))
        with override_settings(BOOKING_VIA_AYLA_REST=True):
            with pytest.raises(SalonUnavailable):
                schedule_frame.load_day_frame(master, from_date=FROM, to_date=TO, tz=MSK)


class TestBuildScheduleEndToEnd:
    def test_screen_frame_follows_ayla_not_the_local_stub(
        self, tenant: Tenant, master: CatalogMaster, monkeypatch
    ) -> None:
        """The defect itself: local says 10–19 seven days (the DRF-1050
        stub), Ayla says Monday 12–16 — the screen must show Ayla's."""
        from apps.scheduling.models import WorkingHours

        for dow in range(7):
            WorkingHours.all_tenants.create(
                tenant=tenant,
                master=master,
                day_of_week=dow,
                is_working=True,
                start_time=dt.time(10, 0),
                end_time=dt.time(19, 0),
            )
        _staff(tenant)
        _use_client(
            monkeypatch,
            FakeSalonClient(
                template=[
                    {
                        "day_of_week": 0,
                        "day_name": "",
                        "is_working_day": True,
                        "start_time": "12:00",
                        "end_time": "16:00",
                        "break_start": None,
                        "break_end": None,
                    }
                ]
                + [
                    {
                        "day_of_week": i,
                        "day_name": "",
                        "is_working_day": False,
                        "start_time": None,
                        "end_time": None,
                        "break_start": None,
                        "break_end": None,
                    }
                    for i in range(1, 7)
                ],
            ),
        )
        with override_settings(BOOKING_VIA_AYLA_REST=True):
            payload = build_schedule(master, from_date=FROM, to_date=FROM)
        day = payload.days[0]
        assert day.working_hours == {"start": "12:00", "end": "16:00"}
