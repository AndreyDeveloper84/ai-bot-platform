"""DRF-2595 — приветствие салона считало «сейчас» в UTC у салона без пояса.

``salon_greeting._tenant_now`` (до сведения): пусто → ``"UTC"``, битое имя →
``timezone.now()``, то есть тоже UTC. Одно значение портило видимое двумя
способами — и узлов два, по одному на каждый:

1. **час на экране** — строка «следующий визит» в приветствии мастера:
   ``start_at.astimezone(now.tzinfo)``; у МСК-салона на три часа раньше;
2. **какой день «сегодня»** — ``build_salon_day(day=now.date())``: с 00:00 до
   03:00 МСК дата по UTC вчерашняя, и счётчик записей выходил за вчера.

Сводка владельца (``salon_digest``) дефекта не показывала: она передаёт
``now`` сама, уже в поясе салона, — поэтому он и не был замечен.
"""

from __future__ import annotations

from datetime import date, datetime, timezone as dt_timezone
from types import SimpleNamespace

import pytest

from apps.admin_api.services.salon_day import DayMaster
from apps.channels.max import salon_greeting
from apps.channels.tests.test_salon_greeting_2114 import _day, _visit
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

#: 01:30 по Москве 19.09 — это 22:30 UTC 18.09: край «после полуночи».
CLOCK_UTC = datetime(2026, 9, 18, 22, 30, tzinfo=dt_timezone.utc)
#: Визит в 09:00 по Москве.
VISIT_UTC = datetime(2026, 9, 19, 6, 0, tzinfo=dt_timezone.utc)

MASTER = SimpleNamespace(is_master=True, master_id="m-2595", is_owner=False, is_admin=False)


@pytest.fixture
def seen(monkeypatch) -> dict:
    captured: dict = {}

    def _salon_day(tenant, now):
        captured["day"] = now.date()
        return _day(
            [DayMaster(master_id="m-2595", name="Анна", is_active=True, visits=[_visit(VISIT_UTC)])]
        )

    monkeypatch.setattr(salon_greeting.timezone, "now", lambda: CLOCK_UTC)
    monkeypatch.setattr(salon_greeting, "_salon_day", _salon_day)
    monkeypatch.setattr(salon_greeting, "_masters_available", lambda: 1)
    monkeypatch.setattr(salon_greeting, "_attention", lambda: 0)
    return captured


@pytest.mark.parametrize("zone", ["", "Not/AZone"], ids=["empty", "broken"])
class TestAZonelessSalonGreetsInItsOwnHour:
    def _gather(self, zone: str):
        slug = {"": "greet-empty", "Not/AZone": "greet-broken"}[zone]
        tenant = Tenant.objects.create(slug=slug, name="Салон", timezone=zone)
        return salon_greeting.gather(tenant, MASTER)

    def test_the_next_visit_hour_is_the_salons(self, seen, zone) -> None:
        data = self._gather(zone)
        assert data.next_visit is not None
        assert data.next_visit.time == "09:00"

    def test_today_is_the_salons_today_after_midnight(self, seen, zone) -> None:
        self._gather(zone)
        assert seen["day"] == date(2026, 9, 19)
