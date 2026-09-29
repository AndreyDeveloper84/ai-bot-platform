"""DRF-2454: часовой детектор не объявляет состоявшимся то, чего не было.

Красное (замер окна b6 на стенде, 24.09): 9 из 11 строк несут ``completed_at``
при ``status=confirmed``, а у их каталожных двойников ``completed`` 3,
``cancelled`` 3, ``confirmed`` 2, ``awaiting_payment`` 1. **Три отменённых визита
и один неоплаченный объявлены состоявшимися.** Причина — ключ на
``BookingRequest.status``, который **никто не ведёт**: входящие события канона до
этой колонки не доезжают, отмены живут в зеркале ``RemoteBookingProxy``.

Здесь — обе стороны, потому что каждая по отдельности зелёная и лживая:

* **не штампует то, чего не было** — отменённый, неоплаченный, не подтверждённый,
  без зеркала, с неоднозначным ключом, без человека в строке;
* **штампует то, что было** — визит, который канон знает и не отменял, получает
  и штамп, и событие. Без этого узла мы доказали бы умение молчать, а не работать.

Плюс два свойства, которые легко потерять при такой правке:

* отказ **назван** — счётчик ``skipped`` и причина в сводке; «пропустили N» без
  причины — то же молчание, из которого вырос этот лист;
* отказ **ничего не пишет**: ни ``completed_at``, ни ``completed_by``, ни события
  в очередь. Последствия заперты гейтом ``completed_by=system``, но ложная строка
  в очереди — уже необратимый факт после её разбора.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from django.utils import timezone

from apps.booking.models import BookingRequest, RemoteBookingProxy
from apps.bookings.completion_evidence import (
    MIRROR_ALLOWS,
    MIRROR_REFUSES,
    REASON_AMBIGUOUS,
    REASON_NO_KEY,
    REASON_NO_MIRROR,
    mirror_evidence,
)
from apps.bookings.tasks import detect_completed_bookings
from apps.eventbus.models import DomainEvent
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

#: Визит кончился давно: время + длительность + льгота позади.
PAST_MINUTES = -300


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="ev-2454", name="Salon 2454")


@pytest.fixture
def customer(tenant) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="ev-2454-1")


def _booking(tenant, customer, *, minutes: int = PAST_MINUTES, bot_user=None) -> BookingRequest:
    visit_at = timezone.now() + dt.timedelta(minutes=minutes)
    return BookingRequest.objects.create(
        tenant=tenant,
        bot_user=customer if bot_user is None else bot_user,
        service_name="Маникюр",
        client_name="Customer",
        client_phone="snapshot",
        visit_at=visit_at,
        duration_min=60,
        status=BookingRequest.Status.CONFIRMED,
        source="bot",
        booking_source="external",
    )


def _mirror(tenant, customer, booking, *, status: str) -> RemoteBookingProxy:
    return RemoteBookingProxy.all_tenants.create(
        appointment_id=uuid.uuid4(),
        tenant=tenant,
        bot_user=customer,
        start_at=booking.visit_at,
        end_at=booking.visit_at + dt.timedelta(minutes=60),
        status=status,
        source=RemoteBookingProxy.Source.MOBILE_APP,
    )


def _events() -> int:
    return DomainEvent.objects.filter(event_name="booking.completed").count()


# ─── не штампует то, чего не было ──────────────────────────────────────────


class TestTheCancelledVisitIsNeverCompleted:
    @pytest.mark.parametrize("mirror_status", MIRROR_REFUSES)
    def test_a_mirror_that_says_it_did_not_happen_blocks_the_stamp(
        self, tenant, customer, mirror_status: str
    ) -> None:
        booking = _booking(tenant, customer)
        _mirror(tenant, customer, booking, status=mirror_status)

        counters = detect_completed_bookings()

        booking.refresh_from_db()
        assert booking.completed_at is None
        assert booking.completed_by == ""
        assert _events() == 0  # в очередь ложный факт не попал
        assert counters["scanned"] == 1  # положительно: строку рассматривали
        assert counters["skipped"] == 1
        assert counters["emitted"] == 0

    def test_a_row_the_canon_does_not_know_is_not_stamped(self, tenant, customer) -> None:
        """Цена выбора названа: без зеркала строка автоматически не закрывается."""
        booking = _booking(tenant, customer)

        counters = detect_completed_bookings()

        booking.refresh_from_db()
        assert booking.completed_at is None
        assert counters["skipped"] == 1
        assert mirror_evidence(booking) == (False, REASON_NO_MIRROR)

    def test_two_mirrors_on_one_key_are_not_guessed_between(self, tenant, customer) -> None:
        booking = _booking(tenant, customer)
        _mirror(tenant, customer, booking, status=RemoteBookingProxy.Status.CONFIRMED)
        _mirror(tenant, customer, booking, status=RemoteBookingProxy.Status.CANCELLED)

        counters = detect_completed_bookings()

        booking.refresh_from_db()
        assert booking.completed_at is None
        assert counters["skipped"] == 1
        assert mirror_evidence(booking) == (False, REASON_AMBIGUOUS)

    def test_a_row_without_a_person_has_no_key_at_all(self, tenant, customer) -> None:
        booking = _booking(tenant, customer)
        BookingRequest.all_tenants.filter(pk=booking.pk).update(bot_user=None)
        booking.refresh_from_db()

        assert mirror_evidence(booking) == (False, REASON_NO_KEY)

    def test_an_unknown_mirror_status_is_a_refusal_not_a_pass(self, tenant, customer) -> None:
        """Список разрешающих — положительный: новое состояние канона не проходит."""
        booking = _booking(tenant, customer)
        _mirror(tenant, customer, booking, status="some_future_state")

        may_stamp, reason = mirror_evidence(booking)

        assert may_stamp is False
        assert reason == "mirror_some_future_state"


# ─── штампует то, что было ─────────────────────────────────────────────────


class TestTheVisitThatHappenedIsStillCompleted:
    @pytest.mark.parametrize("mirror_status", MIRROR_ALLOWS)
    def test_a_visit_the_canon_did_not_cancel_gets_the_stamp_and_the_event(
        self, tenant, customer, mirror_status: str
    ) -> None:
        booking = _booking(tenant, customer)
        _mirror(tenant, customer, booking, status=mirror_status)

        counters = detect_completed_bookings()

        booking.refresh_from_db()
        assert booking.completed_at is not None
        assert booking.completed_by == "system"
        assert _events() == 1
        assert counters["emitted"] == 1
        assert counters["skipped"] == 0

    def test_the_positive_path_stays_exactly_once(self, tenant, customer) -> None:
        # DRF-2519: свидетельством стал только ``completed``. Прежде здесь
        # стоял ``CONFIRMED`` — узел про «ровно один раз» не изменился по
        # предмету, изменился вход, который считается доказательством.
        booking = _booking(tenant, customer)
        _mirror(tenant, customer, booking, status=RemoteBookingProxy.Status.COMPLETED)

        first = detect_completed_bookings()
        second = detect_completed_bookings()

        assert first["emitted"] == 1
        assert second["emitted"] == 0  # второй тик не находит уже закрытую строку
        assert _events() == 1

    def test_one_cancelled_row_does_not_stop_the_neighbour(self, tenant, customer) -> None:
        """Отказ по одной строке не должен гасить работу по другой."""
        cancelled = _booking(tenant, customer)
        _mirror(tenant, customer, cancelled, status=RemoteBookingProxy.Status.CANCELLED)
        other_customer = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="ev-2454-2"
        )
        happened = _booking(tenant, other_customer, bot_user=other_customer)
        _mirror(tenant, other_customer, happened, status=RemoteBookingProxy.Status.COMPLETED)

        counters = detect_completed_bookings()

        cancelled.refresh_from_db()
        happened.refresh_from_db()
        assert cancelled.completed_at is None
        assert happened.completed_at is not None
        assert counters == {
            "scanned": 2,
            "emitted": 1,
            "raced": 0,
            "emit_failed": 0,
            "skipped": 1,
        }


# ─── отказ назван ──────────────────────────────────────────────────────────


class TestTheRefusalIsNamed:
    def test_the_summary_log_names_every_reason(self, tenant, customer, caplog) -> None:
        import logging

        cancelled = _booking(tenant, customer)
        _mirror(tenant, customer, cancelled, status=RemoteBookingProxy.Status.CANCELLED)
        other = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="ev-2454-3"
        )
        no_mirror = _booking(tenant, other, bot_user=other)
        assert no_mirror.completed_at is None  # положительно: строка в выборке

        logger_name = "apps.bookings.tasks"
        target = logging.getLogger(logger_name)
        target.addHandler(caplog.handler)
        try:
            with caplog.at_level(logging.INFO, logger=logger_name):
                detect_completed_bookings()
        finally:
            target.removeHandler(caplog.handler)

        text = caplog.text
        assert "skipped=2" in text
        assert "mirror_cancelled=1" in text
        assert f"{REASON_NO_MIRROR}=1" in text
