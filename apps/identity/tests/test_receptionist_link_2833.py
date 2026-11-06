"""Каталожная половина ``receptionist`` — проводная форма (DRF-2833, B3).

Сеть — ``pytest-httpx``: каталог НЕ заглушка, а форма ручки
beautygo_backend #658 (``role=receptionist`` + ``assigned_by_external_user_id``).

  - w1: тело несёт роль и MAX-личность НАЗНАЧАЮЩЕГО, ключ повтора — свой,
    не ключ ``admin``; аудит успеха с ``role=receptionist``;
  - w2: ключ ``admin`` не изменился байт в байт — по нему каталог узнаёт
    уже заведённые связи (DRF-2085), иначе повтор «Выдать роль» отвечал бы
    ``identity_already_bound``;
  - w3: 403 каталога с причиной ``assigner_not_admin`` — отказ по ЭТОМУ
    имени, не ``credential_refused`` (иначе оператор сверял бы исправный
    секрет); 403 без причины — по-прежнему ``credential_refused``;
  - w4: без назначающего в сеть не ходим — отказ по имени;
  - w5: ресепшна без активной роли в боте каталог не видит.
"""

from __future__ import annotations

import json
import uuid

import pytest
from pytest_httpx import HTTPXMock

from apps.audit.models import AuditLog
from apps.events.vocabulary import STAFF_SALON_ADMIN_LINK_REFUSED, STAFF_SALON_ADMIN_LINKED
from apps.identity.models import BotUser
from apps.identity.services import salon_admin_link
from apps.identity.services.receptionist_link import NotAReceptionist, link_receptionist_to_catalog
from apps.identity.services.salon_admin_link import CatalogAdminLinkRefused, idempotency_key_for
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

BASE = "https://ayla.test"
LINK_TOKEN = "salon-admin-link-secret-2833"  # noqa: S105
AYLA_USER = "d0a1c2d3-0000-4000-8000-000000002833"
RELATIONSHIP = "e0a1c2d3-0000-4000-8000-000000002833"


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.AYLA_BASE_URL = BASE
    settings.AYLA_INTERNAL_API_TOKEN = "general-bearer-2833"  # noqa: S105
    settings.AYLA_SALON_ADMIN_LINK_TOKEN = LINK_TOKEN


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="desk-2833", name="Салон 2833")


@pytest.fixture
def owner(tenant) -> BotUser:
    bu = BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="2833001")
    TenantStaff.all_tenants.create(tenant=tenant, bot_user=bu, role="owner")
    return bu


@pytest.fixture
def desk(tenant) -> BotUser:
    bu = BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="2833002")
    TenantStaff.all_tenants.create(tenant=tenant, bot_user=bu, role="receptionist")
    return bu


def _url(tenant: Tenant) -> str:
    return f"{BASE}/api/v1/internal/tenants/{tenant.slug}/salon-admins/"


def _ok(tenant: Tenant) -> dict:
    return {
        "data": {
            "tenant_id": str(tenant.id),
            "slug": tenant.slug,
            "ayla_user_id": AYLA_USER,
            "relationship_id": RELATIONSHIP,
            "role": "receptionist",
            "status": "created",
        }
    }


def _link(tenant, desk, owner):
    return link_receptionist_to_catalog(
        tenant=tenant,
        person=desk,
        assigned_by=owner,
        actor_label=f"salon_miniapp:bot_user:{owner.pk}",
    )


def _audit(action: str, person: BotUser) -> list[AuditLog]:
    return list(AuditLog.all_tenants.filter(action=action, target_id=person.pk))


def test_w1_body_names_the_role_and_the_assigner(httpx_mock: HTTPXMock, tenant, owner, desk):
    httpx_mock.add_response(method="POST", url=_url(tenant), json=_ok(tenant), status_code=201)

    result = _link(tenant, desk, owner)

    assert result.created is True
    (req,) = httpx_mock.get_requests()
    assert req.headers["Authorization"] == f"Bearer {LINK_TOKEN}"
    body = json.loads(req.content)
    assert body["external_user_id"] == "bot:max:2833002"
    assert body["role"] == "receptionist"
    assert body["assigned_by_external_user_id"] == "bot:max:2833001"
    assert body["idempotency_key"] == idempotency_key_for(
        tenant.id, "bot:max:2833002", "receptionist"
    )
    assert body["idempotency_key"] != idempotency_key_for(tenant.id, "bot:max:2833002")
    (linked,) = _audit(STAFF_SALON_ADMIN_LINKED, desk)
    assert linked.payload["role"] == "receptionist"
    assert linked.payload["created"] is True


def test_w2_the_admin_key_is_unchanged():
    tenant_id = uuid.UUID("00000000-0000-4000-8000-000000002085")
    expected = str(
        uuid.uuid5(
            uuid.UUID("7c1a2d3e-2085-4bcd-9e0f-5a10ad1e4b2c"),
            f"salon-admin-link:{tenant_id}:bot:max:1",
        )
    )
    assert idempotency_key_for(tenant_id, "bot:max:1") == expected
    assert idempotency_key_for(tenant_id, "bot:max:1", "admin") == expected


def test_w3_a_403_with_the_catalogs_reason_keeps_its_name(
    httpx_mock: HTTPXMock, tenant, owner, desk
):
    httpx_mock.add_response(
        method="POST",
        url=_url(tenant),
        status_code=403,
        json={
            "error": {
                "code": "SALON_ADMIN_LINK_REFUSED",
                "message": "x",
                "details": {"reason": "assigner_not_admin"},
            }
        },
    )

    with pytest.raises(CatalogAdminLinkRefused) as caught:
        _link(tenant, desk, owner)

    assert caught.value.reason == "assigner_not_admin"
    assert caught.value.role == "receptionist"
    assert "связь с каталогом" in caught.value.hint
    assert "ресепшна" in caught.value.operator_text()
    (refused,) = _audit(STAFF_SALON_ADMIN_LINK_REFUSED, desk)
    assert refused.payload["reason"] == "assigner_not_admin"
    assert refused.payload["role"] == "receptionist"


def test_w3_a_bare_403_is_still_the_credential(httpx_mock: HTTPXMock, tenant, owner, desk):
    httpx_mock.add_response(method="POST", url=_url(tenant), status_code=403, json={"detail": "no"})

    with pytest.raises(CatalogAdminLinkRefused) as caught:
        _link(tenant, desk, owner)

    assert caught.value.reason == "credential_refused"


def test_w4_no_assigner_never_reaches_the_network(httpx_mock: HTTPXMock, tenant, desk):
    with pytest.raises(CatalogAdminLinkRefused) as caught:
        salon_admin_link.ensure_catalog_salon_receptionist(
            tenant=tenant, bot_user=desk, assigned_by=None, actor_label="x:y"
        )

    assert caught.value.reason == "assigner_not_admin"
    assert httpx_mock.get_requests() == []


def test_w5_no_live_receptionist_role_never_reaches_the_catalog(
    httpx_mock: HTTPXMock, tenant, owner, desk
):
    TenantStaff.all_tenants.filter(bot_user=desk).update(deactivated_at="2026-10-20T00:00:00Z")

    with pytest.raises(NotAReceptionist):
        _link(tenant, desk, owner)

    assert httpx_mock.get_requests() == []
