"""DRF-2984 B2 — гейты подписки на админской поверхности и дверях мастеров.

Решения владельца 20.10: В-2 (соло со вторым мастером → переход на «Салон»,
не запрет), В-3 (у салона жёсткого лимита нет), В-4 (заморозка — статус, а
не ``is_active=False``). Всё за ``BILLING_ENABLED``.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client

from apps.admin_api.services.master_deactivation import DeactivationError, reactivate_master
from apps.admin_api.tests.conftest import init_data_header, make_master
from apps.catalog.models import CatalogMaster
from apps.catalog.services.verification import verify_masters_by_salon
from apps.identity.services.staff_invites import MasterAddBlocked, link_master_to_person
from apps.tenancy.billing_gate import MasterAddVerdict, master_add_verdict
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

OWNER = "5001"


@pytest.fixture
def billing_on(settings) -> None:
    settings.BILLING_ENABLED = True


def _subscription(tenant: Tenant, **fields: Any) -> Tenant:
    Tenant.all_objects.filter(pk=tenant.pk).update(**fields)
    return Tenant.all_objects.get(pk=tenant.pk)


def _invite_body() -> dict[str, Any]:
    return {
        "name": "Лера Новая",
        "contact_method": "max_username",
        "contact_value": "@lera_new_2984",
        "services": [],
        "schedule_preset": "default_mon_fri_10_19",
        "mode": "invite",
    }


# ── вердикт ────────────────────────────────────────────────────────────────


def test_v1_flag_off_allows_even_a_frozen_salon(tenant):
    frozen = _subscription(tenant, subscription_status="frozen")

    assert master_add_verdict(frozen) is MasterAddVerdict.ALLOWED


@pytest.mark.parametrize(
    ("status", "verdict"),
    [
        ("frozen", MasterAddVerdict.REFUSED_FROZEN),
        ("canceled", MasterAddVerdict.REFUSED_CANCELED),
        ("past_due", MasterAddVerdict.ALLOWED),
        ("trial", MasterAddVerdict.ALLOWED),
        ("none", MasterAddVerdict.ALLOWED),
    ],
)
def test_v2_only_frozen_and_canceled_refuse(billing_on, tenant, status, verdict):
    assert master_add_verdict(_subscription(tenant, subscription_status=status)) is verdict


def test_v3_a_salon_has_no_hard_master_limit(billing_on, tenant):
    """В-3: сверх трёх — доплата, её считает каталог; запрета нет."""

    for i in range(5):
        make_master(tenant, name=f"Мастер {i}", external_id=100 + i)
    salon = _subscription(tenant, subscription_status="active", plan="salon")

    assert master_add_verdict(salon) is MasterAddVerdict.ALLOWED


def test_v4_a_solo_second_master_needs_the_plan_change(billing_on, tenant, master):
    solo = _subscription(tenant, subscription_status="active", plan="solo")

    assert master_add_verdict(solo) is MasterAddVerdict.NEEDS_PLAN_CHANGE


def test_v5_the_solo_master_herself_is_not_a_second_one(billing_on, tenant, master):
    solo = _subscription(tenant, subscription_status="active", plan="solo")

    assert master_add_verdict(solo, master_id=master.id) is MasterAddVerdict.ALLOWED


def test_v6_a_confirmed_switch_to_salon_lets_the_second_master_in_at_once(
    billing_on, tenant, master
):
    """Каталог меняет вид тенанта при подтверждении, тариф — со следующего периода."""

    switching = _subscription(
        tenant, subscription_status="active", plan="solo", pending_plan="salon"
    )

    assert master_add_verdict(switching) is MasterAddVerdict.ALLOWED


# ── заморозка Mini App ─────────────────────────────────────────────────────


def test_f1_a_frozen_salon_closes_the_admin_app(billing_on, client: Client, tenant, owner_bot_user):
    _subscription(tenant, subscription_status="frozen")

    resp = client.get("/api/v1/admin/masters/", HTTP_AUTHORIZATION=init_data_header(OWNER))

    assert resp.status_code == 403
    assert resp.json()["error"] == "subscription_frozen"


def test_f2_flag_off_leaves_a_frozen_salon_open(client: Client, tenant, owner_bot_user):
    _subscription(tenant, subscription_status="frozen")

    resp = client.get("/api/v1/admin/masters/", HTTP_AUTHORIZATION=init_data_header(OWNER))

    assert resp.status_code == 200


def test_f3_past_due_is_not_frozen(billing_on, client: Client, tenant, owner_bot_user):
    """Долг блокирует новые записи в каталоге (C1), но не закрывает приложение."""

    _subscription(tenant, subscription_status="past_due")

    resp = client.get("/api/v1/admin/masters/", HTTP_AUTHORIZATION=init_data_header(OWNER))

    assert resp.status_code == 200


# ── двери ──────────────────────────────────────────────────────────────────


def test_d1_invite_on_a_solo_answers_plan_change(
    billing_on, client: Client, tenant, owner_bot_user, master
):
    _subscription(tenant, subscription_status="active", plan="solo")
    before = CatalogMaster.all_tenants.filter(tenant=tenant).count()

    resp = client.post(
        "/api/v1/admin/masters/invite/",
        data=_invite_body(),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(OWNER),
    )

    assert resp.status_code == 409
    assert resp.json()["error"] == "plan_change_required"
    assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == before


def test_d2_switching_on_an_inactive_master_on_a_solo_answers_plan_change(
    billing_on, client: Client, tenant, owner_bot_user, master, inactive_master
):
    _subscription(tenant, subscription_status="active", plan="solo")

    resp = client.patch(
        f"/api/v1/admin/masters/{inactive_master.id}/",
        data={"is_active": True},
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(OWNER),
    )

    assert resp.status_code == 409
    assert resp.json()["error"] == "plan_change_required"
    inactive_master.refresh_from_db()
    assert inactive_master.is_active is False


def test_d3_reactivation_is_refused_while_frozen(billing_on, tenant, owner_bot_user):
    archived = make_master(tenant, name="Ушедшая", is_active=False, external_id=77)
    CatalogMaster.all_tenants.filter(pk=archived.pk).update(archived_at="2026-10-01T00:00:00Z")
    _subscription(tenant, subscription_status="frozen")

    with pytest.raises(DeactivationError) as caught:
        reactivate_master(archived, notify_master=False, actor=owner_bot_user, actor_role="owner")

    assert (caught.value.slug, caught.value.status) == ("subscription_frozen", 409)
    archived.refresh_from_db()
    assert archived.archived_at is not None


def test_d4_linking_a_person_is_refused_and_leaves_the_card_unlinked(
    billing_on, tenant, customer_bot_user
):
    from django.db import transaction

    card = make_master(tenant, name="Новая", is_active=False, external_id=55)
    _subscription(tenant, subscription_status="canceled")

    with pytest.raises(MasterAddBlocked) as caught, transaction.atomic():
        link_master_to_person(master_id=card.id, tenant_id=tenant.id, bot_user=customer_bot_user)

    assert caught.value.slug == "subscription_canceled"
    card.refresh_from_db()
    assert card.linked_bot_user_id is None


def test_d5_salon_verification_counts_billing_refusals_apart(
    billing_on, tenant, owner_bot_user, master
):
    waiting = make_master(
        tenant,
        name="Ждёт подтверждения",
        invite_status=CatalogMaster.InviteStatus.PENDING,
        external_id=66,
    )
    CatalogMaster.all_tenants.filter(pk=waiting.pk).update(invite_token=None)
    _subscription(tenant, subscription_status="active", plan="solo")

    outcome = verify_masters_by_salon(
        CatalogMaster.all_tenants.filter(pk=waiting.pk), actor=owner_bot_user
    )

    assert (outcome.verified, outcome.billing_blocked) == (0, 1)
    waiting.refresh_from_db()
    assert waiting.invite_status == CatalogMaster.InviteStatus.PENDING
