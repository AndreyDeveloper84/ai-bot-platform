"""Общие приспособления для тестов админки (DRF-1514).

Учётные записи заводятся настоящим механизмом выдачи
(``grant_admin_account``), а не ``create_user`` с ручной раздачей групп:
тест, который сам себе выдал права, проверяет свою же догадку о правах,
а не то, что получает живой сотрудник.
"""

from __future__ import annotations

import secrets

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from apps.adminconsole.accounts import PASSWORD_ENV_VAR, grant_admin_account
from apps.conversations.models import Conversation, Message
from apps.handoff.models import AdminTask
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant


@pytest.fixture
def login_as(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201 - фабрика клиентов
    """Завести запись выбранной роли и войти под ней."""

    def _login(username: str, role: str) -> Client:
        password = secrets.token_urlsafe(24)
        monkeypatch.setenv(PASSWORD_ENV_VAR, password)
        grant_admin_account(username=username, role=role)
        monkeypatch.delenv(PASSWORD_ENV_VAR, raising=False)
        client = Client()
        assert client.login(username=username, password=password), (
            f"{username!r} не смог войти — дальше проверялось бы пустое место"
        )
        return client

    return _login


@pytest.fixture
def owner_client(db) -> Client:  # noqa: ANN001
    """Владелец — суперпользователь. Половина «присутствия» во всех парах."""
    user = get_user_model().objects.create_superuser(
        username="vladelec",
        email="owner@example.com",
        password="x",  # pragma: allowlist secret
    )
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def salon(db) -> Tenant:  # noqa: ANN001
    return Tenant.objects.create(slug="drf1514-salon", name="Салон DRF-1514")


@pytest.fixture
def other_salon(db) -> Tenant:  # noqa: ANN001
    return Tenant.objects.create(slug="drf1514-chuzhoy", name="Чужой салон")


def make_client_thread(
    tenant: Tenant,
    *,
    channel_user_id: str,
    display_name: str,
    text: str,
    phone: str = "",
    context: dict | None = None,
) -> tuple[BotUser, Conversation, Message, AdminTask]:
    """Клиент, его разговор, его сообщение и обращение по нему."""
    bot_user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=channel_user_id,
        display_name=display_name,
        phone=phone,
        context=context or {},
    )
    conversation = Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)
    message = Message.all_tenants.create(
        tenant=tenant,
        conversation=conversation,
        role=Message.Role.USER,
        content=text,
    )
    task = AdminTask.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        conversation=conversation,
        task_type=AdminTask.TaskType.COMPLAINT,
        reason="клиент жалуется на запись",
    )
    return bot_user, conversation, message, task
