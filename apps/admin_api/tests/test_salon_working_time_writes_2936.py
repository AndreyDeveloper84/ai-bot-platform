"""DRF-2936: what the salon's «Рабочее время» screen added to the DRF-2607 writes.

Pairs that must differ:

* a time off sent as the salon's date and hours reaches the catalog in the
  salon's offset — against the old ``start_at``/``end_at`` form, which still
  goes as sent; both at once is a refusal with nothing written;
* a repeat of a window already stored answers with that row and writes
  nothing — against a different window, which is written;
* the weekday's break inside the new hours of a date travels with the date;
  outside them it does not; across them the write is refused, and an unread
  template refuses too — an unknown break is not «no break».
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone as dt_timezone

import httpx
import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster
from apps.channels.bot_registry import SALON_STREAM, BotEntry
from apps.identity.models import BotUser
from apps.integrations.ayla.person_token import PersonToken
from apps.integrations.ayla.salon_client import AylaSalonClient
from apps.tenancy.models import Tenant
from tests.support.catalog_mirror import sync_shaped

from .conftest import BOT_TOKEN, init_data_header

pytestmark = pytest.mark.django_db

SERVICE_TOKEN = "service-token-2936"  # pragma: allowlist secret
PERSON_TOKEN = "person-token-2936"  # pragma: allowlist secret
WRITES = "apps.admin_api.views_salon_schedule_writes"

#: A Friday. The template below gives Fridays a break.
DAY = "2031-01-10"
FRIDAY = date.fromisoformat(DAY).weekday()


@pytest.fixture(autouse=True)
def _salon_bot_signs(settings, tenant: Tenant) -> None:
    settings.MAX_BOT_REGISTRY = (
        BotEntry(
            slug="signer",
            webhook_secret="",
            api_token=BOT_TOKEN,
            tenant_slug=tenant.slug,
            stream=SALON_STREAM,
        ),
    )


@pytest.fixture(autouse=True)
def _empty_token_cache():
    from apps.integrations.ayla.person_token import _CACHE

    _CACHE.clear()
    yield
    _CACHE.clear()


@pytest.fixture
def synced_master(tenant: Tenant) -> CatalogMaster:
    return sync_shaped(
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=2936,
            external_updated_at=datetime.now(tz=dt_timezone.utc),
            name="Ольга",
            ayla_user_id=uuid.uuid4(),
        )
    )


def _template(*, break_start: str | None = "13:00", break_end: str | None = "14:00") -> list[dict]:
    rows = []
    for weekday in range(7):
        rows.append(
            {
                "day_of_week": weekday,
                "is_working_day": weekday < 6,
                "start_time": "10:00:00" if weekday < 6 else None,
                "end_time": "19:00:00" if weekday < 6 else None,
                "break_start": break_start if weekday == FRIDAY else None,
                "break_end": break_end if weekday == FRIDAY else None,
            }
        )
    return rows


class _Catalog:
    """The catalog whole: reads on the service key, writes on the person's token."""

    def __init__(
        self,
        monkeypatch,
        *,
        time_off: list[dict] | None = None,
        exceptions: list[dict] | None = None,
        template: list[dict] | None = None,
        unreadable: tuple[str, ...] = (),
        write_body: dict | None = None,
    ) -> None:
        self.reads: list[httpx.Request] = []
        self.writes: list[httpx.Request] = []
        self.exchanged = 0
        lists = {
            "/time-off/": time_off or [],
            "/schedule-exceptions/": exceptions or [],
            "/schedule/": _template() if template is None else template,
        }

        def exchange(*, init_data: str, tenant_slug: str, **_kw) -> PersonToken:
            self.exchanged += 1
            return PersonToken(access_token=PERSON_TOKEN, tenant_slug=tenant_slug, expires_in=600)

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if request.method == "GET":
                self.reads.append(request)
                for suffix, rows in lists.items():
                    if path.endswith(suffix):
                        if suffix in unreadable:
                            return httpx.Response(503, json={"error": {"code": "DOWN"}})
                        return httpx.Response(200, json={"data": rows})
                return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
            self.writes.append(request)
            return httpx.Response(
                201 if request.method == "POST" else 200,
                json={"data": write_body or {"id": "row-1"}},
            )

        client = AylaSalonClient(
            base_url="https://ayla.example",
            service_token=SERVICE_TOKEN,
            transport=httpx.MockTransport(handler),
        )
        monkeypatch.setattr(f"{WRITES}.obtain_person_token", exchange)
        monkeypatch.setattr(f"{WRITES}.get_salon_client", lambda: client)

    def written(self) -> dict:
        (sent,) = self.writes
        return json.loads(sent.content)


