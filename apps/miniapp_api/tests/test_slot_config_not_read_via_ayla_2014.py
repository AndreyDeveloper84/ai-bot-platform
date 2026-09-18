"""Горизонт слотов на пути Ayla — каталога, не локальной копии (DRF-2014).

Последний читатель локального расписания мимо ``BOOKING_VIA_AYLA_REST``:
``miniapp_api/views.py`` читал ``get_slot_config(tenant)`` ДО ветки флага, и
``max_advance_days`` локального ``SlotConfig`` (на пилоте 0 строк → умолчание
60) клампил ``date_to`` ещё до того, как каталог спрашивали. Держалось это
совпадением: локальное умолчание 60 == ``BOOKING_MAX_AHEAD_DAYS = 60`` у
каталога; поменяй любое из двух — разойдутся молча.

Узлы, по паре на одних данных:

* N1 — флаг ON, локальная строка ``SlotConfig(max_advance_days=1)``: окно в
  14 дней НЕ обрезается — каталог спрошен за каждый день (счётчиком);
* N2 — флаг ON: ``get_slot_config`` не зовётся вовсе (шпион), ни одним полем;
* N3 — флаг OFF, та же строка: кламп до одного дня работает как раньше —
  положительная стража: OFF-путь (X5) не тронут, и узел N1 не зеленеет на
  предикате, который клампа не знает вовсе;
* N4 — флаг ON: граница ЗАПРОСА (``MAX_SLOT_DATE_RANGE_DAYS``) остаётся —
  снят локальный кламп, а не всякая граница.

Стенд — ``test_slots_ayla_source_1062`` (подписанная initData, подмена
клиента Ayla со счётчиком вызовов).
"""

# ruff: noqa: F811 — фикстуры стенда 1062 импортируются по имени и приходят
# параметрами узлов; для ruff это «переопределение», для pytest — механизм.
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time

from apps.miniapp_api import views as miniapp_views
from apps.miniapp_api.tests.test_slots_ayla_source_1062 import (  # noqa: F401 — фикстуры
    _bot_token,
    _fake_client,
    _init_data_header,
    bot_user,
    master,
    master_service,
    open_every_day,
    service,
    tenant,
)
from apps.miniapp_api.views import MAX_SLOT_DATE_RANGE_DAYS
from apps.scheduling.models import SlotConfig
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

CLIENT_PATH = "apps.integrations.ayla.booking_client.get_ayla_booking_client"
WINDOW_DAYS = MAX_SLOT_DATE_RANGE_DAYS  # 14 — граница запроса, не горизонт


@pytest.fixture
def one_day_horizon(tenant: Tenant) -> SlotConfig:
    """Локальная копия говорит «не дальше завтра» — на пути Ayla ей слова нет."""
    return SlotConfig.all_tenants.create(tenant=tenant, max_advance_days=1)


def _today_local(tenant: Tenant) -> date:
    """«Сегодня» в поясе тенанта — тем же выражением, что у OFF-пути ручки.

    DRF-2103: ``date.today()`` на UTC-раннере и ``today_local`` тенанта
    (Europe/Moscow, UTC+3) расходятся на сутки между 21:00 и 24:00 UTC, и
    узел n3, сравнивающий окно с «сегодня», краснел ровно в эти три часа —
    на трёх PR разом. Часы — молчаливый параметр; здесь он назван.
    """
    return timezone.now().astimezone(ZoneInfo(tenant.timezone)).date()


def _window(client: Client, master, service, *, start: date, days: int):
    return client.get(
        reverse("miniapp_api:slots")
        + "?"
        + urlencode(
            {
                "master_id": str(master.id),
                "service_id": str(service.id),
                "date_from": start.isoformat(),
                "date_to": (start + timedelta(days=days)).isoformat(),
            }
        ),
        HTTP_AUTHORIZATION=_init_data_header("12345"),
    )


