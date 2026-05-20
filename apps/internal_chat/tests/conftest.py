"""Shared fixtures for internal_chat tests.

Combines master_api-style HMAC init-data signing with admin_api-style
role-row creation. Most tests need both surfaces (master sends master
sends to admin), so the fixtures live here rather than in two parallel
conftests.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from datetime import datetime, timezone
from urllib.parse import urlencode

import pytest

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.internal_chat.models import (
    MasterAdminThread,
    TopicChoices,
)
from apps.internal_chat.services import create_thread
from apps.tenancy.models import Tenant, TenantStaff


BOT_TOKEN = "test-bot-token-internal-chat"


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def init_data_header(user_id: str) -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Анна"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _bot_token(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture
def tenant(db, settings) -> Tenant:
    t = Tenant.objects.create(
        slug="internal-chat-test",
        name="Студия Тест",
        timezone="Europe/Moscow",
    )
    settings.MAX_BOT_TENANT_SLUG = "internal-chat-test"
    return t


@pytest.fixture
def other_tenant(db) -> Tenant:
    return Tenant.objects.create(
        slug="internal-chat-other",
        name="Другая Студия",
        timezone="Europe/Moscow",
    )


def _make_bot_user(
    tenant: Tenant, *, channel_user_id: str, display_name: str = "Кто-то"
) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=channel_user_id,
        display_name=display_name,
        chat_id=channel_user_id,
        phone="+79161234567",
    )


def _make_master(
    tenant: Tenant,
    *,
    name: str = "Анна Петрова",
    linked_bot_user: BotUser | None = None,
    is_active: bool = True,
    external_id: int | None = None,
) -> CatalogMaster:
    now = datetime.now(tz=timezone.utc)
    if external_id is None:
        external_id = CatalogMaster.all_tenants.filter(tenant=tenant).count() + 100
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=external_id,
        external_updated_at=now,
        name=name,
        specialization="Маникюр",
        is_active=is_active,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        invited_at=now,
        linked_bot_user=linked_bot_user,
    )


@pytest.fixture
def master_bot_user(tenant: Tenant) -> BotUser:
    """The master who opens threads from the master Mini App."""

    return _make_bot_user(tenant, channel_user_id="6001", display_name="Анна-Мастер")


@pytest.fixture
def master(tenant: Tenant, master_bot_user: BotUser) -> CatalogMaster:
    """The ACTIVE / ACCEPTED master row, linked to ``master_bot_user``."""

    return _make_master(tenant, linked_bot_user=master_bot_user)


@pytest.fixture
def other_master_bot_user(tenant: Tenant) -> BotUser:
    return _make_bot_user(tenant, channel_user_id="6002", display_name="Иная-Мастер")


@pytest.fixture
def other_master(tenant: Tenant, other_master_bot_user: BotUser) -> CatalogMaster:
    """A second master in the same tenant — used for cross-master tests."""

    return _make_master(
        tenant,
        name="Мария Иванова",
        linked_bot_user=other_master_bot_user,
        external_id=200,
    )


@pytest.fixture
def owner_bot_user(tenant: Tenant) -> BotUser:
    bu = _make_bot_user(tenant, channel_user_id="7001", display_name="Карина")
    TenantStaff.all_tenants.create(tenant=tenant, bot_user=bu, role=TenantStaff.Role.OWNER)
    return bu


@pytest.fixture
def admin_bot_user(tenant: Tenant) -> BotUser:
    bu = _make_bot_user(tenant, channel_user_id="7002", display_name="Аня")
    TenantStaff.all_tenants.create(tenant=tenant, bot_user=bu, role=TenantStaff.Role.ADMIN)
    return bu


@pytest.fixture
def receptionist_bot_user(tenant: Tenant) -> BotUser:
    bu = _make_bot_user(tenant, channel_user_id="7003", display_name="Ресепшн")
    TenantStaff.all_tenants.create(tenant=tenant, bot_user=bu, role=TenantStaff.Role.RECEPTIONIST)
    return bu


@pytest.fixture
def customer_bot_user(tenant: Tenant) -> BotUser:
    """A plain customer — no staff row, no master link → 403."""

    return _make_bot_user(tenant, channel_user_id="8001", display_name="Клиент")


@pytest.fixture
def thread(tenant: Tenant, master: CatalogMaster, master_bot_user: BotUser) -> MasterAdminThread:
    """A vanilla open thread, master-initiated, with a first message."""

    return create_thread(
        tenant=tenant,
        master=master,
        topic=TopicChoices.EARNINGS_DISPUTE,
        subject="Спор по доходу",
        actor=master_bot_user,
        first_message_body="По окрашиванию у меня ставка 40%, а применилась дефолтная.",
    )


@pytest.fixture
def sensitive_thread(
    tenant: Tenant, master: CatalogMaster, master_bot_user: BotUser
) -> MasterAdminThread:
    """A thread on an auto-sensitive topic per handoff §5.4."""

    return create_thread(
        tenant=tenant,
        master=master,
        topic=TopicChoices.OFFBOARDING_DISCUSSION,
        subject="Подумываю об уходе",
        actor=master_bot_user,
        first_message_body="Хочу обсудить уход из студии",
    )