def _post_time_off(client: Client, master: CatalogMaster, body: dict):
    return client.post(
        reverse("admin_api:master_time_off", args=[str(master.id)]),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header("5001"),
    )


def _put_date(client: Client, master: CatalogMaster, body: dict):
    return client.put(
        reverse("admin_api:master_date_exception", args=[str(master.id)]),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header("5001"),
    )


class TestTimeOffInTheSalonsHours:
    def test_the_salons_date_and_hours_reach_the_catalog_in_the_salons_offset(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, monkeypatch
    ) -> None:
        catalog = _Catalog(monkeypatch)

        resp = _post_time_off(
            client,
            synced_master,
            {"date": DAY, "from": "13:00", "to": "15:00", "reason": "Личные дела"},
        )

        assert resp.status_code == 201, resp.content
        assert catalog.written() == {
            "start_at": f"{DAY}T13:00:00+03:00",
            "end_at": f"{DAY}T15:00:00+03:00",
            "reason": "Личные дела",
        }

    def test_end_of_day_is_the_next_midnight(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, monkeypatch
    ) -> None:
        catalog = _Catalog(monkeypatch)

        resp = _post_time_off(client, synced_master, {"date": DAY, "from": "00:00", "to": "24:00"})

        assert resp.status_code == 201, resp.content
        assert catalog.written()["end_at"] == "2031-01-11T00:00:00+03:00"

    def test_the_old_moment_form_still_goes_as_sent(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, monkeypatch
    ) -> None:
        catalog = _Catalog(monkeypatch)

        resp = _post_time_off(
            client,
            synced_master,
            {"start_at": "2031-01-10T09:00:00+03:00", "end_at": "2031-01-10T18:00:00+03:00"},
        )

        assert resp.status_code == 201, resp.content
        assert catalog.written()["start_at"] == "2031-01-10T09:00:00+03:00"

    @pytest.mark.parametrize(
        "body",
        [
            {"date": DAY, "from": "13:00", "to": "15:00", "start_at": "2031-01-10T13:00:00+03:00"},
            {"date": DAY, "from": "15:00", "to": "13:00"},
            {"date": DAY, "from": "13:00"},
            {"date": "10.01.2031", "from": "13:00", "to": "15:00"},
            {"date": DAY, "from": "1300", "to": "15:00"},
        ],
        ids=["both-forms", "backwards", "no-end", "foreign-date", "no-colon"],
    )
    def test_a_window_that_does_not_parse_is_refused_and_nothing_is_written(
        self,
        client: Client,
        owner_bot_user: BotUser,
        synced_master: CatalogMaster,
        monkeypatch,
        body: dict,
    ) -> None:
        catalog = _Catalog(monkeypatch)

        resp = _post_time_off(client, synced_master, body)

        assert (resp.status_code, resp.json()["error"]) == (400, "validation")
        assert (catalog.writes, catalog.exchanged) == ([], 0)

    def test_the_answer_reads_the_salons_clock(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, monkeypatch
    ) -> None:
        # Presence first: the catalog answered in UTC.
        stored = {
            "id": "off-9",
            "start_at": "2031-01-10T10:00:00+00:00",
            "end_at": "2031-01-10T12:00:00+00:00",
            "reason": "Личные дела",
        }
        _Catalog(monkeypatch, write_body=stored)

        resp = _post_time_off(client, synced_master, {"date": DAY, "from": "13:00", "to": "15:00"})

        data = resp.json()["data"]
        assert (data["id"], data["start_at"], data["end_at"]) == (
            "off-9",
            "2031-01-10T13:00:00+03:00",
            "2031-01-10T15:00:00+03:00",
        )


