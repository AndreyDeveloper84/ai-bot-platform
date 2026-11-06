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

    def test_a_retry_after_a_failure_repeats_the_key(self, client, home, monkeypatch) -> None:
        """Сбой канона → зеркало не сдвинуто → повтор того же нажатия несёт
        тот же ключ: если канон всё-таки перенёс, он ответит сохранённым."""
        mine = _proxy(home, _identity(home, ME, AYLA_UID))
        # Повтор того же нажатия — то же время. Два вызова ``_when(9)`` берут
        # ``now()`` дважды и на границе секунды дают разные времена, а с ними
        # и разные ключи: узел краснел без дефекта (CI #2283, 04.10).
        nine = _when(9)
        failing = _stub(monkeypatch, BookingUnavailableError("down"))
        assert _confirm(client, mine, nine).status_code == 502
        ok = _stub(monkeypatch)
        assert _confirm(client, mine, nine).status_code == 200
        assert _confirm(client, mine, _when(10)).status_code == 200

        first = failing.calls[0]["idempotency_key"]
        assert ok.calls[0]["idempotency_key"] == first
        assert ok.calls[1]["idempotency_key"] != first

    def test_a_repeat_after_success_does_not_reach_the_canon_again(
        self, client, home, monkeypatch
    ) -> None:
        stub = _stub(monkeypatch)
        mine = _proxy(home, _identity(home, ME, AYLA_UID))
        when = _when(9)

        assert _confirm(client, mine, when).status_code == 200
        again = _confirm(client, mine, when)

        assert again.status_code == 200
        assert len(stub.calls) == 1
        assert datetime.fromisoformat(again.json()["new_booking"]["visit_at"]) == when

    def test_moving_back_to_an_earlier_time_is_a_new_key(self, client, home, monkeypatch) -> None:
        """«10:00 → 12:00 → снова 10:00»: третий перенос — не повтор первого.
        Ключ помнит, ОТКУДА переносят; иначе канон вправе ответить
        сохранённым 200 и не двинуть запись."""
        stub = _stub(monkeypatch)
        mine = _proxy(home, _identity(home, ME, AYLA_UID))
        ten, noon = _when(9), _when(10)

        assert _confirm(client, mine, ten).status_code == 200
        # Событие booking.rescheduled сдвинуло зеркало на 10:00; теперь — в 12:00.
        RemoteBookingProxy.all_tenants.filter(pk=mine.pk).update(start_at=ten)
        assert _confirm(client, mine, noon).status_code == 200
        RemoteBookingProxy.all_tenants.filter(pk=mine.pk).update(start_at=noon)
        assert _confirm(client, mine, ten).status_code == 200

        keys = [c["idempotency_key"] for c in stub.calls]
        assert len(set(keys)) == 3

    def test_same_instant_in_another_offset_is_the_same_key(
        self, client, home, monkeypatch
    ) -> None:
        from zoneinfo import ZoneInfo

        mine = _proxy(home, _identity(home, ME, AYLA_UID))
        at = _when(9)
        failing = _stub(monkeypatch, BookingUnavailableError("down"))
        for when in (at, at.astimezone(ZoneInfo("Europe/Moscow"))):
            assert _confirm(client, mine, when).status_code == 502

        assert failing.calls[0]["idempotency_key"] == failing.calls[1]["idempotency_key"]

    def test_non_string_time_is_400_not_500(self, client, home, monkeypatch) -> None:
        stub = _stub(monkeypatch)
        mine = _proxy(home, _identity(home, ME, AYLA_UID))
        url = f"/api/v1/customer/bookings/{mine.appointment_id}/reschedule/confirm"

        assert _post(client, url, {"new_visit_at": 123}).status_code == 400
        assert stub.calls == []


class TestTheMirrorMovesOnlyTheTime:
    """Вариант А (главное окно, 28.09): после 200 канона зеркало сдвигается
    сразу — иначе экран покажет старое время и «Перенести» ещё раз. Но
    двигается ТОЛЬКО время: запись статуса или отметки события константой —
    дефект DRF-2537."""

    def test_only_start_and_end_move_everything_else_is_untouched(
        self, client, home, monkeypatch
    ) -> None:
        from django.forms.models import model_to_dict

        _stub(monkeypatch)
        mine = _proxy(home, _identity(home, ME, AYLA_UID))
        # Непустые значения там, где чат пишет константы: подмена «записать как
        # чат» (источник, отметка события) обязана здесь краснеть.
        RemoteBookingProxy.all_tenants.filter(pk=mine.pk).update(
            source="mobile_app",
            last_applied_event_name="booking.created",
            last_applied_event_at=timezone.now() - timedelta(days=1),
            service_id=uuid.uuid4(),
        )
        mine.refresh_from_db()
        before = model_to_dict(mine)
        duration = mine.end_at - mine.start_at
        when = _when(9)

        assert _confirm(client, mine, when).status_code == 200

        mine.refresh_from_db()
        after = model_to_dict(mine)
        assert mine.start_at == when
        assert mine.end_at == when + duration
        changed = {k for k in before if before[k] != after[k]}
        assert changed == {"start_at", "end_at"}

    def test_a_refused_move_leaves_the_mirror_alone(self, client, home, monkeypatch) -> None:
        _stub(monkeypatch, BookingBadRequestError("x", status_code=409, code="SLOT_UNAVAILABLE"))
        mine = _proxy(home, _identity(home, ME, AYLA_UID))
        start = mine.start_at

        assert _confirm(client, mine, _when(9)).status_code == 409

        mine.refresh_from_db()
        assert mine.start_at == start

    def test_a_late_racer_does_not_overwrite_the_winner(self, client, home, monkeypatch) -> None:
        """Сравнить-и-поставить: пока канон переносил, строку уже сдвинули
        (другой перенос или событие). Опоздавший её не перезаписывает."""
        stub = _stub(monkeypatch)
        mine = _proxy(home, _identity(home, ME, AYLA_UID))
        winner = _when(11)
        original = stub.reschedule_appointment

        def moved_meanwhile(**kwargs):
            RemoteBookingProxy.all_tenants.filter(pk=mine.pk).update(start_at=winner)
            return original(**kwargs)

        stub.reschedule_appointment = moved_meanwhile  # type: ignore[method-assign]

        assert _confirm(client, mine, _when(9)).status_code == 200

        mine.refresh_from_db()
        assert mine.start_at == winner


class TestTheVersionRuleIsTheChats:
    def test_the_mirrors_version_goes_to_the_canon_none_stays_none(
        self, client, home, monkeypatch
    ) -> None:
        """Одно правило с чатом (_proxy_expected_version). NULL — сегодняшнее
        состояние всех строк (DRF-2537) — уходит как None, и клиент тогда
        поле не шлёт: «не проверять», а не «отказать»."""
        stub = _stub(monkeypatch)
        me = _identity(home, ME, AYLA_UID)
        known = _proxy(home, me)
        RemoteBookingProxy.all_tenants.filter(pk=known.pk).update(
            last_applied_appointment_version=3
        )
        unknown = _proxy(home, me)

        assert _confirm(client, known, _when(9)).status_code == 200
        assert _confirm(client, unknown, _when(9)).status_code == 200

        assert [c["expected_version"] for c in stub.calls] == [3, None]
