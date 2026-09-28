"""DRF-2584 п.4: недоставленное напоминание — число операторам, а не находка замера.

Двенадцать ``FAILED`` нашлись разовым замером через полтора месяца: число
жило только в строке журнала ``bookings.dispatch.summary`` и в аудите.
Теперь прогон с отказом отправки зовёт ``alerting.page`` с двумя числами —
за прогон и за окно — и адресом причины.

Узлы стоят на паре: прогон с отказом — страница есть; прогон без отказа —
её нет. Рядом: окно считает только свои сутки; в тексте нет человека;
сбой страницы не ломает прогон.
"""

from __future__ import annotations

import re
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.booking.models import BookingReminder
from apps.bookings.tasks import FAILED_WINDOW_DAYS, send_due_reminders
from apps.channels.max.outbound import MaxAPIError
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

PAGE = "apps.observability.alerting.page"


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="rem-2584", name="Salon 2584")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-2584",
        chat_id="chat-2584",
        phone="79990002584",
        client_name="Вера",
    )


def _reminder(tenant, bot_user, *, yc_id: str, scheduled_at, status=None) -> BookingReminder:
    return BookingReminder.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        yclients_record_id=yc_id,
        chat_id=bot_user.chat_id,
        visit_at=scheduled_at + timedelta(hours=24),
        kind=BookingReminder.Kind.DAY_BEFORE,
        status=status or BookingReminder.Status.PENDING,
        scheduled_at=scheduled_at,
        master_name="Лера",
        service_name="Массаж",
    )


def _due(tenant, bot_user, yc_id: str) -> BookingReminder:
    return _reminder(
        tenant, bot_user, yc_id=yc_id, scheduled_at=timezone.now() - timedelta(minutes=1)
    )


class TestAFailedRunIsPaged:
    def test_a_send_failure_pages_with_the_numbers(self, tenant, bot_user) -> None:
        _due(tenant, bot_user, "yc-2584-a")

        with (
            patch("apps.bookings.tasks.send_message", side_effect=MaxAPIError(403, "blocked")),
            patch(PAGE) as page,
        ):
            result = send_due_reminders()

        assert result["failed"] == 1
        page.assert_called_once()
        severity, title, body = page.call_args.args
        assert severity == "warning"
        assert "не доставлены" in title
        assert "за этот прогон не ушло: 1" in body
        assert f"со сроком за {FAILED_WINDOW_DAYS} суток: 1" in body
        assert "bookings.reminder.send_failed" in body

    def test_a_run_without_failure_pages_nothing(self, tenant, bot_user) -> None:
        """Пара: тот же прогон, отправка прошла — страницы нет."""
        _due(tenant, bot_user, "yc-2584-b")

        with patch("apps.bookings.tasks.send_message"), patch(PAGE) as page:
            result = send_due_reminders()

        assert result["sent"] == 1
        page.assert_not_called()


class TestTheNumbers:
    def test_the_window_counts_its_own_days_only(self, tenant, bot_user) -> None:
        now = timezone.now()
        _reminder(
            tenant,
            bot_user,
            yc_id="yc-2584-recent",
            scheduled_at=now - timedelta(days=2),
            status=BookingReminder.Status.FAILED,
        )
        _reminder(
            tenant,
            bot_user,
            yc_id="yc-2584-old",
            scheduled_at=now - timedelta(days=FAILED_WINDOW_DAYS + 3),
            status=BookingReminder.Status.FAILED,
        )
        _due(tenant, bot_user, "yc-2584-c")

        with (
            patch("apps.bookings.tasks.send_message", side_effect=RuntimeError("boom")),
            patch(PAGE) as page,
        ):
            send_due_reminders()

        body = page.call_args.args[2]
        # Этот прогон + позавчерашний; тот, что старше окна, не считается.
        assert f"со сроком за {FAILED_WINDOW_DAYS} суток: 2" in body

    def test_no_person_is_named(self, tenant, bot_user) -> None:
        _due(tenant, bot_user, "yc-2584-d")

        with (
            patch("apps.bookings.tasks.send_message", side_effect=MaxAPIError(403, "blocked")),
            patch(PAGE) as page,
        ):
            send_due_reminders()

        _, title, body = page.call_args.args
        shown = f"{title} {body}"
        for value in ("Вера", "79990002584", "chat-2584", "bu-2584", "yc-2584-d"):
            assert value not in shown

    def test_one_page_per_utc_hour(self, tenant, bot_user) -> None:
        _due(tenant, bot_user, "yc-2584-e")

        with (
            patch("apps.bookings.tasks.send_message", side_effect=MaxAPIError(403, "blocked")),
            patch(PAGE) as page,
        ):
            send_due_reminders()

        key = page.call_args.kwargs["dedup_key"]
        # Час, а не минута и не прогон: прогоны идут раз в 15 минут.
        assert re.fullmatch(r"bookings\.reminder\.failed:\d{4}-\d{2}-\d{2}T\d{2}", key)


class TestThePageIsBestEffort:
    def test_a_failing_page_does_not_break_the_run(self, tenant, bot_user) -> None:
        row = _due(tenant, bot_user, "yc-2584-f")

        with (
            patch("apps.bookings.tasks.send_message", side_effect=MaxAPIError(403, "blocked")),
            patch(PAGE, side_effect=RuntimeError("pager down")),
        ):
            result = send_due_reminders()

        assert result["failed"] == 1
        row.refresh_from_db()
        assert row.status == BookingReminder.Status.FAILED
