"""Задача с адресатом и сроком (DRF-1488).

Замер, из которого выросла эта правка: `AdminTask.all_tenants` за всю
историю пилота, с 11.08 по 04.09.2026 — десять задач, десять раз
``assigned_to = None``. Время до закрытия от 0 секунд (`35a76650`,
заведена и закрыта в ту же секунду) до 20 часов (`6a5a881e`). Разброс без
единого назначения — это не медленный процесс, а его отсутствие; и всё это
время клиенту не отвечает бот, потому что mute снимается только закрытием
задачи (DRF-980).

Что здесь пришпилено:

* задача не бывает без адресата — либо named-оператор, либо явная дежурная
  очередь, и обе пустые не запускаются (boot-check ``handoff.E001``);
* «взяли» — это отдельный факт (``claimed_at``), а не строка в статусе;
* просрочка эскалируется РОВНО один раз, сколько бы раз ни прогнали sweep;
* вовремя взятая задача эскалацию не порождает — парная положительная
  проверка к предыдущей на тех же данных (DRF-1411).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone
from io import StringIO

from apps.audit.models import AuditLog
from apps.conversations.models import Conversation
from apps.handoff.assignment import claim, resolve_addressee
from apps.handoff.checks import check_handoff_addressee_configured
from apps.handoff.escalation import sweep_unclaimed_tasks
from apps.handoff.models import AdminTask
from apps.handoff.notify import build_unclaimed_notification
from apps.handoff.services import create_admin_task
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="hs-sla", name="HSla Salon")


@pytest.fixture
def conversation(tenant) -> Conversation:
    bot_user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="sla-user",
        display_name="Ivan",
        phone="+79990001122",
    )
    return Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)


@pytest.fixture
def send(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        "apps.handoff.notify.send_message",
        lambda *, chat_id, text, attachments=None, timeout=10.0: (
            calls.append({"chat_id": chat_id, "text": text}) or {}
        ),
    )
    return calls


def _task(tenant, conversation, **kwargs) -> AdminTask:
    with tenant_scope(tenant):
        return create_admin_task(
            conversation, task_type=AdminTask.TaskType.HANDOFF, reason="нужен человек", **kwargs
        )


def _age(task: AdminTask, minutes: int) -> None:
    """Backdate the task so the sweep sees a real wait, not a mocked clock."""

    AdminTask.all_tenants.filter(pk=task.pk).update(
        created_at=timezone.now() - timedelta(minutes=minutes)
    )
    task.refresh_from_db()


# --------------------------------------------------------------------------- #
# Адресат                                                                      #
# --------------------------------------------------------------------------- #
class TestEveryTaskIsAddressed:
    def test_created_task_has_a_non_empty_addressee(self, tenant, conversation, settings):
        settings.HANDOFF_DUTY_OPERATORS = []
        settings.HANDOFF_DUTY_QUEUE = "duty"

        task = _task(tenant, conversation)

        assert task.is_addressed
        assert task.addressee == "queue:duty"
        assert task.assigned_queue == "duty"

    def test_named_operator_wins_over_the_queue(self, tenant, conversation, settings):
        operator = get_user_model().objects.create_user(username="anna", password="x")  # noqa: S106
        settings.HANDOFF_DUTY_OPERATORS = ["anna"]

        task = _task(tenant, conversation)

        assert task.assigned_to_id == operator.pk
        assert task.assigned_queue == ""
        assert task.addressee == "anna"

    def test_roster_picks_the_least_loaded_operator(self, tenant, conversation, settings):
        User = get_user_model()
        busy = User.objects.create_user(username="anna", password="x")  # noqa: S106
        free = User.objects.create_user(username="boris", password="x")  # noqa: S106
        settings.HANDOFF_DUTY_OPERATORS = ["anna", "boris"]
        first = _task(tenant, conversation)
        assert first.assigned_to_id == busy.pk, "алфавит решает только при равной загрузке"

        second = _task(tenant, conversation)

        assert second.assigned_to_id == free.pk

    def test_inactive_roster_falls_back_to_the_queue_not_to_nobody(
        self, tenant, conversation, settings
    ):
        User = get_user_model()
        User.objects.create_user(username="anna", password="x", is_active=False)  # noqa: S106
        settings.HANDOFF_DUTY_OPERATORS = ["anna"]
        settings.HANDOFF_DUTY_QUEUE = "duty"

        operator, queue = resolve_addressee()

        assert operator is None
        assert queue == "duty"

    def test_boot_refuses_a_configuration_with_no_addressee(self, settings):
        settings.HANDOFF_DUTY_OPERATORS = []
        settings.HANDOFF_DUTY_QUEUE = ""

        errors = check_handoff_addressee_configured(None)

        assert [e.id for e in errors] == ["handoff.E001"]

    def test_default_configuration_boots(self, settings):
        """Положительная стража: штатная конфигурация проверку проходит."""

        settings.HANDOFF_DUTY_OPERATORS = []
        settings.HANDOFF_DUTY_QUEUE = "duty"

        assert check_handoff_addressee_configured(None) == []


class TestPickupIsAFact:
    def test_claim_stamps_the_moment_and_the_operator(self, tenant, conversation, settings):
        settings.HANDOFF_DUTY_OPERATORS = []
        task = _task(tenant, conversation)
        assert task.claimed_at is None
        operator = get_user_model().objects.create_user(username="anna", password="x")  # noqa: S106

        assert claim(task, operator) is True

        task.refresh_from_db()
        assert task.claimed_at is not None
        assert task.assigned_to_id == operator.pk

    def test_second_claim_does_not_move_the_clock(self, tenant, conversation, settings):
        settings.HANDOFF_DUTY_OPERATORS = []
        task = _task(tenant, conversation)
        User = get_user_model()
        first = User.objects.create_user(username="anna", password="x")  # noqa: S106
        second = User.objects.create_user(username="boris", password="x")  # noqa: S106
        claim(task, first)
        first_claim_at = AdminTask.all_tenants.get(pk=task.pk).claimed_at

        assert claim(task, second) is False

        fresh = AdminTask.all_tenants.get(pk=task.pk)
        assert fresh.claimed_at == first_claim_at
        assert fresh.assigned_to_id == first.pk


# --------------------------------------------------------------------------- #
# Предел ожидания                                                              #
# --------------------------------------------------------------------------- #
class TestPickupSla:
    def test_unclaimed_task_escalates_exactly_once(self, tenant, conversation, settings, send):
        settings.HANDOFF_DUTY_OPERATORS = []
        settings.HANDOFF_PICKUP_SLA_MINUTES = 15
        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = ["555000"]
        task = _task(tenant, conversation)
        _age(task, 40)
        assert len(send) == 1, "уведомление о создании (DRF-1029) — оно уже ушло"
        send.clear()

        assert sweep_unclaimed_tasks() == 1
        assert len(send) == 1
        assert "Эскалация без ответа" in send[0]["text"]

        assert sweep_unclaimed_tasks() == 0
        assert sweep_unclaimed_tasks() == 0
        assert len(send) == 1, "повторный прогон не смеет уведомить второй раз"
        task.refresh_from_db()
        assert task.pickup_escalated_at is not None

    def test_task_claimed_in_time_is_never_escalated(self, tenant, conversation, settings, send):
        """Парная положительная проверка на тех же данных."""

        settings.HANDOFF_DUTY_OPERATORS = []
        settings.HANDOFF_PICKUP_SLA_MINUTES = 15
        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = ["555000"]
        taken = _task(tenant, conversation)
        _age(taken, 40)
        operator = get_user_model().objects.create_user(username="anna", password="x")  # noqa: S106
        claim(taken, operator)
        # …и рядом задача, которую НЕ взяли: если бы sweep вообще ничего не
        # видел, «ноль эскалаций» у взятой ничего бы не доказывало.
        forgotten = _task(tenant, conversation)
        _age(forgotten, 40)

        escalated = sweep_unclaimed_tasks()

        assert escalated == 1
        taken.refresh_from_db()
        forgotten.refresh_from_db()
        assert taken.pickup_escalated_at is None
        assert forgotten.pickup_escalated_at is not None

    def test_fresh_task_is_not_escalated(self, tenant, conversation, settings, send):
        settings.HANDOFF_DUTY_OPERATORS = []
        settings.HANDOFF_PICKUP_SLA_MINUTES = 15
        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = ["555000"]
        fresh = _task(tenant, conversation)
        old = _task(tenant, conversation)
        _age(old, 40)

        assert sweep_unclaimed_tasks() == 1

        fresh.refresh_from_db()
        assert fresh.pickup_escalated_at is None

    def test_closed_task_is_not_chased(self, tenant, conversation, settings, send):
        from apps.handoff.services import resolve_admin_task

        settings.HANDOFF_DUTY_OPERATORS = []
        settings.HANDOFF_PICKUP_SLA_MINUTES = 15
        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = ["555000"]
        closed = _task(tenant, conversation)
        _age(closed, 90)
        with tenant_scope(tenant):
            resolve_admin_task(closed, resolution_note="ответили")
        still_open = _task(tenant, conversation)
        _age(still_open, 90)

        assert sweep_unclaimed_tasks() == 1

        closed.refresh_from_db()
        assert closed.pickup_escalated_at is None

    def test_sla_zero_disables_the_sweep(self, tenant, conversation, settings, send):
        settings.HANDOFF_DUTY_OPERATORS = []
        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = ["555000"]
        task = _task(tenant, conversation)
        _age(task, 600)
        settings.HANDOFF_PICKUP_SLA_MINUTES = 15
        assert sweep_unclaimed_tasks() == 1, "с включённым SLA задача действительно просрочена"

        AdminTask.all_tenants.filter(pk=task.pk).update(pickup_escalated_at=None)
        settings.HANDOFF_PICKUP_SLA_MINUTES = 0

        assert sweep_unclaimed_tasks() == 0

    def test_escalation_is_audited(self, tenant, conversation, settings, send):
        settings.HANDOFF_DUTY_OPERATORS = []
        settings.HANDOFF_PICKUP_SLA_MINUTES = 15
        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = ["555000"]
        task = _task(tenant, conversation)
        _age(task, 40)

        sweep_unclaimed_tasks()

        rows = AuditLog.all_tenants.filter(action="handoff.pickup_overdue", target_id=task.id)
        assert rows.count() == 1
        payload = rows.first().payload
        assert payload["addressee"] == "queue:duty"
        assert payload["waited_minutes"] >= 40


class TestOverdueNotificationText:
    def test_says_who_and_how_long_and_nothing_private(self, tenant, conversation, settings):
        settings.HANDOFF_DUTY_OPERATORS = []
        task = _task(tenant, conversation)

        text = build_unclaimed_notification(task, waited_minutes=42)

        assert "42 мин" in text
        assert "queue:duty" in text
        assert tenant.name in text
        # PII-минимум ровно как у уведомления о создании (DRF-1029):
        # ни телефона, ни расшифровки диалога.
        assert "+79990001122" not in text
        assert "нужен человек" not in text


class TestQueueCommand:
    def test_lists_the_open_task_with_its_age(self, tenant, conversation, settings):
        settings.HANDOFF_DUTY_OPERATORS = []
        settings.HANDOFF_PICKUP_SLA_MINUTES = 15
        task = _task(tenant, conversation)
        _age(task, 75)

        out = StringIO()
        call_command("handoff_queue", stdout=out)
        printed = out.getvalue()

        assert str(task.id)[:8] in printed
        assert "queue:duty" in printed
        assert tenant.slug in printed
        assert "ДА" in printed, "просроченная задача обязана быть помечена просроченной"

    def test_empty_queue_says_so(self, settings):
        """Положительная стража: команда отличает «пусто» от «сломалось»."""

        settings.HANDOFF_PICKUP_SLA_MINUTES = 15
        out = StringIO()
        call_command("handoff_queue", stdout=out)

        assert "Открытых задач нет." in out.getvalue()