class TestARepeatDoesNotCloseTheTimeTwice:
    def test_the_same_window_already_stored_is_returned_and_nothing_is_written(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, monkeypatch
    ) -> None:
        # The same moments, in the catalog's UTC, and the same reason.
        catalog = _Catalog(
            monkeypatch,
            time_off=[
                {
                    "id": "off-1",
                    "start_at": "2031-01-10T10:00:00+00:00",
                    "end_at": "2031-01-10T12:00:00+00:00",
                    "reason": "Личные дела",
                }
            ],
        )

        resp = _post_time_off(
            client,
            synced_master,
            {"date": DAY, "from": "13:00", "to": "15:00", "reason": " Личные дела "},
        )

        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["existing"] is True
        assert (body["data"]["id"], body["data"]["start_at"]) == (
            "off-1",
            "2031-01-10T13:00:00+03:00",
        )
        assert (catalog.writes, catalog.exchanged) == ([], 0)

    def test_the_same_window_with_another_reason_is_a_new_row(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, monkeypatch
    ) -> None:
        # Правка причины на экране — новая строка и снятие старой. «Такой уже
        # есть» со старой строкой и следом её снятие оставили бы мастера без
        # отгула вовсе.
        catalog = _Catalog(
            monkeypatch,
            time_off=[
                {
                    "id": "off-1",
                    "start_at": "2031-01-10T10:00:00+00:00",
                    "end_at": "2031-01-10T12:00:00+00:00",
                    "reason": "Личные дела",
                }
            ],
        )

        resp = _post_time_off(
            client, synced_master, {"date": DAY, "from": "13:00", "to": "15:00", "reason": "Врач"}
        )

        assert resp.status_code == 201, resp.content
        assert catalog.written()["reason"] == "Врач"

    def test_a_different_window_is_written(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, monkeypatch
    ) -> None:
        catalog = _Catalog(
            monkeypatch,
            time_off=[
                {
                    "id": "off-1",
                    "start_at": "2031-01-10T10:00:00+00:00",
                    "end_at": "2031-01-10T11:00:00+00:00",
                }
            ],
        )

        resp = _post_time_off(client, synced_master, {"date": DAY, "from": "13:00", "to": "15:00"})

        assert resp.status_code == 201, resp.content
        body = resp.json()
        # Presence first: the answer is the written row.
        assert body["data"]["id"] == "row-1"
        assert "existing" not in body
        assert len(catalog.writes) == 1

    def test_the_read_spans_the_day_before_and_after(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, monkeypatch
    ) -> None:
        catalog = _Catalog(monkeypatch)

        _post_time_off(client, synced_master, {"date": DAY, "from": "13:00", "to": "15:00"})

        (listed,) = [r for r in catalog.reads if r.url.path.endswith("/time-off/")]
        assert (listed.url.params["date_from"], listed.url.params["date_to"]) == (
            "2031-01-09",
            "2031-01-11",
        )

    def test_an_unread_list_does_not_block_the_write(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, monkeypatch
    ) -> None:
        catalog = _Catalog(monkeypatch, unreadable=("/time-off/",))

        resp = _post_time_off(client, synced_master, {"date": DAY, "from": "13:00", "to": "15:00"})

        assert resp.status_code == 201, resp.content
        assert len(catalog.writes) == 1


