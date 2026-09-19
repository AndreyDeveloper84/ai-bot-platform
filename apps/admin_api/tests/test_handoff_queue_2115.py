"""``GET /api/v1/admin/handoff-queue/`` — очередь handoff для «Сегодня» (DRF-2115).

Карточка «Диалоги — N ждут ответа» на экране «Сегодня» салонной админки.
Источник — тот же, что у экрана очереди в Django-админке (DRF-1499,
``apps.adminconsole.handoff_queue``): открытые ``AdminTask`` этого салона,
самые старые сверху. Только чтение: «взять» и «закрыть» остаются в
Django-админке (DRF-1488 — механика не дублируется).

Что уходит наружу: номер задачи, сколько ждёт, взята ли и кем (адресат —
имя оператора/очередь, не клиент), эскалирована ли. Текстов сообщений и
данных клиента (имя, телефон) в ответе нет — это поверхность оператора,
и очередь показывает «сколько и как давно ждут», а не «кто и что писал».
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.admin_api.tests.conftest import init_data_header
from apps.conversations.models import Conversation
from apps.handoff.models import AdminTask
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant


def _url() -> str:
    return reverse("admin_api:handoff_queue")


def _task(
    tenant: Tenant, bot_user: BotUser, *, minutes_ago: int, status: str = "open"
) -> AdminTask:
    conversation, _ = Conversation.all_tenants.get_or_create(tenant=tenant, bot_user=bot_user)
    task = AdminTask.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        conversation=conversation,
        task_type="handoff",
        status=status,
        reason="хочу поговорить с человеком",
        transcript_snapshot=[{"role": "user", "text": "секретный текст клиента"}],
    )
    AdminTask.all_tenants.filter(pk=task.pk).update(
        created_at=timezone.now() - timedelta(minutes=minutes_ago)
    )
    task.refresh_from_db()
    return task


@pytest.fixture
def client_bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="7001", display_name="Анна Клиентова"
    )


class TestQueue:
    def test_owner_sees_open_tasks_of_her_salon_oldest_first(
        self, client: Client, tenant: Tenant, owner_bot_user: BotUser, client_bot_user: BotUser
    ) -> None:
        old = _task(tenant, client_bot_user, minutes_ago=84)
        young = _task(tenant, client_bot_user, minutes_ago=3, status="in_progress")
        _task(tenant, client_bot_user, minutes_ago=200, status="resolved")

        resp = client.get(_url(), HTTP_AUTHORIZATION=init_data_header("5001"))

        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["waiting"] == 2
        assert [r["task_id"] for r in body["rows"]] == [str(old.id), str(young.id)]
        first = body["rows"][0]
        assert first["age_minutes"] >= 84
        assert first["claimed"] is False
        assert first["escalated"] is False
        assert first["status"] == "open"
        assert body["rows"][1]["status"] == "in_progress"

    def test_no_client_data_and_no_message_text_in_the_body(
        self, client: Client, tenant: Tenant, owner_bot_user: BotUser, client_bot_user: BotUser
    ) -> None:
        _task(tenant, client_bot_user, minutes_ago=10)

        body = client.get(_url(), HTTP_AUTHORIZATION=init_data_header("5001")).json()

        assert body["waiting"] == 1
        flat = str(body)
        assert "task_id" in flat
        assert "Анна" not in flat
        assert "Клиентова" not in flat
        assert "7001" not in flat
        assert "секретный текст" not in flat
        assert "transcript" not in flat
        assert "reason" not in flat

    def test_another_salons_tasks_are_invisible(
        self,
        client: Client,
        tenant: Tenant,
        other_tenant: Tenant,
        owner_bot_user: BotUser,
    ) -> None:
        stranger = BotUser.all_tenants.create(
            tenant=other_tenant, channel="max", channel_user_id="7002", display_name="Чужая"
        )
        _task(other_tenant, stranger, minutes_ago=30)

        body = client.get(_url(), HTTP_AUTHORIZATION=init_data_header("5001")).json()

        assert body == {"waiting": 0, "rows": []}

    def test_admin_allowed_receptionist_and_master_refused(
        self,
        client: Client,
        admin_bot_user: BotUser,
        receptionist_bot_user: BotUser,
        master_only_bot_user: BotUser,
    ) -> None:
        assert client.get(_url(), HTTP_AUTHORIZATION=init_data_header("5002")).status_code == 200
        assert client.get(_url(), HTTP_AUTHORIZATION=init_data_header("5003")).status_code == 403
        assert client.get(_url(), HTTP_AUTHORIZATION=init_data_header("5004")).status_code == 403
