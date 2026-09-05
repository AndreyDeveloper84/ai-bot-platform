"""Shared fixtures for admin_api tests.

Mirrors :mod:`apps.master_api.tests.conftest` — same MAX init-data
signing pattern + master/bot_user factories — extended with
:class:`TenantStaff` role rows for the admin-role gate.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import pytest

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant, TenantStaff


BOT_TOKEN = "test-bot-token-admin-api"


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def init_data_header(user_id: str = "5001", first_name: str = "Карина") -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": first_name}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _bot_token(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture
def tenant(db, settings) -> Tenant:
    t = Tenant.objects.create(
        slug="admin-api-test",
        name="Студия Карина",
        timezone="Europe/Moscow",
    )
    settings.MAX_BOT_TENANT_SLUG = "admin-api-test"
    return t


@pytest.fixture
def other_tenant(db) -> Tenant:
    return Tenant.objects.create(
        slug="admin-api-other",
        name="Other Salon",
        timezone="Europe/Moscow",
    )


def _make_bot_user(tenant: Tenant, *, channel_user_id: str, display_name: str = "Anya") -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=channel_user_id,
        display_name=display_name,
        chat_id=channel_user_id,
        phone="+79161234567",
    )


@pytest.fixture
def owner_bot_user(tenant: Tenant) -> BotUser:
    """Karina — the salon's Owner."""

    bu = _make_bot_user(tenant, channel_user_id="5001", display_name="Карина")
    TenantStaff.all_tenants.create(tenant=tenant, bot_user=bu, role=TenantStaff.Role.OWNER)
    return bu


@pytest.fixture
def admin_bot_user(tenant: Tenant) -> BotUser:
    """Anya — the salon's Admin (cannot deactivate masters)."""

    bu = _make_bot_user(tenant, channel_user_id="5002", display_name="Аня")
    TenantStaff.all_tenants.create(tenant=tenant, bot_user=bu, role=TenantStaff.Role.ADMIN)
    return bu


@pytest.fixture
def receptionist_bot_user(tenant: Tenant) -> BotUser:
    """Receptionist — read-only role; rejected by require_admin_role."""

    bu = _make_bot_user(tenant, channel_user_id="5003", display_name="Стажёр")
    TenantStaff.all_tenants.create(tenant=tenant, bot_user=bu, role=TenantStaff.Role.RECEPTIONIST)
    return bu


@pytest.fixture
def master_only_bot_user(tenant: Tenant) -> BotUser:
    """A BotUser linked to a CatalogMaster — primary_role=master.

    The master fixture below promotes this user. require_admin_role
    rejects (master is not admin/owner).
    """

    return _make_bot_user(tenant, channel_user_id="5004", display_name="Анна-Мастер")


@pytest.fixture
def customer_bot_user(tenant: Tenant) -> BotUser:
    """Bare BotUser — no role rows; primary_role=customer; → 403."""

    return _make_bot_user(tenant, channel_user_id="5005", display_name="Клиент")


def make_master(
    tenant: Tenant,
    *,
    name: str = "Анна Петрова",
    invite_status: str = CatalogMaster.InviteStatus.ACCEPTED,
    is_active: bool = True,
    archived_at: datetime | None = None,
    external_id: int | None = None,
    linked_bot_user: BotUser | None = None,
    specialization: str = "Маникюр",
    bio: str = "",
    invited_at_offset_days: int = 0,
) -> CatalogMaster:
    """Create a CatalogMaster with MM1/MM3-relevant defaults.

    Defaults match a typical ACTIVE roster row (ACCEPTED + is_active).
    """

    now = datetime.now(tz=timezone.utc)
    if external_id is None:
        external_id = CatalogMaster.all_tenants.filter(tenant=tenant).count() + 1
    invited_at = now - timedelta(days=invited_at_offset_days)
    invite_token: uuid.UUID | None = None
    if invite_status == CatalogMaster.InviteStatus.PENDING:
        invite_token = uuid.uuid4()
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=external_id,
        external_updated_at=now,
        name=name,
        specialization=specialization,
        bio=bio,
        is_active=is_active,
        archived_at=archived_at,
        invite_status=invite_status,
        invite_token=invite_token,
        invited_at=invited_at,
        max_handle="@anna_styl",
        linked_bot_user=linked_bot_user,
    )


@pytest.fixture
def master(tenant: Tenant) -> CatalogMaster:
    """A vanilla ACTIVE / ACCEPTED master — the dominant fixture."""

    return make_master(tenant)


@pytest.fixture
def inactive_master(tenant: Tenant) -> CatalogMaster:
    return make_master(tenant, name="Ирина Смирнова", is_active=False, external_id=99)


@pytest.fixture
def pending_master(tenant: Tenant) -> CatalogMaster:
    """A master the owner invited yesterday, in the shape production makes.

    ``is_active=False`` is not decoration — it is what the invite-create
    path writes (``apps/admin_api/views_invite.py``, ``is_active=False``
    beside ``invite_status=PENDING``), and it is deliberate: an invited
    master who has not answered must not appear on the booking surface.

    This fixture used to inherit ``make_master``'s ``is_active=True``,
    a shape the invite path has never produced. Every assertion resting
    on it was therefore guarding a case that cannot occur (DRF-1506).
    """

    return make_master(
        tenant,
        name="Наталья Прохорова",
        invite_status=CatalogMaster.InviteStatus.PENDING,
        is_active=False,
        external_id=98,
    )


@pytest.fixture
def service(tenant: Tenant) -> CatalogService:
    now = datetime.now(tz=timezone.utc)
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=42,
        external_updated_at=now,
        slug="manicure-gel",
        name="Маникюр гель-лак",
        duration_min=60,
        is_active=True,
    )


@pytest.fixture
def master_with_service(
    tenant: Tenant, master: CatalogMaster, service: CatalogService
) -> CatalogMaster:
    MasterService.all_tenants.create(tenant=tenant, master=master, service=service)
    return master


def link_master_to_bot_user(master: CatalogMaster, bot_user: BotUser) -> CatalogMaster:
    """Promote a BotUser to master by binding the OneToOne link."""

    master.linked_bot_user = bot_user
    master.save(update_fields=["linked_bot_user"])
    return master


def _serialize(value: Any) -> Any:
    """JSON-safe value coercion — used by tests that assert payload shapes."""

    if isinstance(value, datetime):
        return value.isoformat()
    return value
