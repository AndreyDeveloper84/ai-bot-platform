"""«Передал администратору» — значит адресат есть (DRF-2338).

Человек жмёт «Перенести» под напоминанием, бот отвечает «Передал
администратору, скоро напишут» — и не передавал никому: на этом месте стоял
``TODO(Phase 2)``. Строка аудита и событие остаются в системе, но ни задачи в
очереди операторов, ни адресата у неё нет; человек ждёт звонка, которого не
будет.

Передача человеку в этом же репозитории работает по-настоящему:
:func:`apps.handoff.services.create_admin_task` создаёт ``AdminTask``,
адресует его (``resolve_addressee``) и переводит разговор в
``HUMAN_HANDOFF``. Лист подключает перенос к этому пути.

**Текст не меняется** — он становится правдой.

Красных до правки — пять; ещё два узла зелёные и до неё: они сторожа, а не
измерение (текст обещания и состояние напоминания не должны были поменяться).
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from apps.booking.models import BookingReminder
from apps.bookings.callbacks import (
    REPLY_ALREADY_HANDLED,
    REPLY_RESCHEDULE,
    BookingReminderCallbackSkill,
)
from apps.conversations.models import Conversation
from apps.handoff.models import AdminTask
from apps.identity.models import BotUser
from apps.skills.base import SkillContext
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

RECORD_ID = "yc-2338-778899"


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="resched-2338", name="Salon 2338")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-2338",
        chat_id="chat-2338",
        display_name="Мария",
    )


@pytest.fixture
def conversation(tenant: Tenant, bot_user: BotUser) -> Conversation:
    return Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)


@pytest.fixture
def reminder(tenant: Tenant, bot_user: BotUser) -> BookingReminder:
    visit_at = timezone.now() + timedelta(hours=24)
    return BookingReminder.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        yclients_record_id=RECORD_ID,
        chat_id=bot_user.chat_id,
        visit_at=visit_at,
        kind=BookingReminder.Kind.DAY_BEFORE,
        status=BookingReminder.Status.SENT_NO_REPLY,
        scheduled_at=visit_at - timedelta(hours=24),
        master_name="Лера",
        service_name="Массаж",
    )


def _press_reschedule(reminder: BookingReminder, bot_user: BotUser, conversation: Conversation):
    ctx = SkillContext(
        conversation=conversation,
        bot_user=bot_user,
        message_text=f"cb:rem:reschedule:{reminder.pk}",
        has_attachments=False,
    )
    return BookingReminderCallbackSkill().handle(ctx)


def _tasks(conversation: Conversation) -> list[AdminTask]:
    return list(AdminTask.all_tenants.filter(conversation=conversation).order_by("created_at"))


class TestThePromiseHasAnAddressee:
    def test_a_task_exists_after_the_bot_says_it_handed_over(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation, reminder
    ) -> None:
        """Сердце листа: сказал «передал» — задача у операторов есть."""
        assert _tasks(conversation) == []

        with tenant_scope(tenant):
            result = _press_reschedule(reminder, bot_user, conversation)

        assert result.reply_text == REPLY_RESCHEDULE  # наличие: тот самый ответ
        (task,) = _tasks(conversation)
        assert task.status == AdminTask.Status.OPEN
        assert task.bot_user_id == bot_user.pk

    def test_the_task_names_the_booking_so_an_operator_can_act(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation, reminder
    ) -> None:
        """Задача без записи — та же отписка: оператору нечего переносить."""
        with tenant_scope(tenant):
            _press_reschedule(reminder, bot_user, conversation)

        (task,) = _tasks(conversation)
        assert RECORD_ID in task.reason, task.reason

    def test_the_task_is_addressed(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation, reminder
    ) -> None:
        """«Скоро напишут» — значит у задачи есть адресат (DRF-1488), не только строка."""
        with tenant_scope(tenant):
            _press_reschedule(reminder, bot_user, conversation)

        (task,) = _tasks(conversation)
        assert task.assigned_to_id is not None or task.assigned_queue, (
            task.assigned_to_id,
            task.assigned_queue,
        )

    def test_the_conversation_waits_for_the_human(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation, reminder
    ) -> None:
        with tenant_scope(tenant):
            _press_reschedule(reminder, bot_user, conversation)

        conversation.refresh_from_db()
        assert conversation.state == Conversation.State.HUMAN_HANDOFF


class TestTheHandoverIsAllOrNothing:
    def test_a_failed_handover_leaves_the_reminder_alone(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation, reminder
    ) -> None:
        """Ради этого и транзакция: не передали — не пометили «перенос запрошен».

        Иначе человек остался бы со строкой «запрошено», которую никто не
        держит, а повтор получил бы «уже обработано».
        """
        with tenant_scope(tenant):
            with mock.patch(
                "apps.bookings.callbacks.create_admin_task", side_effect=RuntimeError("boom")
            ):
                with pytest.raises(RuntimeError):
                    _press_reschedule(reminder, bot_user, conversation)

        reminder.refresh_from_db()
        assert reminder.status == BookingReminder.Status.SENT_NO_REPLY
        assert reminder.replied_at is None
        assert _tasks(conversation) == []

    def test_after_a_failure_the_person_can_press_again(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation, reminder
    ) -> None:
        """Положительная пара: откат не запирает кнопку."""
        with tenant_scope(tenant):
            with mock.patch(
                "apps.bookings.callbacks.create_admin_task", side_effect=RuntimeError("boom")
            ):
                with pytest.raises(RuntimeError):
                    _press_reschedule(reminder, bot_user, conversation)
            result = _press_reschedule(reminder, bot_user, conversation)

        assert result.reply_text == REPLY_RESCHEDULE
        assert len(_tasks(conversation)) == 1


class TestTheTaskTypeKeepsTheMuteNarrow:
    def test_the_task_is_manual_not_handoff(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation, reminder
    ) -> None:
        """MANUAL, а не HANDOFF: `global_handoff_muted` фильтрует HANDOFF, и
        глобальный диалог человека остаётся с ботом — молчит только салонный."""
        with tenant_scope(tenant):
            _press_reschedule(reminder, bot_user, conversation)

        (task,) = _tasks(conversation)
        assert task.task_type == AdminTask.TaskType.MANUAL

    def test_the_reason_carries_no_phone(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation, reminder
    ) -> None:
        """DRF-1039: телефон клиента персоналу не показывается."""
        BotUser.all_tenants.filter(pk=bot_user.pk).update(phone="+79995550111")

        with tenant_scope(tenant):
            _press_reschedule(reminder, bot_user, conversation)

        (task,) = _tasks(conversation)
        assert RECORD_ID in task.reason  # наличие: строка та самая
        assert "+79995550111" not in task.reason
        assert "9995550111" not in task.reason


class TestTheTextIsUnchanged:
    def test_the_words_stay_the_owners(self) -> None:
        """Правим поведение, а не слова: текст обещания прежний, теперь он правда."""
        assert REPLY_RESCHEDULE == "Передал администратору, скоро напишут."


class TestTheSecondPressChangesNothing:
    def test_a_repeat_makes_no_second_task(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation, reminder
    ) -> None:
        with tenant_scope(tenant):
            _press_reschedule(reminder, bot_user, conversation)
            again = _press_reschedule(reminder, bot_user, conversation)

        assert again.reply_text == REPLY_ALREADY_HANDLED
        assert len(_tasks(conversation)) == 1

    def test_the_reminder_still_moves_to_requested(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation, reminder
    ) -> None:
        """Состояние напоминания — как было: лист добавляет адресата, а не меняет учёт."""
        with tenant_scope(tenant):
            _press_reschedule(reminder, bot_user, conversation)

        reminder.refresh_from_db()
        assert reminder.status == BookingReminder.Status.RESCHEDULE_REQUESTED
        assert reminder.replied_at is not None
