"""DRF-2342 — напоминание о визите не уходит, пока человеком занят оператор.

Жалоба: администратор переносит запись руками, а боту в этот же момент
наступает T-2, и человек получает напоминание о СТАРОМ времени — ровно о
том, что сейчас меняют.

### Почему это не закрыто существующими проверками

В `send_due_reminders` уже есть две проверки, которые легко принять за эту,
и обе про другое:

* `_recheck_booking_state` откладывает ход при `CANCEL_REQUESTED` /
  `RESCHEDULE_REQUESTED` — это окно отмены ~5 секунд, а не работа оператора:
  пока администратор переносит запись руками, статус остаётся `CONFIRMED`;
* `_reminders_muted` — личный выключатель человека («не присылай
  напоминания»), он и должен работать независимо от передачи.

### Что такое «занят оператор»

Мьют ходит за ЧЕЛОВЕКОМ, а не за диалогом (DRF-1015): открытая или взятая
в работу задача `HANDOFF` у любой оболочки той же канальной личности —
или любой её диалог в `HUMAN_HANDOFF`. Знание доступно отсюда обычным
чтением БД; до этого листа проактив его просто не спрашивал.

### Что здесь НЕ решается

Срок молчания (задача администратора может висеть долго — DRF-1015) — часть
ответа владельца. Пока его нет, строка откладывается: `PENDING`, следующий
пятнадцатиминутный тик проверит заново. Место для будущего срока одно —
:func:`apps.bookings.tasks._handoff_silenced`.

Питание (вода, итоги дня, наблюдение диетолога, «как всё прошло») этим
листом не трогается: оно и есть разница между двумя правилами, которые
сейчас у владельца.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.booking.models import BookingReminder
from apps.bookings.tasks import send_due_reminders
from apps.conversations.models import Conversation
from apps.handoff.models import AdminTask
from apps.identity.models import BotUser, UserPreferences
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="rem-2342", name="Salon 2342", manager_chat_id="mgr-2342")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-2342",
        chat_id="chat-2342",
        phone="79991234567",
        client_name="Anna",
    )


def _due_reminder(tenant: Tenant, bot_user: BotUser, *, yc_id: str = "yc-2342") -> BookingReminder:
    past = timezone.now() - timedelta(minutes=5)
    return BookingReminder.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        yclients_record_id=yc_id,
        chat_id=bot_user.chat_id,
        visit_at=past + timedelta(hours=2),
        kind=BookingReminder.Kind.TWO_HOURS,
        status=BookingReminder.Status.PENDING,
        scheduled_at=past,
        master_name="Лера",
        service_name="Массаж",
    )


def _an_open_task(tenant: Tenant, bot_user: BotUser) -> AdminTask:
    with patch("apps.tenancy.context.current_tenant", return_value=tenant):
        conversation = Conversation.all_tenants.create(
            tenant=tenant, bot_user=bot_user, state=Conversation.State.HUMAN_HANDOFF
        )
    return AdminTask.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        conversation=conversation,
        task_type=AdminTask.TaskType.HANDOFF,
        status=AdminTask.Status.OPEN,
    )


class TestTheReminderWaitsForTheOperator:
    def test_an_open_task_holds_the_reminder(self, tenant: Tenant, bot_user: BotUser) -> None:
        """Жалоба целиком: человеком занят оператор, T-2 наступило."""
        row = _due_reminder(tenant, bot_user)
        _an_open_task(tenant, bot_user)

        with patch("apps.bookings.tasks.send_message") as send:
            result = send_due_reminders()

        assert not send.called, "напоминание ушло человеку, которым занят оператор"
        row.refresh_from_db()
        assert row.status == BookingReminder.Status.PENDING, "строка должна ждать, а не сгореть"
        assert result["deferred"] >= 1

    def test_a_task_on_another_shell_holds_it_too(self, tenant: Tenant, bot_user: BotUser) -> None:
        """Мьют ходит за человеком: задача заведена на другой оболочке той же личности."""
        other_tenant = Tenant.objects.create(slug="rem-2342-b", name="Другой салон")
        twin = BotUser.all_tenants.create(
            tenant=other_tenant,
            channel=bot_user.channel,
            channel_user_id=bot_user.channel_user_id,
            chat_id="chat-2342-b",
            phone=bot_user.phone,
            client_name=bot_user.client_name,
        )
        row = _due_reminder(tenant, bot_user)
        _an_open_task(other_tenant, twin)

        with patch("apps.bookings.tasks.send_message") as send:
            send_due_reminders()

        assert not send.called
        row.refresh_from_db()
        assert row.status == BookingReminder.Status.PENDING


class TestTheOtherSideIsNotBroken:
    def test_without_a_task_the_reminder_goes_as_before(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """Чинить выключением — не чинить: обычное напоминание уходит."""
        row = _due_reminder(tenant, bot_user)

        with patch("apps.bookings.tasks.send_message") as send:
            send_due_reminders()

        assert send.called
        row.refresh_from_db()
        assert row.status == BookingReminder.Status.SENT

    def test_a_resolved_task_does_not_hold_it(self, tenant: Tenant, bot_user: BotUser) -> None:
        """Задача закрыта — молчать больше не о чем."""
        task = _an_open_task(tenant, bot_user)
        task.status = AdminTask.Status.RESOLVED
        task.save(update_fields=["status"])
        Conversation.all_tenants.filter(bot_user=bot_user).update(state=Conversation.State.IDLE)
        _due_reminder(tenant, bot_user)

        with patch("apps.bookings.tasks.send_message") as send:
            send_due_reminders()

        assert send.called

    def test_the_personal_switch_still_works_on_its_own(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """Личный выключатель — про другое и действует независимо от передачи."""
        UserPreferences.all_tenants.create(tenant=tenant, bot_user=bot_user, notify_reminders=False)
        row = _due_reminder(tenant, bot_user)

        with patch("apps.bookings.tasks.send_message") as send:
            result = send_due_reminders()

        assert not send.called
        row.refresh_from_db()
        assert row.status == BookingReminder.Status.MUTED, "это выключатель, а не ожидание"
        assert result["muted"] >= 1
