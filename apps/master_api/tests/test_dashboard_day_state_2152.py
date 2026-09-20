"""Дашборд мастера — состояние дня для «Сегодня» по макету DRF-1182 (DRF-2152, М-1).

Что экрану нужно от сервера и чего у него не было:

* у ближайшей записи — ``end_at`` (начало–конец, а не «N мин») и
  ``minutes_until`` («до визита N мин» — с сервера, как и ``minutes_remaining``:
  на экране ничего не тикает);
* остальные записи дня после ближайшей — ``upcoming_today`` (имя, услуга,
  начало–конец), «спокойнее» ниже;
* состояние дня — ``states.day_off``: ``True`` выходной (рамка дня прочитана и
  пуста), ``False`` рабочий, ``None`` — рамка не прочитана (каталог не ответил):
  «не знаю» ≠ «выходной» (DRF-1111), экран в этом случае выходного не рисует.

Карточка записи — имя, услуга, время: набор полей ``upcoming_today`` закрыт,
телефона/цены/оплаты/источника в нём нет (сторож на ключи).
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

from apps.catalog.models import CatalogMaster
from apps.master_api.services import dashboard as ds
from apps.master_api.tests.test_dashboard import _make_booking, _utc
from apps.scheduling.models import WorkingHours
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

MSK = ZoneInfo("Europe/Moscow")


def _thursday_hours(tenant: Tenant, master: CatalogMaster) -> None:
    # 2026-05-21 is a Thursday.
    WorkingHours.all_tenants.create(
        tenant=tenant,
        master=master,
        day_of_week=3,
        is_working=True,
        start_time=time(10, 0),
        end_time=time(20, 0),
    )


class TestNextVisitEndAndCountdown:
    def test_next_visit_carries_end_at_and_minutes_until(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 11, 15, tzinfo=MSK))
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 12, 0, tzinfo=MSK),
            duration_min=90,
        )

        nxt = ds.get_next_visit(accepted_master, now)

        assert nxt is not None
        assert (
            nxt.visit_at
            == datetime(2026, 5, 21, 12, 0, tzinfo=MSK).astimezone(timezone.utc).isoformat()
        )
        assert (
            nxt.end_at
            == datetime(2026, 5, 21, 13, 30, tzinfo=MSK).astimezone(timezone.utc).isoformat()
        )
        assert nxt.minutes_until == 45

    def test_minutes_until_never_negative(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        # The next visit is the first one not yet started; a visit that
        # starts exactly «now» is 0 minutes away, not −0.
        now = _utc(datetime(2026, 5, 21, 12, 0, tzinfo=MSK))
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 12, 0, tzinfo=MSK),
            duration_min=60,
        )
        nxt = ds.get_next_visit(accepted_master, now)
        assert nxt is None or nxt.minutes_until >= 0


class TestUpcomingToday:
    def test_rest_of_the_day_after_the_next_visit(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 9, 0, tzinfo=MSK))
        for hour, name in ((12, "Анна Петрова"), (14, "Борис Кузнецов"), (16, "Вера Соколова")):
            _make_booking(
                tenant=tenant,
                master=accepted_master,
                visit_local=datetime(2026, 5, 21, hour, 0, tzinfo=MSK),
                duration_min=60,
                client_name=name,
            )

        snap = ds.build_dashboard(accepted_master, now)

        assert snap.next_visit is not None and snap.next_visit.client_first_name == "Анна"
        assert [v.client_first_name for v in snap.upcoming_today] == ["Борис", "Вера"]
        first = snap.upcoming_today[0]
        assert first.visit_at < first.end_at
        assert first.service_name

    def test_card_fields_are_closed_no_phone_price_payment_source(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        """Сторож набора полей карточки (макет: имя, услуга, время)."""
        now = _utc(datetime(2026, 5, 21, 9, 0, tzinfo=MSK))
        for hour in (12, 14):
            _make_booking(
                tenant=tenant,
                master=accepted_master,
                visit_local=datetime(2026, 5, 21, hour, 0, tzinfo=MSK),
                duration_min=60,
            )
        payload = ds.build_dashboard(accepted_master, now).to_dict()
        rows = payload["upcoming_today"]
        assert len(rows) == 1
        assert set(rows[0]) == {
            "booking_id",
            "client_first_name",
            "client_last_initial",
            "service_name",
            "visit_at",
            "end_at",
        }

    def test_empty_when_only_the_next_visit_exists(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 9, 0, tzinfo=MSK))
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 12, 0, tzinfo=MSK),
            duration_min=60,
        )
        snap = ds.build_dashboard(accepted_master, now)
        assert snap.next_visit is not None
        assert snap.upcoming_today == []


class TestDayOff:
    def test_working_day_is_not_day_off(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        _thursday_hours(tenant, accepted_master)
        now = _utc(datetime(2026, 5, 21, 9, 0, tzinfo=MSK))
        assert ds.get_states(accepted_master, now).day_off is False

    def test_no_hours_today_is_day_off(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        # Hours on Thursday only; 2026-05-24 is a Sunday.
        _thursday_hours(tenant, accepted_master)
        now = _utc(datetime(2026, 5, 24, 9, 0, tzinfo=MSK))
        assert ds.get_states(accepted_master, now).day_off is True

    def test_unreadable_frame_is_unknown_not_day_off(
        self, tenant: Tenant, accepted_master: CatalogMaster, monkeypatch
    ) -> None:
        """«Не знаю» ≠ «выходной» (DRF-1111): каталог не ответил → None."""
        from apps.integrations.ayla.salon_client import SalonUnavailable

        def _boom(*a, **kw):
            raise SalonUnavailable("down")

        monkeypatch.setattr(ds, "load_day_frame", _boom)
        now = _utc(datetime(2026, 5, 21, 9, 0, tzinfo=MSK))
        assert ds.get_states(accepted_master, now).day_off is None

    def test_payload_carries_day_off_and_upcoming(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        now = _utc(datetime(2026, 5, 24, 9, 0, tzinfo=MSK))
        payload = ds.build_dashboard(accepted_master, now).to_dict()
        assert "day_off" in payload["states"]
        assert payload["upcoming_today"] == []
