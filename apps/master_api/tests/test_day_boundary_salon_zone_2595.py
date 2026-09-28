"""DRF-2595 — сутки мастера режутся по поясу салона, а не по UTC.

У салона с пустым ``timezone`` правило расписания мастера
(``schedule.get_tenant_tz``) молча давало UTC, а семь соседних правил —
Москву. Визит в 01:30 по Москве — это 22:30 UTC НАКАНУНЕ: по UTC он уезжал
в предыдущий день. Час на проводе после DRF-2589 верный, день — чужой.

Узел на ЧАС этого не видит и не должен: здесь узел на ДАТУ. Пара, которая
обязана различаться — день визита и день накануне.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from apps.catalog.models import CatalogMaster
from apps.master_api.services import schedule as sched
from apps.master_api.tests.test_schedule import _make_booking, workdays  # noqa: F401
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

MSK = ZoneInfo("Europe/Moscow")


@pytest.fixture
def zoneless(tenant: Tenant) -> Tenant:
    """Салон без пояса: так бывает, когда поле очищено вручную."""
    Tenant.objects.filter(pk=tenant.pk).update(timezone="")
    tenant.refresh_from_db()
    return tenant


def test_a_visit_after_midnight_stays_in_its_own_day(
    zoneless: Tenant,
    accepted_master: CatalogMaster,
    workdays,  # noqa: F811
) -> None:
    accepted_master.refresh_from_db()
    _make_booking(
        tenant=zoneless,
        master=accepted_master,
        visit_local=datetime(2026, 5, 21, 1, 30, tzinfo=MSK),  # 22:30 UTC 20.05
    )

    result = sched.build_schedule(
        accepted_master,
        from_date=date(2026, 5, 20),
        to_date=date(2026, 5, 21),
        now=datetime(2026, 5, 19, 9, 0, tzinfo=MSK),
    )

    by_day = {d.date: len(d.bookings) for d in result.days}
    # Пара: визит — в своём дне 21.05, а не накануне 20.05.
    assert by_day == {"2026-05-20": 0, "2026-05-21": 1}
    assert result.tenant_tz == "Europe/Moscow"


def test_both_master_rules_and_the_salon_day_agree_on_a_zoneless_salon(
    zoneless: Tenant,
) -> None:
    """Одно правило, а не три: расписание, день мастера и день салона."""
    from apps.admin_api.services.salon_day import tenant_tz
    from apps.master_api.services.dashboard import get_tenant_tz as dashboard_tz
    from apps.master_api.services.schedule import get_tenant_tz as schedule_tz

    zones = {str(schedule_tz(zoneless)), str(dashboard_tz(zoneless)), str(tenant_tz(zoneless))}
    assert zones == {"Europe/Moscow"}


def test_a_real_zone_is_still_honoured(tenant: Tenant) -> None:
    Tenant.objects.filter(pk=tenant.pk).update(timezone="Asia/Yekaterinburg")
    tenant.refresh_from_db()
    assert str(sched.get_tenant_tz(tenant)) == "Asia/Yekaterinburg"
    # И запасной не подменяет настоящий пояс: момент тот же, день тот же.
    moment = datetime(2026, 5, 20, 22, 30, tzinfo=ZoneInfo("UTC"))
    assert moment.astimezone(sched.get_tenant_tz(tenant)).date() == date(2026, 5, 21)
