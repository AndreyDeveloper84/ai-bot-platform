"""«Часы не заданы» — не «выходной» (DRF-2200, М-7, макет DRF-1186).

Красное листа: ``states.day_off`` считается как «рамка прочитана, блока на
сегодня нет» — под этим одним словом лежат две разные вещи:

* у мастера есть недельный шаблон, и **сегодня** в нём выходной;
* шаблона нет вовсе — часы ему ещё никто не ставил.

Экран «Сегодня» обе читает как «Сегодня выходной». Для второго это
неправда: рамки нет, а не выходной, — и дверь «Рабочие часы →» ведёт
человека не туда (соло — «поставьте часы», салонному — «часы ставит
салон»).

* h1 — ``states.hours_set``: шаблона нет → ``false``; есть хоть один
  рабочий день недели → ``true``; каталог не ответил → ``null`` (как
  ``day_off``: «не знаю» — не «нет»);
* h2 — при пустом шаблоне ``day_off`` остаётся ``true`` (рамка прочитана,
  блока нет — это правда), но ``hours_set=false`` даёт экрану право
  сказать другое слово;
* h3 — шаблон есть, сегодня в нём выходной → ``day_off=true``,
  ``hours_set=true`` — прежнее поведение не тронуто.
"""

from __future__ import annotations

from datetime import time as time_cls

import pytest
from django.urls import reverse
from django.utils import timezone as dj_timezone

from apps.integrations.ayla.salon_client import SalonUnavailable
from apps.master_api.tests.conftest import init_data_header
from apps.scheduling.models import WorkingHours

pytestmark = pytest.mark.django_db

DASHBOARD_URL = reverse("master_api:dashboard")


def _dashboard(client):
    return client.get(DASHBOARD_URL, HTTP_AUTHORIZATION=init_data_header("12345"))


def _week(tenant, master, *, working: set[int], start=time_cls(10, 0), end=time_cls(19, 0)) -> None:
    for weekday in range(7):
        WorkingHours.all_tenants.create(
            tenant=tenant,
            master=master,
            day_of_week=weekday,
            is_working=weekday in working,
            start_time=start if weekday in working else None,
            end_time=end if weekday in working else None,
        )


class TestHoursSetIsItsOwnFact:
    def test_no_template_at_all_reads_as_hours_not_set(
        self, client, tenant, bot_user, accepted_master
    ):
        body = _dashboard(client).json()
        states = body["states"]
        # Присутствие: рамка ПРОЧИТАНА — это не «не знаю».
        assert states["day_off"] is True
        assert states["hours_set"] is False

    def test_a_template_with_a_working_day_reads_as_hours_set(
        self, client, tenant, bot_user, accepted_master
    ):
        _week(tenant, accepted_master, working={0, 1, 2, 3, 4})
        states = _dashboard(client).json()["states"]
        assert states["hours_set"] is True

    def test_a_day_off_inside_a_real_template_stays_a_day_off(
        self, client, tenant, bot_user, accepted_master
    ):
        today = dj_timezone.now().astimezone().weekday()
        _week(tenant, accepted_master, working={(today + 1) % 7})
        states = _dashboard(client).json()["states"]
        assert states["day_off"] is True
        assert states["hours_set"] is True

    def test_unreadable_frame_says_nothing_about_hours(
        self, client, tenant, bot_user, accepted_master, monkeypatch, settings
    ):
        settings.BOOKING_VIA_AYLA_REST = True

        def _boom(*args, **kwargs):
            raise SalonUnavailable("timeout")

        monkeypatch.setattr("apps.master_api.services.schedule_frame._load_ayla", _boom)
        states = _dashboard(client).json()["states"]
        assert states["day_off"] is None
        assert states["hours_set"] is None
