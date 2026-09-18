"""Отзыв доступа из админки бота закрывает Mini App салона сразу (DRF-2082, PR-1).

Условие листа: «человек теряет Mini App салона сразу (узел: initData того же
человека → 403 после отзыва)». Здесь стенд подписи initData (`conftest`
admin_api): один и тот же человек до отзыва — 200 на списке мастеров, после
действия «Отозвать доступ» в Django Admin — 403 по имени `forbidden`. Пара на
одних данных: 200 до — это стража, что 403 после не от чужой причины.
"""

from __future__ import annotations

import secrets

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from apps.admin_api.tests.conftest import init_data_header
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db


def _operator_client() -> Client:
    password = secrets.token_urlsafe(24)
    user = get_user_model().objects.create_user(
        username="ops-403", password=password, is_staff=True
    )
    user.user_permissions.add(
        Permission.objects.get(codename="platform_operations", content_type__app_label="tenancy")
    )
    client = Client()
    assert client.login(username="ops-403", password=password)
    return client


def test_the_same_init_data_is_403_right_after_the_admin_revokes(tenant: Tenant) -> None:
    person = BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="7001", display_name="Админ", chat_id="7001"
    )
    row = TenantStaff.all_tenants.create(tenant=tenant, bot_user=person, role="admin")
    mini_app = Client()
    url = reverse("admin_api:masters_list")

    before = mini_app.get(url, HTTP_AUTHORIZATION=init_data_header("7001"))
    assert before.status_code == 200, before.content  # стража: до отзыва доступ есть

    resp = _operator_client().post(
        reverse("admin:tenancy_tenantstaff_changelist"),
        {
            "action": "revoke_staff_access",
            "_selected_action": [str(row.pk)],
            "apply": "1",
            "reason": "тест 2082",
        },
        follow=True,
    )
    assert resp.status_code == 200

    after = mini_app.get(url, HTTP_AUTHORIZATION=init_data_header("7001"))
    assert after.status_code == 403, after.content
    assert after.json()["error"] == "forbidden"
    row.refresh_from_db()
    assert row.deactivated_at is not None
