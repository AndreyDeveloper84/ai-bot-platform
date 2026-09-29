"""Часы мастера — прокси в каталог и подтверждение для соло (DRF-1816, M24).

Что заперто:

- GET/PUT ``/working-hours`` идут в ``/internal/specialists/{id}/working-hours/``
  каталога с bot-личностью мастера как субъектом; ответ — readback каталога,
  не эхо запроса; второго хранилища часов в боте нет;
- соло (владелец = мастер): после удачной записи расписание подтверждается
  тем же человеком (§83) — ``schedule_confirmed=true``; подтверждать нечего
  (ни одного рабочего дня) → сохранено, ``schedule_confirmed=false``, не
  ошибка; readback упал → сохранено, не подтверждено;
- не соло: подтверждение не зовётся — владелец подтверждает сам;
- отказы каталога переводятся честно: 409 → ``has_active_appointments``,
  403 → ``not_linked`` (до связи писать некуда), 400 → ``validation_error``,
  недоступность → 503; тело без ``schedule`` → 400 без вызова каталога.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster
from apps.catalog.services.schedule_confirmation import ScheduleConfirmationError
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import (
    BookingBadRequestError,
    BookingUnavailableError,
    ScheduleBlockConflictError,
)
from apps.master_api import views
from apps.master_api.tests.conftest import init_data_header, make_master

pytestmark = pytest.mark.django_db

WEEK = [
    {
        "day_of_week": d,
        "is_working_day": d < 5,
        "start_time": "10:00" if d < 5 else None,
        "end_time": "19:00" if d < 5 else None,
        "break_start": None,
        "break_end": None,
    }
    for d in range(7)
]


def _readback(specialist_id: str, schedule=WEEK) -> dict:
    return {"specialist_id": specialist_id, "timezone": "Europe/Moscow", "schedule": schedule}


@pytest.fixture
def ayla(monkeypatch, accepted_master: CatalogMaster) -> MagicMock:
    client = MagicMock()
    client.get_working_hours.return_value = _readback(str(accepted_master.id))
    client.put_working_hours.return_value = _readback(str(accepted_master.id))
    monkeypatch.setattr(views, "get_ayla_booking_client", lambda: client)
    return client


@pytest.fixture
def confirm(monkeypatch) -> MagicMock:
    spy = MagicMock(return_value=object())
    monkeypatch.setattr(views, "confirm_schedule", spy)
    return spy


def _get(client: Client):
    return client.get(
        reverse("master_api:working_hours"), HTTP_AUTHORIZATION=init_data_header("12345")
    )


def _put(client: Client, body):
    return client.put(
        reverse("master_api:working_hours"),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header("12345"),
    )


class TestProxy:
    def test_get_names_the_master_as_subject_and_returns_the_readback(
        self, client: Client, accepted_master, bot_user: BotUser, ayla, confirm
    ):
        resp = _get(client)
        assert resp.status_code == 200, resp.content
        kwargs = ayla.get_working_hours.call_args.kwargs
        assert kwargs["specialist_id"] == str(accepted_master.id)
        assert kwargs["external_user_id"].endswith(str(bot_user.channel_user_id))
        data = resp.json()
        assert data["timezone"] == "Europe/Moscow"
        assert len(data["schedule"]) == 7
        assert "schedule_confirmed" not in data

    def test_put_sends_the_schedule_and_returns_the_readback_not_the_echo(
        self, client: Client, accepted_master, ayla, confirm
    ):
        stored = [dict(d, start_time="09:00") if d["is_working_day"] else d for d in WEEK]
        ayla.put_working_hours.return_value = _readback(str(accepted_master.id), stored)
        resp = _put(client, {"schedule": WEEK})
        assert resp.status_code == 200, resp.content
        assert ayla.put_working_hours.call_args.kwargs["schedule"] == WEEK
        # Ответ — то, что каталог прочёл (09:00), а не то, что мы послали (10:00).
        assert resp.json()["schedule"][0]["start_time"] == "09:00"

    def test_body_without_schedule_is_400_and_the_catalog_is_not_called(
        self, client: Client, accepted_master, ayla, confirm
    ):
        resp = _put(client, {"days": []})
        assert resp.status_code == 400
        ayla.put_working_hours.assert_not_called()


class TestSoloConfirmation:
    def test_solo_confirms_after_a_successful_write(
        self, client: Client, accepted_master, bot_user, ayla, confirm, monkeypatch
    ):
        monkeypatch.setattr(views, "is_solo_provider", lambda tenant: True)
        resp = _put(client, {"schedule": WEEK})
        assert resp.status_code == 200, resp.content
        assert resp.json()["schedule_confirmed"] is True
        confirm.assert_called_once()
        assert confirm.call_args.args[0].pk == accepted_master.pk
        assert confirm.call_args.kwargs["by"].pk == bot_user.pk
        # Порядок: сначала запись в каталог, потом подтверждение (readback).
        assert ayla.put_working_hours.called

    def test_solo_with_nothing_to_confirm_is_saved_but_not_confirmed(
        self, client: Client, accepted_master, ayla, confirm, monkeypatch
    ):
        monkeypatch.setattr(views, "is_solo_provider", lambda tenant: True)
        confirm.side_effect = ScheduleConfirmationError("no_working_day", "nothing to confirm")
        resp = _put(client, {"schedule": WEEK})
        assert resp.status_code == 200, resp.content
        assert resp.json()["schedule_confirmed"] is False

    def test_solo_readback_failure_keeps_the_save_and_reports_unconfirmed(
        self, client: Client, accepted_master, ayla, confirm, monkeypatch
    ):
        monkeypatch.setattr(views, "is_solo_provider", lambda tenant: True)
        confirm.side_effect = BookingUnavailableError("down")
        resp = _put(client, {"schedule": WEEK})
        assert resp.status_code == 200, resp.content
        assert resp.json()["schedule_confirmed"] is False

    def test_salon_master_does_not_self_confirm(
        self, client: Client, accepted_master, ayla, confirm, monkeypatch
    ):
        monkeypatch.setattr(views, "is_solo_provider", lambda tenant: False)
        resp = _put(client, {"schedule": WEEK})
        assert resp.status_code == 200, resp.content
        assert resp.json()["schedule_confirmed"] is False
        confirm.assert_not_called()

    def test_failed_write_never_confirms(
        self, client: Client, accepted_master, ayla, confirm, monkeypatch
    ):
        monkeypatch.setattr(views, "is_solo_provider", lambda tenant: True)
        ayla.put_working_hours.side_effect = ScheduleBlockConflictError("has_active_appointments")
        resp = _put(client, {"schedule": WEEK})
        assert resp.status_code == 409
        confirm.assert_not_called()


class TestCatalogRefusalsAreTranslated:
    @pytest.mark.parametrize(
        "exc, status, slug",
        [
            (ScheduleBlockConflictError("has_active_appointments"), 409, "has_active_appointments"),
            (BookingBadRequestError("http_403", status_code=403), 403, "not_linked"),
            (BookingBadRequestError("http_400", status_code=400), 400, "validation_error"),
            (BookingUnavailableError("down"), 503, "schedule_unavailable"),
        ],
    )
    def test_put(self, client: Client, accepted_master, ayla, confirm, exc, status, slug):
        ayla.put_working_hours.side_effect = exc
        resp = _put(client, {"schedule": WEEK})
        assert resp.status_code == status, resp.content
        assert resp.json()["error"] == slug

    def test_get_before_link_is_not_linked(self, client: Client, accepted_master, ayla, confirm):
        ayla.get_working_hours.side_effect = BookingBadRequestError("http_403", status_code=403)
        resp = _get(client)
        assert resp.status_code == 403
        assert resp.json()["error"] == "not_linked"


class TestSolitude:
    def test_is_solo_provider_is_the_real_one_in_the_view(self, tenant, bot_user):
        """Не подменяя: одинокий мастер в своём тенанте — соло по §3.1."""
        from apps.identity.services.solo_onboarding import is_solo_provider

        make_master(
            tenant,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=None,
            expires_in_days=None,
            linked_bot_user=bot_user,
        )
        assert views.is_solo_provider is is_solo_provider
        assert is_solo_provider(tenant) is True


class TestSoloTimeOffIsOneAction:
    """Карта P69/P71 (M24): соло ставит выходной без заявки владельцу —
    владелец и есть мастер."""

    def _post(self, client: Client, *, days: int = 7):
        from datetime import datetime, timedelta, timezone

        start = (datetime.now(tz=timezone.utc) + timedelta(days=days)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        end = start.replace(hour=18)
        return client.post(
            reverse("master_api:availability_request"),
            data=json.dumps(
                {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "reason_class": "personal",
                    "reason_text": "",
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )

    def test_solo_day_off_is_applied_immediately(
        self, client: Client, accepted_master, bot_user, settings
    ):
        """Один мастер в тенанте, без владельца-персонала — соло по §3.1
        (не подменяя): заявка не остаётся «на решении», выходной уже стоит."""
        from apps.scheduling.models import ScheduleChangeRequest, ScheduleException

        settings.BOOKING_VIA_AYLA_REST = False  # локальная материализация
        resp = self._post(client)
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["applied"] is True
        assert body["status"] == ScheduleChangeRequest.Status.APPROVED
        assert not ScheduleChangeRequest.all_tenants.filter(
            status=ScheduleChangeRequest.Status.PENDING
        ).exists()
        assert ScheduleException.all_tenants.filter(master_id=accepted_master.id).exists()

    def test_solo_conflict_leaves_no_pending_request_behind(
        self, client: Client, accepted_master, bot_user, settings, monkeypatch
    ):
        from apps.master_api import views as v
        from apps.scheduling.models import ScheduleChangeRequest

        settings.BOOKING_VIA_AYLA_REST = False

        def refuse(**kwargs):
            raise v.AvailabilityDecisionError(
                "has_active_appointments", "There are active bookings in this period.", status=409
            )

        monkeypatch.setattr(v, "approve_availability_request", refuse)
        resp = self._post(client)
        assert resp.status_code == 409, resp.content
        assert resp.json()["error"] == "has_active_appointments"
        assert not ScheduleChangeRequest.all_tenants.exists()

    def test_salon_master_still_files_a_request(
        self, client: Client, accepted_master, bot_user, other_bot_user, settings
    ):
        """Положительная стража: у мастера салона (есть владелец) — прежний
        путь заявки, `applied=false`, статус pending."""
        from apps.scheduling.models import ScheduleChangeRequest
        from apps.tenancy.models import TenantStaff

        TenantStaff.all_tenants.create(
            tenant=accepted_master.tenant, bot_user=other_bot_user, role=TenantStaff.Role.OWNER
        )
        settings.BOOKING_VIA_AYLA_REST = False
        resp = self._post(client)
        assert resp.status_code == 201, resp.content
        assert resp.json()["applied"] is False
        assert resp.json()["status"] == ScheduleChangeRequest.Status.PENDING