class TestADateKeepsWhatTheScreenDoesNotEdit:
    def test_the_weekdays_break_inside_the_new_hours_travels_with_the_date(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, monkeypatch
    ) -> None:
        catalog = _Catalog(monkeypatch)

        resp = _put_date(
            client,
            synced_master,
            {"date": DAY, "is_working_day": True, "start_time": "10:00", "end_time": "18:00"},
        )

        assert resp.status_code == 200, resp.content
        written = catalog.written()
        assert (written["break_start"], written["break_end"]) == ("13:00", "14:00")

    def test_a_break_outside_the_new_hours_is_not_sent(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, monkeypatch
    ) -> None:
        catalog = _Catalog(monkeypatch)

        resp = _put_date(
            client,
            synced_master,
            {"date": DAY, "is_working_day": True, "start_time": "15:00", "end_time": "19:00"},
        )

        assert resp.status_code == 200, resp.content
        written = catalog.written()
        assert written["start_time"] == "15:00"
        assert "break_start" not in written and "break_end" not in written

    def test_a_break_across_the_new_hours_is_refused_and_nothing_is_written(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, monkeypatch
    ) -> None:
        catalog = _Catalog(monkeypatch)

        resp = _put_date(
            client,
            synced_master,
            {"date": DAY, "is_working_day": True, "start_time": "13:30", "end_time": "19:00"},
        )

        assert resp.status_code == 400, resp.content
        assert resp.json()["details"] == {
            "reason": "template_break_crosses_hours",
            "break_start": "13:00",
            "break_end": "14:00",
        }
        assert (catalog.writes, catalog.exchanged) == ([], 0)

    def test_the_dates_own_break_wins_over_the_weekday(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, monkeypatch
    ) -> None:
        catalog = _Catalog(
            monkeypatch,
            exceptions=[
                {
                    "id": "exc-1",
                    "date": DAY,
                    "is_working_day": True,
                    "start_time": "10:00:00",
                    "end_time": "19:00:00",
                    "break_start": "15:00:00",
                    "break_end": "16:00:00",
                    "note": "",
                }
            ],
        )

        resp = _put_date(
            client,
            synced_master,
            {"date": DAY, "is_working_day": True, "start_time": "10:00", "end_time": "18:00"},
        )

        assert resp.status_code == 200, resp.content
        written = catalog.written()
        assert (written["break_start"], written["break_end"]) == ("15:00", "16:00")
        assert not [r for r in catalog.reads if r.url.path.endswith("/schedule/")]

    def test_a_date_set_without_a_break_does_not_get_the_weekdays_back(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, monkeypatch
    ) -> None:
        # «10–19 без обеда» из Pro App: правка часов не возвращает обед шаблона.
        catalog = _Catalog(
            monkeypatch,
            exceptions=[
                {
                    "id": "exc-1",
                    "date": DAY,
                    "is_working_day": True,
                    "start_time": "10:00:00",
                    "end_time": "19:00:00",
                    "break_start": None,
                    "break_end": None,
                    "note": "",
                }
            ],
        )

        resp = _put_date(
            client,
            synced_master,
            {"date": DAY, "is_working_day": True, "start_time": "10:00", "end_time": "18:00"},
        )

        assert resp.status_code == 200, resp.content
        written = catalog.written()
        assert written["end_time"] == "18:00"
        assert "break_start" not in written and "break_end" not in written

    def test_an_unread_date_row_writes_nothing(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, monkeypatch
    ) -> None:
        catalog = _Catalog(monkeypatch, unreadable=("/schedule-exceptions/",))

        resp = _put_date(
            client,
            synced_master,
            {"date": DAY, "is_working_day": True, "start_time": "10:00", "end_time": "18:00"},
        )

        assert (resp.status_code, resp.json()["error"]) == (503, "schedule_unavailable")
        assert (catalog.writes, catalog.exchanged) == ([], 0)

    def test_an_unread_template_writes_nothing(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, monkeypatch
    ) -> None:
        catalog = _Catalog(monkeypatch, unreadable=("/schedule/",))

        resp = _put_date(
            client,
            synced_master,
            {"date": DAY, "is_working_day": True, "start_time": "10:00", "end_time": "18:00"},
        )

        assert (resp.status_code, resp.json()["error"]) == (503, "schedule_unavailable")
        assert (catalog.writes, catalog.exchanged) == ([], 0)

    def test_a_day_off_reads_no_template(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, monkeypatch
    ) -> None:
        catalog = _Catalog(monkeypatch, unreadable=("/schedule/",))

        resp = _put_date(client, synced_master, {"date": DAY, "is_working_day": False})

        assert resp.status_code == 200, resp.content
        assert not [r for r in catalog.reads if r.url.path.endswith("/schedule/")]

    def test_the_dates_note_survives_an_edit_that_sends_none(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, monkeypatch
    ) -> None:
        catalog = _Catalog(
            monkeypatch,
            exceptions=[{"id": "exc-1", "date": DAY, "is_working_day": True, "note": "Свадьба"}],
        )

        resp = _put_date(client, synced_master, {"date": DAY, "is_working_day": False})

        assert resp.status_code == 200, resp.content
        assert catalog.written()["note"] == "Свадьба"

    def test_a_sent_note_is_written_as_sent(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, monkeypatch
    ) -> None:
        catalog = _Catalog(
            monkeypatch,
            exceptions=[{"id": "exc-1", "date": DAY, "is_working_day": True, "note": "Свадьба"}],
        )

        resp = _put_date(client, synced_master, {"date": DAY, "is_working_day": False, "note": ""})

        assert resp.status_code == 200, resp.content
        assert catalog.written()["note"] == ""
