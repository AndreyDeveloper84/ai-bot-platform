"""Экран очереди handoff: список, возраст, действия, пустая очередь (DRF-1499).

Пины из условия задачи:

* открытая задача в списке есть, закрытой нет;
* возраст считается от создания, а не от последнего изменения;
* самые старые сверху;
* пустая очередь показывает пустоту честно, а не ошибку;
* «взять» и «закрыть» идут через сервис и пишут журнал с автором;
* «смотрящий» экран видит, действий у него нет;
* из списка видно, какие диалоги замьючены — салонный И глобальный.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.conversations.models import Conversation
from apps.handoff.models import AdminTask
from apps.handoff.services import create_admin_task, resolve_admin_task
from apps.identity.models import BotUser
from apps.identity.services.global_tenant import get_global_bot_tenant
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)

QUEUE_URL = reverse("admin:handoff_admintask_queue")


@pytest.fixture
def salon(db) -> Tenant:  # noqa: ANN001
    return Tenant.objects.create(slug="drf1499-salon", name="Салон Очереди")


def _thread(tenant: Tenant, *, channel_user_id: str, display_name: str):
    """Клиент и его разговор в этом салоне."""
    bot_user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=channel_user_id,
        display_name=display_name,
    )
    conversation = Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)
    return bot_user, conversation


def _open_task(tenant: Tenant, conversation: Conversation) -> AdminTask:
    """Задача через сервис — как в бою: диалог уходит в HUMAN_HANDOFF."""
    with tenant_scope(tenant):
        task = create_admin_task(conversation, task_type=AdminTask.TaskType.HANDOFF)
    conversation.refresh_from_db()
    assert conversation.state == Conversation.State.HUMAN_HANDOFF
    return task


@pytest.fixture
def open_task(salon, settings) -> AdminTask:  # noqa: ANN001
    settings.STRICT_TENANT_SCOPE = "strict"
    _, conversation = _thread(salon, channel_user_id="drf1499-open", display_name="Открытый Клиент")
    return _open_task(salon, conversation)


def _get(client: Client):
    response = client.get(QUEUE_URL)
    assert response.status_code == 200
    return response.content.decode()


class TestQueueList:
    def test_open_task_listed_closed_is_not(self, owner_client, salon, open_task, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        _, closed_conv = _thread(
            salon, channel_user_id="drf1499-closed", display_name="Закрытый Клиент"
        )
        closed_task = _open_task(salon, closed_conv)
        with tenant_scope(salon):
            resolve_admin_task(closed_task)

        html = _get(owner_client)

        # Присутствие: открытая задача в списке есть (и закрытая строка
        # доказуемо существовала — иначе отсутствие ниже ничего не проверяет).
        assert "Открытый Клиент" in html
        closed_task.refresh_from_db()
        assert closed_task.status == AdminTask.Status.RESOLVED
        assert "Закрытый Клиент" not in html

    def test_oldest_first(self, owner_client, salon, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        _, new_conv = _thread(salon, channel_user_id="drf1499-new", display_name="Новый Клиент")
        _, old_conv = _thread(salon, channel_user_id="drf1499-old", display_name="Старый Клиент")
        new_task = _open_task(salon, new_conv)
        old_task = _open_task(salon, old_conv)
        AdminTask.all_tenants.filter(pk=old_task.pk).update(
            created_at=timezone.now() - timedelta(hours=3)
        )
        AdminTask.all_tenants.filter(pk=new_task.pk).update(
            created_at=timezone.now() - timedelta(minutes=5)
        )

        html = _get(owner_client)

        assert "Старый Клиент" in html and "Новый Клиент" in html
        assert html.index("Старый Клиент") < html.index("Новый Клиент")

    def test_age_counts_from_creation_not_last_update(self, owner_client, open_task):
        two_hours_ago = timezone.now() - timedelta(hours=2)
        # Последнее изменение — прямо сейчас; создание — два часа назад.
        # Возраст, посчитанный от updated_at, показал бы «0 мин».
        AdminTask.all_tenants.filter(pk=open_task.pk).update(
            created_at=two_hours_ago,
            resolution_note="тронули после создания",
        )

        html = _get(owner_client)

        assert "2 ч 00 мин" in html
        # Счётчик на видном месте называет самую старую тем же возрастом.
        assert "самая старая ждёт уже" in html

    def test_counter_names_open_count(self, owner_client, open_task):
        html = _get(owner_client)
        assert "Открытый Клиент" in html
        assert "Сейчас открыто:" in html

    def test_empty_queue_is_honest_not_an_error(self, owner_client, db):
        html = _get(owner_client)
        # Парная положительная: экран отрисовался и говорит правду о пустоте.
        assert "Очередь handoff" in html
        assert "Сейчас открыто:" in html
        assert "Открытых задач нет" in html


class TestMutedDialogs:
    def test_salon_and_global_dialogs_both_listed(self, owner_client, salon, open_task):
        sentinel = get_global_bot_tenant()
        global_bot_user = BotUser.all_tenants.create(
            tenant=sentinel,
            channel="max",
            # Та же канальная личность, что у задачи, — мьют ходит за человеком.
            channel_user_id="drf1499-open",
            display_name="Открытый Клиент",
        )
        Conversation.all_tenants.create(tenant=sentinel, bot_user=global_bot_user)

        html = _get(owner_client)

        assert "Салон Очереди" in html
        assert "глобальный диалог" in html
        assert "здесь заведена задача" in html


class TestQueueActions:
    def test_claim_assigns_through_service_and_journals(self, login_as, open_task):
        editor = login_as("drf1499-editor-claim", "editor")
        # Присутствие: задача адресована дежурной очереди и никем не взята.
        assert open_task.assigned_queue
        assert open_task.claimed_at is None

        response = editor.post(QUEUE_URL, {"action": "claim", "task_id": str(open_task.id)})
        assert response.status_code == 302

        open_task.refresh_from_db()
        assert open_task.assigned_to is not None
        assert open_task.assigned_to.username == "drf1499-editor-claim"
        assert open_task.claimed_at is not None
        entry = AuditLog.all_tenants.filter(
            action="admin.object.updated", target_id=open_task.id
        ).first()
        assert entry is not None
        assert entry.payload.get("actor_username") == "drf1499-editor-claim"

    def test_resolve_closes_through_service_and_releases_dialog(self, login_as, salon, open_task):
        editor = login_as("drf1499-editor-close", "editor")
        conversation = open_task.conversation
        assert conversation.state == Conversation.State.HUMAN_HANDOFF

        response = editor.post(
            QUEUE_URL,
            {
                "action": "resolve",
                "task_id": str(open_task.id),
                "resolution_note": "отвечено клиенту",
            },
        )
        assert response.status_code == 302

        open_task.refresh_from_db()
        assert open_task.status == AdminTask.Status.RESOLVED
        assert open_task.resolved_at is not None
        assert open_task.resolution_note == "отвечено клиенту"
        conversation.refresh_from_db()
        assert conversation.state == Conversation.State.IDLE
        assert AuditLog.all_tenants.filter(
            action="admin.object.updated",
            target_id=open_task.id,
            payload__actor_username="drf1499-editor-close",
        ).exists()

    def test_viewer_sees_queue_but_cannot_act(self, login_as, open_task):
        viewer = login_as("drf1499-viewer", "viewer")

        html = _get(viewer)
        # Присутствие: экран зрителю показан, задача в нём есть.
        assert "Открытый Клиент" in html
        assert 'name="action"' not in html

        response = viewer.post(QUEUE_URL, {"action": "claim", "task_id": str(open_task.id)})
        assert response.status_code == 403
        open_task.refresh_from_db()
        # Присутствие на тех же данных: задача жива и адресована очереди.
        assert open_task.assigned_queue
        assert open_task.claimed_at is None
        assert open_task.assigned_to is None
