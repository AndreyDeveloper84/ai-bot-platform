"""DRF-2561 — перенос записи в Mini App на пути Ayla: исходы подтверждения.

Путь целиком (запись → перенос → событие) держит
``test_booking_journey_ayla_path.py::TestRescheduleOnTheAylaPath``. Здесь —
отказы канона и то, чего до канона доходить не должно.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from django.utils import timezone

from apps.integrations.ayla.booking_client import (
    AylaBookingRecord,
    BookingBadRequestError,
    BookingUnavailableError,
)
from apps.miniapp_api.tests.test_person_owns_booking_2436 import (  # noqa: F401 — autouse
    AYLA_UID,
    ME,
    STRANGER,
    _identity,
    _post,
    _settings,
)
from apps.booking.models import RemoteBookingProxy
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


class _Stub:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.error = error

    def reschedule_appointment(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return AylaBookingRecord(
            appointment_id=kwargs["appointment_id"],
            raw={"id": kwargs["appointment_id"], "start_datetime": kwargs["new_start_datetime"]},
        )


@pytest.fixture
def home() -> Tenant:
    return Tenant.objects.create(slug="home-2436", name="Формула тела", timezone="Europe/Moscow")


def _stub(monkeypatch, error: Exception | None = None) -> _Stub:
    stub = _Stub(error)
    monkeypatch.setattr(
        "apps.integrations.ayla.booking_client.get_ayla_booking_client", lambda: stub
    )
    return stub


def _proxy(tenant: Tenant, bot_user, status: str = "confirmed") -> RemoteBookingProxy:
    start = timezone.now() + timedelta(days=5)
    return RemoteBookingProxy.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        appointment_id=uuid.uuid4(),
        start_at=start,
        end_at=start + timedelta(hours=1),
        status=status,
    )


def _confirm(client, proxy: RemoteBookingProxy, when: datetime):
    return _post(
        client,
        f"/api/v1/customer/bookings/{proxy.appointment_id}/reschedule/confirm",
        {"new_visit_at": when.isoformat()},
    )


def _when(days: int = 9) -> datetime:
    return (timezone.now() + timedelta(days=days)).replace(microsecond=0)


class TestConfirmOutcomes:
    def test_slot_taken_in_canon_is_slot_unavailable_other_refusal_is_invalid_state(
        self, client, home, monkeypatch
    ) -> None:
        mine = _proxy(home, _identity(home, ME, AYLA_UID))

        _stub(monkeypatch, BookingBadRequestError("x", status_code=409, code="SLOT_UNAVAILABLE"))
        taken = _confirm(client, mine, _when())
        _stub(monkeypatch, BookingBadRequestError("x", status_code=422, code="TOO_LATE"))
        other = _confirm(client, mine, _when())

        # Пара: «время заняли» — экран предложит другое; прочий отказ — нет.
        assert (taken.status_code, taken.json()["error"]) == (409, "slot_unavailable")
        assert (other.status_code, other.json()["error"]) == (409, "invalid_state")

    def test_canon_down_is_502_not_a_success(self, client, home, monkeypatch) -> None:
        mine = _proxy(home, _identity(home, ME, AYLA_UID))
        _stub(monkeypatch, BookingUnavailableError("down"))

        resp = _confirm(client, mine, _when())

        assert resp.status_code == 502
        assert resp.json()["error"] == "upstream_unavailable"

    def test_strangers_booking_never_reaches_the_canon(self, client, home, monkeypatch) -> None:
        stub = _stub(monkeypatch)
        mine = _proxy(home, _identity(home, ME, AYLA_UID))
        theirs = _proxy(home, _identity(home, STRANGER))

        # Положительная сторона впереди: своя — доходит.
        assert _confirm(client, mine, _when()).status_code == 200
        resp = _confirm(client, theirs, _when())

        assert resp.status_code == 404
        assert [c["appointment_id"] for c in stub.calls] == [str(mine.appointment_id)]

    def test_unpaid_booking_is_refused_before_the_canon(self, client, home, monkeypatch) -> None:
        stub = _stub(monkeypatch)
        unpaid = _proxy(home, _identity(home, ME, AYLA_UID), status="awaiting_payment")

        resp = _confirm(client, unpaid, _when())

        assert (resp.status_code, resp.json()["error"]) == (409, "invalid_state")
        assert stub.calls == []

    def test_time_without_offset_or_in_the_past_is_400(self, client, home, monkeypatch) -> None:
        stub = _stub(monkeypatch)
        mine = _proxy(home, _identity(home, ME, AYLA_UID))
        url = f"/api/v1/customer/bookings/{mine.appointment_id}/reschedule/confirm"

        naive = _post(client, url, {"new_visit_at": "2031-01-01T10:00:00"})
        past = _post(
            client, url, {"new_visit_at": (timezone.now() - timedelta(hours=1)).isoformat()}
        )

        assert naive.status_code == 400
        assert (past.status_code, past.json()["error"]) == (400, "visit_in_past")
        assert stub.calls == []

    def test_idempotency_key_repeats_for_the_same_time_and_differs_for_another(
        self, client, home, monkeypatch
    ) -> None:
        stub = _stub(monkeypatch)
        mine = _proxy(home, _identity(home, ME, AYLA_UID))
        first = _when(9)

        for when in (first, first, _when(10)):
            assert _confirm(client, mine, when).status_code == 200

        keys = [c["idempotency_key"] for c in stub.calls]
        assert keys[0] == keys[1]
        assert keys[0] != keys[2]
