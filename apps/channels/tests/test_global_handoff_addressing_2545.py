"""Адресация «позовите человека» с глобальной поверхности — эвристика названа (DRF-2545).

Просьба о человеке на глобальном пути адресуется салону **по времени**, а не
по предмету: задача ложится на последний по ``last_message_at`` салонный
разговор (``_latest_tenant_conversation``), а при их отсутствии — в очередь
платформы. Диалог салона при этом уходит в ``HUMAN_HANDOFF``: бот салона
замолкает, персоналу уходит «Клиент … ждёт ответа».

Это не случайность, а названная эвристика — и эти узлы держат её **как она
есть сегодня**, включая неудобные случаи. Покраснели — значит, адресация
поменялась: сверьте с решением по DRF-2545 (спрашивать человека «о каком
салоне речь?» или адресовать платформе) и перепишите узлы вместе с ним.

Цена ошибки односторонняя: ложное молчание бота у салона, о котором речь не
шла, хуже задержки ответа у того, о котором шла.

Узлы:

* салон назван, но не последний — задача всё равно у последнего, названный
  салон не тронут;
* простое упоминание сотрудника («я сам администратор») — не просьба, но
  диалог последнего салона всё равно уходит в ``HUMAN_HANDOFF``;
* текст сообщения в уведомление салона не попадает, но лежит в ``reason``
  задачи ПОСЛЕДНЕГО салона — то есть жалоба на салон Б хранится у салона А.

Уже закреплено в ``test_global_human_handoff.py`` и здесь не повторяется:
салон не назван — задача у последнего (``test_task_goes_to_most_recent_tenant_context``);
салонных разговоров нет — очередь платформы (``test_no_tenant_context_lands_on_platform_queue``).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone as dt_timezone

import pytest

from apps.channels.max import handler as max_handler
from apps.conversations.models import Conversation
from apps.handoff.models import AdminTask
from apps.identity.models import BotUser
from apps.orchestrator.memory import short_term
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

HANDOFF_REPLY = "Передаю менеджеру — ответят в течение 30 минут."


def _run_global(text: str, *, mid: str, user_id: int = 2545) -> None:
    max_handler.handle_global_max_event(
        {
            "update_type": "message_created",
            "timestamp": 1731320000000,
            "message": {
                "sender": {"user_id": user_id, "name": "Иван"},
                "recipient": {"chat_id": user_id, "chat_type": "dialog"},
                "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
            },
        },
        trace_id=str(uuid.uuid4()),
    )


@pytest.fixture
def mock_send(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture
def spy_concierge(monkeypatch):
    from unittest.mock import MagicMock

    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Какая услуга интересует?"))
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


def _salon_dialog(slug: str, name: str, *, day: int, user_id: int = 2545) -> Conversation:
    """Прежний разговор того же человека с салоном; ``day`` задаёт давность."""
    tenant = Tenant.objects.create(slug=slug, name=name)
    with tenant_scope(tenant):
        bot_user = BotUser.objects.create(tenant=tenant, channel="max", channel_user_id=str(user_id))
        conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)
    Conversation.all_tenants.filter(pk=conv.pk).update(
        last_message_at=datetime(2026, 9, day, tzinfo=dt_timezone.utc)
    )
    conv.refresh_from_db()
    return conv


@pytest.fixture
def two_salons():
    """Салон Б — разговор давний, салон А — последний."""
    salon_b = _salon_dialog("salon-b-2545", "Салон Бета", day=1)
    salon_a = _salon_dialog("salon-a-2545", "Салон Альфа", day=20)
    return salon_a, salon_b


class TestAddressingIsByTimeNotBySubject:
    def test_named_salon_that_is_not_the_latest_is_not_the_addressee(
        self, two_salons, mock_send, fake_redis, spy_concierge
    ):
        salon_a, salon_b = two_salons

        _run_global("администратор салона Бета нагрубил, хочу пожаловаться", mid="n1")

        task = AdminTask.all_tenants.get()
        # Присутствие: задача создана и человеку ответили строкой передачи.
        assert task.task_type == AdminTask.TaskType.HANDOFF
        assert mock_send[-1]["text"] == HANDOFF_REPLY
        # Адресат — последний по времени салон, а не названный в сообщении.
        assert task.tenant_id == salon_a.tenant_id
        salon_a.refresh_from_db()
        salon_b.refresh_from_db()
        assert salon_a.state == Conversation.State.HUMAN_HANDOFF
        assert salon_b.state != Conversation.State.HUMAN_HANDOFF

    def test_a_mention_of_staff_mutes_the_latest_salon(
        self, two_salons, mock_send, fake_redis, spy_concierge
    ):
        """«я сам администратор» — не просьба, но триггер ищет подстроку."""
        salon_a, salon_b = two_salons

        _run_global("я сам администратор", mid="n2")

        task = AdminTask.all_tenants.get()
        assert task.tenant_id == salon_a.tenant_id
        salon_a.refresh_from_db()
        assert salon_a.state == Conversation.State.HUMAN_HANDOFF
        spy_concierge.assert_not_called()


class TestWhereTheComplaintTextGoes:
    def test_text_stays_out_of_the_salon_notice_but_sits_in_the_latest_salons_task(
        self, two_salons, mock_send, fake_redis, spy_concierge
    ):
        from apps.channels.max.salon_notify import handoff_waiting_notice

        salon_a, _ = two_salons
        complaint = "администратор салона Бета нагрубил"

        _run_global(complaint, mid="n3")

        task = AdminTask.all_tenants.get()
        assert task.tenant_id == salon_a.tenant_id
        # Задача салона А хранит текст жалобы на салон Б.
        assert complaint in task.reason

        notice = handoff_waiting_notice(task)
        # Присутствие: уведомление собрано и адресовано салону А.
        assert notice.title.endswith("ждёт ответа")
        assert notice.tenant.pk == salon_a.tenant_id
        # Текст сообщения в уведомление салона не попадает.
        shown = " ".join((notice.title, *notice.facts))
        assert "Бета" not in shown
        assert "нагрубил" not in shown
