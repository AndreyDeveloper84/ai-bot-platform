"""Каталожная половина у владельца салона — решение владельца 13.11.2026.

Аудит регистрации салона (п.4): ``owner`` в боте писал часы и отгулы
мастеров своим MAX-токеном, а каталог пускает только admin TUR салона
(``IsTenantAdmin``) — владелец получал 403 ``salon_write_refused``. Решение
(вариант «а»): каталожную половину заводит оператор и для ``owner``, той же
дверью, что для ``admin`` (DRF-2085); код приглашения каталог не зовёт.
"""

from __future__ import annotations

import json

import pytest
from pytest_httpx import HTTPXMock

from apps.audit.models import AuditLog
from apps.events.vocabulary import STAFF_SALON_ADMIN_LINKED
from apps.identity.models import BotUser
from apps.identity.services.salon_admin_link import CatalogAdminLinkRefused, idempotency_key_for
from apps.identity.services.specialist_onboarding import OnboardingActor
from apps.identity.services.staff_invites import issue_staff_invite, redeem_staff_invite
from apps.identity.services.staff_roles import grant_role_by_operator
from apps.tenancy.models import StaffInvite, Tenant, TenantStaff

pytestmark = pytest.mark.django_db

BASE = "https://ayla.test"
LINK_TOKEN = "salon-admin-link-secret-owner"  # noqa: S105
AYLA_USER = "d0a1c2d3-0000-4000-8000-0000000000aa"
RELATIONSHIP = "e0a1c2d3-0000-4000-8000-0000000000aa"


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.AYLA_BASE_URL = BASE
    settings.AYLA_INTERNAL_API_TOKEN = "general-bearer-owner"  # noqa: S105
    settings.AYLA_SALON_ADMIN_LINK_TOKEN = LINK_TOKEN


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="owner-half", name="Салон владельца")


@pytest.fixture
def person(tenant) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="7700201")


def _actor() -> OnboardingActor:
    return OnboardingActor(
        surface="django_admin",
        audit_label="django_admin:user=7",
        cross_tenant=True,
        capability="platform_operations",
    )


def _url(tenant: Tenant) -> str:
    return f"{BASE}/api/v1/internal/tenants/{tenant.slug}/salon-admins/"


def _ok(tenant: Tenant, *, status_code: int = 201) -> dict:
    return {
        "data": {
            "tenant_id": str(tenant.id),
            "slug": tenant.slug,
            "ayla_user_id": AYLA_USER,
            "relationship_id": RELATIONSHIP,
            "role": "admin",
            "status": "created" if status_code == 201 else "replayed",
        }
    }


def _owners(tenant: Tenant, person: BotUser) -> int:
    return TenantStaff.all_tenants.filter(
        tenant=tenant, bot_user=person, role="owner", deactivated_at__isnull=True
    ).count()


def _linked(person: BotUser) -> list[AuditLog]:
    return list(AuditLog.all_tenants.filter(action=STAFF_SALON_ADMIN_LINKED, target_id=person.pk))


def test_operator_grant_of_owner_creates_the_catalog_half_first(
    httpx_mock: HTTPXMock, tenant, person
) -> None:
    httpx_mock.add_response(method="POST", url=_url(tenant), json=_ok(tenant), status_code=201)

    result = grant_role_by_operator(tenant=tenant, bot_user=person, role="owner", actor=_actor())

    assert result.already_had_role is False and _owners(tenant, person) == 1
    (req,) = httpx_mock.get_requests()
    body = json.loads(req.content)
    assert req.headers["Authorization"] == f"Bearer {LINK_TOKEN}"
    assert body["idempotency_key"] == idempotency_key_for(tenant.id, "bot:max:7700201")
    assert len(_linked(person)) == 1


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_owner_code_still_never_calls_the_catalog(httpx_mock: HTTPXMock, tenant, person) -> None:
    httpx_mock.add_response(method="POST", url=_url(tenant), json=_ok(tenant), status_code=201)
    _, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.OWNER)

    redeem_staff_invite(code=code, bot_user=person, tenant=tenant)

    assert _owners(tenant, person) == 1
    assert httpx_mock.get_requests() == []  # ruling п.1: код — не дверь каталога
    # empty-assert-ok: роль выдана (строка выше), каталожной половины нет по построению
    assert _linked(person) == []


def test_existing_owner_gets_the_half_by_a_repeat_grant(
    httpx_mock: HTTPXMock, tenant, person
) -> None:
    """Владельцы до этого решения: оператор жмёт «Выдать роль» повторно — один клик."""
    httpx_mock.add_response(method="POST", url=_url(tenant), json=_ok(tenant), status_code=201)
    TenantStaff.all_tenants.create(tenant=tenant, bot_user=person, role="owner")

    result = grant_role_by_operator(tenant=tenant, bot_user=person, role="owner", actor=_actor())

    assert result.already_had_role is True and _owners(tenant, person) == 1
    assert len(httpx_mock.get_requests()) == 1
    assert len(_linked(person)) == 1


def test_a_catalog_refusal_writes_no_owner_row(httpx_mock: HTTPXMock, tenant, person) -> None:
    httpx_mock.add_response(
        method="POST",
        url=_url(tenant),
        status_code=409,
        json={
            "error": {
                "code": "SALON_ADMIN_LINK_REFUSED",
                "message": "x",
                "details": {"reason": "identity_already_bound"},
            }
        },
    )

    with pytest.raises(CatalogAdminLinkRefused):
        grant_role_by_operator(tenant=tenant, bot_user=person, role="owner", actor=_actor())

    assert _owners(tenant, person) == 0
