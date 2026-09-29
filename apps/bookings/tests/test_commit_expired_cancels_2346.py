"""Просроченное окно возврата добивает сервер, а не таймер в браузере (DRF-2346).

Отмена из Mini App двухшаговая: сервер переводит запись в ``CANCEL_REQUESTED``
и держит пять секунд на возврат, а завершает отмену **клиент** — таймером на
странице. Сервер её сам не добивал: ``commit_cancel`` звали только клиентская
ручка, админская отмена и деактивация мастера, а задачи на просроченное окно
в расписании не было.

Следствие: человек закрыл приложение или потерял сеть внутри этих пяти секунд —
запись остаётся ``CANCEL_REQUESTED`` **навсегда**. Напоминания не сняты (их
гасит именно завершение отмены), администратор видит «клиент попросил
отменить», мастер ждёт, слот занят. А человеку уже сказали, что запись
отменена.

Правило: истекло окно — отмену завершает сервер, тем же переходом, что и
клиент. Два свойства, которые обязаны выполняться и проверяются отдельно:

* **возврат сильнее подметания** — то, что человек успел вернуть, не
  добивается; различие идёт по состоянию под замком строки, а не по гонке;
* **напоминания снимаются в том же ходе** — завершить отмену, не сняв их,
  значило бы заменить один дефект другим: человек не придёт, а бот напомнит.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone
from uuid import uuid4

import pytest
from django.utils import timezone

from apps.booking.models import BookingReminder, BookingRequest
from apps.booking.services.transitions import (
    UNDO_WINDOW_SECONDS,
    commit_cancel,
    request_cancel,
    undo_cancel,
)
from apps.bookings.tasks import commit_expired_cancels
from apps.catalog.models import CatalogMaster, CatalogService
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="exp-2346", name="Exp", timezone="Europe/Moscow")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="2346",
        chat_id="2346",
    )


@pytest.fixture
def master(tenant: Tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=2346,
        external_updated_at=datetime(2026, 5, 18, tzinfo=dt_timezone.utc),
        name="Лера",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        ayla_user_id=uuid4(),
    )


@pytest.fixture
def service(tenant: Tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=2346,
        external_updated_at=datetime(2026, 5, 18, tzinfo=dt_timezone.utc),
        slug="massage-2346",
        name="Массаж",
        duration_min=60,
        is_active=True,
    )


def _booking(tenant, bot_user, master, service) -> BookingRequest:
    return BookingRequest.objects.create(
        tenant=tenant,
        bot_user=bot_user,
        service=service,
        master=master,
        service_name=service.name,
        master_name=master.name,
        client_name="Ольга",
        client_phone="+7-000",
        visit_at=timezone.now() + timedelta(days=3),
        duration_min=60,
        status=BookingRequest.Status.CONFIRMED,
    )


def _age_the_request(booking: BookingRequest, *, seconds: int) -> None:
    """Состарить запрос отмены — окно возврата считается от его времени."""
    BookingRequest.all_tenants.filter(pk=booking.pk).update(
        cancel_requested_at=timezone.now() - timedelta(seconds=seconds),
    )


def _pending_reminder(booking: BookingRequest, bot_user: BotUser) -> BookingReminder:
    visit_at = booking.visit_at
    assert visit_at is not None  # строка создана с визитом — сужение для типов
    return BookingReminder.all_tenants.create(
        tenant=booking.tenant,
        bot_user=bot_user,
        booking_request=booking,
        chat_id=bot_user.chat_id,
        visit_at=visit_at,
        kind=BookingReminder.Kind.DAY_BEFORE,
        status=BookingReminder.Status.PENDING,
        scheduled_at=visit_at - timedelta(hours=24),
    )


class TestTheServerFinishesWhatTheBrowserStarted:
    def test_an_expired_window_is_committed(
        self,
        tenant,
        bot_user,
        master,
        service,
    ) -> None:
        """Человек закрыл приложение — отмена всё равно доходит до конца."""
        booking = _booking(tenant, bot_user, master, service)
        request_cancel(booking, actor=bot_user)
        _age_the_request(booking, seconds=UNDO_WINDOW_SECONDS + 1)

        commit_expired_cancels()

        booking.refresh_from_db()
        assert booking.status == BookingRequest.Status.CANCELLED

    def test_the_reminders_go_with_it(
        self,
        tenant,
        bot_user,
        master,
        service,
    ) -> None:
        """Иначе человек не придёт, а бот ему напомнит — новый дефект вместо старого."""
        booking = _booking(tenant, bot_user, master, service)
        reminder = _pending_reminder(booking, bot_user)
        request_cancel(booking, actor=bot_user)
        _age_the_request(booking, seconds=UNDO_WINDOW_SECONDS + 1)

        commit_expired_cancels()

        reminder.refresh_from_db()
        assert reminder.status == BookingReminder.Status.CANCELLED

    def test_the_sweep_reports_what_it_did(
        self,
        tenant,
        bot_user,
        master,
        service,
    ) -> None:
        booking = _booking(tenant, bot_user, master, service)
        request_cancel(booking, actor=bot_user)
        _age_the_request(booking, seconds=UNDO_WINDOW_SECONDS + 1)

        assert commit_expired_cancels()["committed"] == 1


class TestTheUndoIsStrongerThanTheSweep:
    """Отрицательная проба: вернувший не должен потерять запись."""

    def test_a_returned_booking_is_not_swept(
        self,
        tenant,
        bot_user,
        master,
        service,
    ) -> None:
        booking = _booking(tenant, bot_user, master, service)
        request_cancel(booking, actor=bot_user)
        undo_cancel(booking, actor=bot_user)
        # Состаряем УЖЕ вернувшуюся строку: подметание не вправе смотреть на
        # время в отрыве от состояния.
        BookingRequest.all_tenants.filter(pk=booking.pk).update(
            cancel_requested_at=timezone.now() - timedelta(seconds=UNDO_WINDOW_SECONDS + 60),
        )

        commit_expired_cancels()

        booking.refresh_from_db()
        assert booking.status == BookingRequest.Status.CONFIRMED

    def test_a_returned_booking_keeps_its_reminders(
        self,
        tenant,
        bot_user,
        master,
        service,
    ) -> None:
        booking = _booking(tenant, bot_user, master, service)
        reminder = _pending_reminder(booking, bot_user)
        request_cancel(booking, actor=bot_user)
        undo_cancel(booking, actor=bot_user)
        BookingRequest.all_tenants.filter(pk=booking.pk).update(
            cancel_requested_at=timezone.now() - timedelta(seconds=UNDO_WINDOW_SECONDS + 60),
        )

        commit_expired_cancels()

        reminder.refresh_from_db()
        assert reminder.status == BookingReminder.Status.PENDING


class TestTheSweepTouchesNothingElse:
    def test_a_window_still_open_is_left_alone(
        self,
        tenant,
        bot_user,
        master,
        service,
    ) -> None:
        """Пока окно не истекло, возврат ещё возможен — трогать нельзя."""
        booking = _booking(tenant, bot_user, master, service)
        request_cancel(booking, actor=bot_user)

        commit_expired_cancels()

        booking.refresh_from_db()
        assert booking.status == BookingRequest.Status.CANCEL_REQUESTED

    def test_a_confirmed_booking_is_left_alone(
        self,
        tenant,
        bot_user,
        master,
        service,
    ) -> None:
        booking = _booking(tenant, bot_user, master, service)

        commit_expired_cancels()

        booking.refresh_from_db()
        assert booking.status == BookingRequest.Status.CONFIRMED

    def test_an_already_cancelled_booking_is_not_counted_twice(
        self,
        tenant,
        bot_user,
        master,
        service,
    ) -> None:
        booking = _booking(tenant, bot_user, master, service)
        request_cancel(booking, actor=bot_user)
        commit_cancel(booking, actor=bot_user)
        _age_the_request(booking, seconds=UNDO_WINDOW_SECONDS + 1)

        assert commit_expired_cancels()["committed"] == 0

    def test_one_stuck_row_does_not_stop_the_others(
        self,
        tenant,
        bot_user,
        master,
        service,
    ) -> None:
        """Подметание — не всё-или-ничего: одна больная строка не глушит остальные."""
        first = _booking(tenant, bot_user, master, service)
        second = _booking(tenant, bot_user, master, service)
        for row in (first, second):
            request_cancel(row, actor=bot_user)
            _age_the_request(row, seconds=UNDO_WINDOW_SECONDS + 1)
        # Первая строка «уезжает» из-под подметания между выборкой и переходом.
        BookingRequest.all_tenants.filter(pk=first.pk).update(
            status=BookingRequest.Status.CONFIRMED,
        )

        result = commit_expired_cancels()

        second.refresh_from_db()
        assert second.status == BookingRequest.Status.CANCELLED
        assert result["committed"] == 1
