"""GET /api/v1/admin/masters/<id>/schedule/impact/ — предпросмотр (§142, срез В).

Что держат эти тесты:

* **салон видит, какие записи закрытие вытеснит** — требование §142
  «показать затронутые записи», сегодня не выполненное ни на одном пути бота:
  внутренний маршрут отвечает числом, не списком;
* **``impact_token`` до экрана не доезжает.** Единственный его потребитель —
  запись с ``resolutions``, а запись отсюда не идёт. Токен на экране — кнопка
  без сервера за ней;
* **«записей нет» и «строки не разобраны» — разные ответы**, и недоступный
  источник называется, а не рисуется пустым списком: пустота читается как
  «никого не затронет»;
* **вид говорит, что не пишет, и куда идти дальше.** ``writable: False``,
  ``next_step: pro_app``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone as dt_timezone

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.integrations.ayla.salon_client import SalonUnavailable, SalonValidationError
from apps.tenancy.models import Tenant

from .conftest import init_data_header

pytestmark = pytest.mark.django_db

#: Окно — датой и часами САЛОНА; смещение прикладывает сервер по поясу салона.
WINDOW = {"date": "2026-09-15", "from": "10:00", "to": "14:00"}
#: Что уходит в Ayla: то же окно со смещением салона (фикстура tenant — MSK).
SENT = {"start_at": "2026-09-15T10:00:00+03:00", "end_at": "2026-09-15T14:00:00+03:00"}


def _booking(**overrides) -> dict:
    """Строка в форме, которую отдаёт ``_impact_to_dict`` каталога
    (``users/schedule_admin_api.py``). Клиента в ней нет по построению."""

    row = {
        "appointment_id": "a-1",
        "version": 3,
        "status": "confirmed",
        "start_at_local": "2026-09-15T11:00:00+03:00",
        "end_at_local": "2026-09-15T12:00:00+03:00",
        "service_name": "Стрижка",
        "duration_minutes": 60,
        "price": "1500.00",
        "payment_status": "paid",
        "refund_percent_if_cancelled": 100,
    }
    row.update(overrides)
    return row


def _impact(bookings=None, **overrides) -> dict:
    body = {
        "specialist_id": "sp-1",
        "start_at": SENT["start_at"],
        "end_at": SENT["end_at"],
        "timezone": "Europe/Moscow",
        "impact_token": "tok-should-not-leak",
        "bookings": [_booking()] if bookings is None else bookings,
    }
    body.update(overrides)
    return body


class _FakeSalonClient:
    def __init__(self, impact=None, exc=None):
        self.impact = _impact() if impact is None else impact
        self.exc = exc
        self.calls: list[dict] = []

    def get_schedule_impact(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return self.impact


@pytest.fixture
def ayla(monkeypatch, settings):
    settings.BOOKING_VIA_AYLA_REST = True

    def use(**kwargs) -> _FakeSalonClient:
        client = _FakeSalonClient(**kwargs)
        # Имя В МОЁМ модуле: он связал ``get_salon_client`` на импорте.
        monkeypatch.setattr("apps.admin_api.views_schedule_impact.get_salon_client", lambda: client)
        return client

    return use


@pytest.fixture
def synced_master(tenant: Tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=941,
        external_updated_at=datetime.now(tz=dt_timezone.utc),
        name="Ольга Синхронная",
        ayla_user_id=uuid.uuid4(),
    )


def _url(master: CatalogMaster) -> str:
    return reverse("admin_api:master_schedule_impact", args=[str(master.id)])


def _get(client: Client, master: CatalogMaster, *, user_id: str = "5001", **params):
    query = {**WINDOW, **params}
    return client.get(_url(master), query, HTTP_AUTHORIZATION=init_data_header(user_id))


class TestTheSalonSeesWhatTheBlockWouldDisplace:
    def test_the_affected_bookings_come_back_parsed(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        fake = ayla()

        resp = _get(client, synced_master)

        assert resp.status_code == 200
        body = resp.json()
        assert body["bookings"]["state"] == "parsed"
        row = body["bookings"]["rows"][0]
        assert row["appointment_id"] == "a-1"
        assert row["start_local"] == "2026-09-15T11:00:00+03:00"
        assert row["service_name"] == "Стрижка"
        assert row["refund_percent_if_cancelled"] == 100
        assert body["timezone"] == "Europe/Moscow"
        # Смещение приложил СЕРВЕР по поясу салона; экран прислал дату и
        # часы, и никакого пересчёта в поясе браузера тут нет и быть не может.
        assert fake.calls[0]["start_at"] == SENT["start_at"]
        assert fake.calls[0]["end_at"] == SENT["end_at"]

    def test_the_impact_token_does_not_reach_the_screen(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        # Присутствие впереди отсутствия: Ayla токен ОТДАЛА — иначе «не
        # доехал» значило бы «не было».
        fake = ayla()
        assert fake.impact["impact_token"] == "tok-should-not-leak"

        body = _get(client, synced_master).json()

        assert body["bookings"]["state"] == "parsed"
        assert "impact_token" not in body
        assert "tok-should-not-leak" not in resp_text(body)

    def test_the_view_says_it_cannot_write_and_where_to_go(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        ayla()
        body = _get(client, synced_master).json()
        assert body["writable"] is False
        assert body["next_step"] == "pro_app"

    def test_no_bookings_is_a_named_empty_not_a_silence(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        ayla(impact=_impact(bookings=[]))
        body = _get(client, synced_master).json()
        assert body["bookings"] == {
            "state": "none",
            "rows": [],
            "seen_fields": [],
            "unreadable_rows": 0,
        }

    def test_a_dropped_row_is_counted_next_to_the_parsed_ones(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        # Строка без времени начала — не запись, а неизвестно что. Мягкий
        # разбор wire_lists её просто выбросил бы и ответил «parsed» — и
        # салон увидел бы «затронет одну», когда затронет две. Здесь предмет
        # — сколько людей пострадает, и выброшенное считается вслух.
        ayla(impact=_impact(bookings=[_booking(), {"appointment_id": "a-2"}]))
        body = _get(client, synced_master).json()["bookings"]
        assert body["state"] == "parsed"
        assert len(body["rows"]) == 1
        assert body["unreadable_rows"] == 1

    def test_nothing_parsed_is_unreadable_not_none(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        ayla(impact=_impact(bookings=[{"appointment_id": "a-2"}]))
        body = _get(client, synced_master).json()["bookings"]
        assert body["state"] == "unreadable"
        assert body["unreadable_rows"] == 1


class TestRefusalsAreNamed:
    def test_an_unavailable_source_is_503_not_an_empty_list(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        ayla(exc=SalonUnavailable("ayla down"))
        resp = _get(client, synced_master)
        assert resp.status_code == 503
        assert resp.json()["error"] == "schedule_unavailable"

    def test_a_validation_refusal_upstream_is_400(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        ayla(exc=SalonValidationError("bad window"))
        assert _get(client, synced_master).status_code == 400

    @pytest.mark.parametrize(
        "params",
        [
            {"date": ""},
            {"from": "25:00"},
            {"date": "15.09.2026"},
            {"from": "14:00", "to": "10:00"},  # конец раньше начала
        ],
    )
    def test_a_bad_window_is_refused_before_ayla_is_asked(
        self,
        client: Client,
        owner_bot_user: BotUser,
        synced_master: CatalogMaster,
        ayla,
        params: dict,
    ) -> None:
        fake = ayla()
        resp = _get(client, synced_master, **params)
        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_window"
        assert fake.calls == []

    def test_flag_off_is_a_named_503(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, settings
    ) -> None:
        settings.BOOKING_VIA_AYLA_REST = False
        resp = _get(client, synced_master)
        assert resp.status_code == 503
        assert resp.json()["error"] == "frame_source_local_unsupported"

    def test_a_master_of_another_salon_is_not_found(
        self, client: Client, owner_bot_user: BotUser, other_tenant: Tenant, ayla
    ) -> None:
        ayla()
        foreign = CatalogMaster.all_tenants.create(
            tenant=other_tenant,
            external_id=942,
            external_updated_at=datetime.now(tz=dt_timezone.utc),
            name="Чужая",
            ayla_user_id=uuid.uuid4(),
        )
        assert _get(client, foreign).status_code == 404


def resp_text(body: dict) -> str:
    import json

    return json.dumps(body, ensure_ascii=False)
