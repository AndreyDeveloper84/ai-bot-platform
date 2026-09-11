"""GET/POST /api/v1/admin/masters/<id>/schedule/ — §83, правило 2.

Владелец салона видит рабочие часы мастера и подтверждает их. Эти тесты
держат обе половины и три границы, каждая из которых стоила бы отдельного
дефекта:

* показ идёт из ТОГО ЖЕ источника, с которого снимается отпечаток, — иначе
  владелица заверяет одни часы, а клиенту продают другие;
* недоступный источник отвечает НАЗВАННОЙ причиной, а не пустым
  расписанием: пустой список читался бы как «мастер не работает никогда»;
* подтверждается ТО, ЧТО ВИДЕЛИ: если часы изменились между показом и
  нажатием, ответ ``stale_view``, а не молчаливое подтверждение новых.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone as dt_timezone

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.integrations.ayla.salon_client import SalonUnavailable
from apps.tenancy.models import Tenant

from .conftest import init_data_header

pytestmark = pytest.mark.django_db


def _wire_week(**overrides) -> list[dict]:
    rows = [
        {
            "day_of_week": i,
            "day_name": "",
            "is_working_day": i != 6,
            "start_time": "10:00" if i != 6 else None,
            "end_time": "19:00" if i != 6 else None,
            "break_start": None,
            "break_end": None,
        }
        for i in range(7)
    ]
    for day, patch in overrides.items():
        rows[int(day.removeprefix("day"))].update(patch)
    return rows


class _FakeSalonClient:
    def __init__(self, template, exc=None):
        self.template = template
        self.exc = exc

    def get_master_schedule(self, **kwargs):
        if self.exc:
            raise self.exc
        return self.template


@pytest.fixture
def ayla(monkeypatch, settings):
    """Флаг ВКЛЮЧЁН — живая конфигурация пилота (замер 09.09.2026)."""

    settings.BOOKING_VIA_AYLA_REST = True

    def use(template, exc=None):
        client = _FakeSalonClient(template, exc)
        monkeypatch.setattr("apps.integrations.ayla.salon_client.get_salon_client", lambda: client)
        return client

    return use


@pytest.fixture
def synced_master(tenant: Tenant) -> CatalogMaster:
    """Мастер в форме, в которой он приезжает синхронизацией."""

    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=901,
        external_updated_at=datetime.now(tz=dt_timezone.utc),
        name="Ольга Синхронная",
        ayla_user_id=uuid.uuid4(),
    )


def _url(master: CatalogMaster) -> str:
    return reverse("admin_api:master_schedule", args=[str(master.id)])


def _confirm_url(master: CatalogMaster) -> str:
    return reverse("admin_api:master_schedule_confirm", args=[str(master.id)])


def _get(client: Client, master: CatalogMaster, *, user_id: str = "5001"):
    return client.get(_url(master), HTTP_AUTHORIZATION=init_data_header(user_id))


def _confirm(client: Client, master: CatalogMaster, body: dict, *, user_id: str = "5001"):
    return client.post(
        _confirm_url(master),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(user_id),
    )


class TestTheOwnerSeesTheHoursSheIsAskedToVouchFor:
    def test_the_week_comes_back_with_all_seven_days(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        ayla(_wire_week())

        resp = _get(client, synced_master)

        assert resp.status_code == 200
        schedule = resp.json()["schedule"]
        assert schedule["source"] == "ayla"
        assert len(schedule["days"]) == 7
        assert schedule["has_working_day"] is True
        assert [d["day_of_week"] for d in schedule["days"]] == list(range(7))

    def test_the_break_is_shown_because_it_is_part_of_the_hours(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        """Перерыв виден на экране — его же несёт отпечаток.

        Показать часы без перерыва значило бы просить заверить неполное:
        владелица не увидела бы того, что подписывает.
        """

        ayla(_wire_week(day0={"break_start": "13:00", "break_end": "14:00"}))

        days = _get(client, synced_master).json()["schedule"]["days"]

        assert days[0]["break_start"] == "13:00"
        assert days[0]["break_end"] == "14:00"

    def test_an_unconfirmed_master_says_so(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        ayla(_wire_week())

        confirmation = _get(client, synced_master).json()["schedule"]["confirmation"]

        assert confirmation["confirmed_at"] is None
        assert confirmation["confirmed_by"] is None
        assert confirmation["is_current"] is False
        assert confirmation["block"] is None  # нажимать можно

    def test_an_admin_may_look(
        self, client: Client, admin_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        """Смотреть может и админ: прятать состояние от того, кто видит
        ростер, дороже, чем показать."""

        ayla(_wire_week())
        assert _get(client, synced_master, user_id="5002").status_code == 200


class TestAnUnreachableSourceIsNamedNotDrawnAsEmpty:
    def test_ayla_down_answers_503_by_name(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        """Пустое расписание вместо отказа отправило бы владелицу чинить
        график, с которым всё в порядке."""

        ayla([], exc=SalonUnavailable("upstream down"))

        resp = _get(client, synced_master)

        assert resp.status_code == 503
        assert resp.json()["error"] == "schedule_unavailable"

    def test_a_short_wire_answer_is_our_failure_not_her_schedule(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        ayla(_wire_week()[:4])

        resp = _get(client, synced_master)

        assert resp.status_code == 502
        assert resp.json()["error"] == "wire_incomplete"


class TestConfirmingSignsWhatWasSeen:
    def test_the_owner_confirms_and_the_row_records_it(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        ayla(_wire_week())
        seen = _get(client, synced_master).json()["schedule"]["confirmation"]["fingerprint"]

        resp = _confirm(client, synced_master, {"fingerprint": seen})

        assert resp.status_code == 200
        confirmation = resp.json()["schedule"]["confirmation"]
        assert confirmation["is_current"] is True
        assert confirmation["confirmed_by"]["id"] == str(owner_bot_user.id)

        synced_master.refresh_from_db()
        assert synced_master.schedule_confirmed_at is not None
        assert synced_master.schedule_fingerprint == seen

    def test_hours_changed_while_she_looked_refuses_with_a_name(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        """``stale_view`` — половина правила 2, без которой оно пустое.

        Сервис читает источник заново в момент нажатия, иначе подписывал
        бы вчерашний экран. Но без сверки с увиденным он подписал бы
        часы, которых владелица НЕ видела. Нужны обе половины.
        """

        ayla(_wire_week())
        seen = _get(client, synced_master).json()["schedule"]["confirmation"]["fingerprint"]

        ayla(_wire_week(day0={"end_time": "22:00"}))
        resp = _confirm(client, synced_master, {"fingerprint": seen})

        assert resp.status_code == 409
        assert resp.json()["error"] == "stale_view"
        synced_master.refresh_from_db()
        assert synced_master.schedule_confirmed_at is None

    def test_confirming_without_saying_what_is_refused(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        """Подтверждение без указания, ЧТО подтверждают, — это «кто-то
        когда-то нажал». Умолчания здесь нет намеренно."""

        ayla(_wire_week())

        resp = _confirm(client, synced_master, {})

        assert resp.status_code == 400
        synced_master.refresh_from_db()
        assert synced_master.schedule_confirmed_at is None

    def test_a_week_with_no_working_day_cannot_be_confirmed(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        """Правило 7 доезжает до ручки, а не остаётся в сервисе."""

        empty = _wire_week()
        for row in empty:
            row.update(is_working_day=False, start_time=None, end_time=None)
        ayla(empty)

        shown = _get(client, synced_master).json()["schedule"]
        assert shown["confirmation"]["block"] == "no_working_day"

        resp = _confirm(
            client, synced_master, {"fingerprint": shown["confirmation"]["fingerprint"]}
        )

        assert resp.status_code == 409
        assert resp.json()["error"] == "no_working_day"
        synced_master.refresh_from_db()
        assert synced_master.schedule_confirmed_at is None

    def test_an_admin_may_not_confirm(
        self,
        client: Client,
        owner_bot_user: BotUser,
        admin_bot_user: BotUser,
        synced_master: CatalogMaster,
        ayla,
    ) -> None:
        """Подтверждение допускает человека к продаже — это владелица.

        Тот же довод, что у подтверждения приглашения: право смотреть и
        право допускать к витрине — не одно право.
        """

        ayla(_wire_week())
        seen = _get(client, synced_master).json()["schedule"]["confirmation"]["fingerprint"]

        resp = _confirm(client, synced_master, {"fingerprint": seen}, user_id="5002")

        assert resp.status_code == 403
        synced_master.refresh_from_db()
        assert synced_master.schedule_confirmed_at is None


class TestTheConfirmationIsAboutTheseHoursNotAnyHours:
    def test_after_the_hours_change_the_screen_says_it_is_no_longer_current(
        self, client: Client, owner_bot_user: BotUser, synced_master: CatalogMaster, ayla
    ) -> None:
        """Столбец на месте, а «актуально» — уже нет.

        Это то самое различие, без которого отпечаток бесполезен:
        «подтверждено когда-то» ≠ «подтверждено для этой версии часов».
        """

        ayla(_wire_week())
        seen = _get(client, synced_master).json()["schedule"]["confirmation"]["fingerprint"]
        _confirm(client, synced_master, {"fingerprint": seen})

        ayla(_wire_week(day2={"start_time": "12:00"}))
        confirmation = _get(client, synced_master).json()["schedule"]["confirmation"]

        assert confirmation["confirmed_at"] is not None
        assert confirmation["is_current"] is False


class TestAnotherSalonsMasterIsNotVisibleHere:
    def test_a_cross_tenant_master_is_not_found(
        self, client: Client, owner_bot_user: BotUser, other_tenant: Tenant, ayla
    ) -> None:
        """Чужой мастер отвечает «нет такого», а не расписанием."""

        foreign = CatalogMaster.all_tenants.create(
            tenant=other_tenant,
            external_id=902,
            external_updated_at=datetime.now(tz=dt_timezone.utc),
            name="Чужая",
            ayla_user_id=uuid.uuid4(),
        )
        ayla(_wire_week())

        assert _get(client, foreign).status_code == 404