class TestAylaPathDoesNotReadTheLocalSlotConfig:
    def test_n1_local_horizon_does_not_clamp_the_window(
        self, settings, client, bot_user, master, service, master_service, one_day_horizon
    ):
        settings.BOOKING_VIA_AYLA_REST = True
        start = date.today() + timedelta(days=2)
        fake, calls = _fake_client({})

        with patch(CLIENT_PATH, return_value=fake):
            resp = _window(client, master, service, start=start, days=WINDOW_DAYS)

        assert resp.status_code == 200, resp.content
        # Каталог спрошен за КАЖДЫЙ день окна — локальный «не дальше завтра» не режет.
        assert [c["date"] for c in calls] == [
            (start + timedelta(days=i)).isoformat() for i in range(WINDOW_DAYS + 1)
        ]

    def test_n2_get_slot_config_is_not_called_on_the_ayla_path(
        self, settings, client, bot_user, master, service, master_service, one_day_horizon
    ):
        settings.BOOKING_VIA_AYLA_REST = True
        fake, _calls = _fake_client({})

        with (
            patch(CLIENT_PATH, return_value=fake),
            patch.object(
                miniapp_views, "get_slot_config", wraps=miniapp_views.get_slot_config
            ) as spy,
        ):
            resp = _window(client, master, service, start=date.today() + timedelta(days=2), days=3)

        assert resp.status_code == 200, resp.content
        assert spy.call_count == 0

    def test_n3_flag_off_the_local_horizon_still_clamps(
        self,
        settings,
        client,
        tenant,
        bot_user,
        master,
        service,
        master_service,
        open_every_day,
        one_day_horizon,
    ):
        """Положительная стража: OFF-путь читает копию и клампит — не тронут (X5)."""
        settings.BOOKING_VIA_AYLA_REST = False
        start = _today_local(tenant)
        fake, calls = _fake_client({})

        with (
            patch(CLIENT_PATH, return_value=fake),
            patch.object(
                miniapp_views, "get_slot_config", wraps=miniapp_views.get_slot_config
            ) as spy,
        ):
            resp = _window(client, master, service, start=start, days=WINDOW_DAYS)

        assert resp.status_code == 200, resp.content
        assert spy.call_count == 1
        assert calls == []  # Ayla на OFF-пути не зовётся
        dates = {s["date"] for s in resp.json()["slots"]}
        assert dates, "локальные часы открыты каждый день — слоты должны быть"
        assert dates <= {start.isoformat(), (start + timedelta(days=1)).isoformat()}

    @freeze_time("2026-09-18 23:30:00", tz_offset=0)
    def test_n3b_the_clamp_holds_at_the_utc_day_boundary(
        self,
        settings,
        client,
        tenant,
        bot_user,
        master,
        service,
        master_service,
        open_every_day,
        one_day_horizon,
    ):
        """DRF-2103 — ложный вход: 23:30 UTC = 02:30 следующего дня в Москве.

        До правки узел брал ``date.today()`` (UTC: 18.09) и ждал окно
        {18.09, 19.09}, а ручка клампила по «сегодня» тенанта (19.09) и
        отдавала {19.09, 20.09}. Часы заморожены на границе: если «сегодня»
        снова возьмут с раннера, узел покраснеет здесь, а не раз в сутки на
        чужих PR.
        """
        settings.BOOKING_VIA_AYLA_REST = False
        assert date.today() == date(2026, 9, 18)  # UTC-раннер
        start = _today_local(tenant)
        assert start == date(2026, 9, 19)  # тенант уже в завтра
        fake, calls = _fake_client({})

        with patch(CLIENT_PATH, return_value=fake):
            resp = _window(client, master, service, start=start, days=WINDOW_DAYS)

        assert resp.status_code == 200, resp.content
        assert calls == []
        dates = {s["date"] for s in resp.json()["slots"]}
        assert dates, "локальные часы открыты каждый день — слоты должны быть"
        assert dates <= {start.isoformat(), (start + timedelta(days=1)).isoformat()}

    def test_n4_the_request_bound_stays_on_the_ayla_path(
        self, settings, client, bot_user, master, service, master_service, one_day_horizon
    ):
        settings.BOOKING_VIA_AYLA_REST = True
        fake, calls = _fake_client({})

        with patch(CLIENT_PATH, return_value=fake):
            resp = _window(
                client,
                master,
                service,
                start=date.today() + timedelta(days=2),
                days=WINDOW_DAYS + 1,
            )

        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_request"
        assert calls == []
