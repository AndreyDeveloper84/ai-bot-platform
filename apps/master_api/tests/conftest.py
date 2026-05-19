"""Shared fixtures for master_api tests."""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant


BOT_TOKEN = "test-bot-token-xyz"


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    """Build a valid signed initData query string."""

    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def init_data_header(user_id: str = "12345", first_name: str = "Анна") -> str:
    """Build an Authorization header for the given MAX user id."""

    params = {
        "user": json.dumps({"id": int(user_id), "first_name": first_name}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _bot_token(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(
        slug="ma-test",
        name="Master API Test Salon",
        timezone="Europe/Moscow",
    )


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    """The master's MAX BotUser — opens the Mini App from the deeplink."""

    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="12345",
        display_name="Анна",
        chat_id="12345",
        phone="+79161234567",
    )


@pytest.fixture
def other_bot_user(tenant: Tenant) -> BotUser:
    """A different MAX user — used for wrong-recipient tests."""

    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="99999",
        display_name="Мария",
        chat_id="99999",
    )


@pytest.fixture
def other_tenant(db) -> Tenant:
    return Tenant.objects.create(
        slug="ma-test-other",
        name="Other Salon",
        timezone="Europe/Moscow",
    )


def make_master(
    tenant: Tenant,
    *,
    name: str = "Анна Петрова",
    invite_status: str = CatalogMaster.InviteStatus.PENDING,
    invite_token: uuid.UUID | None = None,
    expires_in_days: int | None = 7,
    linked_bot_user: BotUser | None = None,
    is_active: bool = True,
    archived_at: datetime | None = None,
    external_id: int | None = None,
) -> CatalogMaster:
    """Create a CatalogMaster with M0-relevant defaults.

    ``invite_token`` defaults to a fresh UUID iff status==PENDING.
    ``expires_in_days`` controls invite_expires_at relative to now;
    pass None to leave it NULL (treated as 'never expires' by validator
    — but production rows always have one set).
    """

    now = datetime.now(tz=timezone.utc)
    token = invite_token
    if token is None and invite_status == CatalogMaster.InviteStatus.PENDING:
        token = uuid.uuid4()
    expires_at = now + timedelta(days=expires_in_days) if expires_in_days is not None else None
    if external_id is None:
        # ensure uniqueness per (tenant, external_id)
        external_id = CatalogMaster.all_tenants.filter(tenant=tenant).count() + 1
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=external_id,
        external_updated_at=now,
        name=name,
        specialization="Маникюр",
        is_active=is_active,
        archived_at=archived_at,
        invite_status=invite_status,
        invite_token=token,
        invite_expires_at=expires_at,
        invited_at=now,
        max_handle="@anna_styl",
        linked_bot_user=linked_bot_user,
    )


@pytest.fixture
def pending_master(tenant: Tenant) -> CatalogMaster:
    """A PENDING master with a fresh 7-day invite — ready to be claimed."""

    return make_master(tenant)


@pytest.fixture
def accepted_master(tenant: Tenant, bot_user: BotUser) -> CatalogMaster:
    """An already-accepted master linked to ``bot_user`` — used by /me, profile."""

    return make_master(
        tenant,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        invite_token=None,
        expires_in_days=None,
        linked_bot_user=bot_user,
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
def master_service(
    tenant: Tenant, accepted_master: CatalogMaster, service: CatalogService
) -> MasterService:
    return MasterService.all_tenants.create(tenant=tenant, master=accepted_master, service=service)
