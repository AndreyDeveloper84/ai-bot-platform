"""Смена роли в Admin Mini App (DRF-2273).

Решение владельца CD §72 п.15–16 (21.09): Admin Mini App — единственное
рабочее место владельца салона; технические админки — только операторы.
Логика уже есть — ``identity.services.staff_roles.change_staff_role``
(журнал ``STAFF_ROLE_CHANGED``, замок на владельце); нужны ручка и кнопка.

Кто меняет: ТОЛЬКО владелец — решение, уже записанное в
``views_staff_roster``: «The owner ruled that *changing* roles is owner-only».

* r1: владелец меняет администратора в ресепшн — 200, строки, журнал;
* r2: администратор роль не меняет — 403;
* r3: свою роль не меняют — 403. Замок сервиса на роли владельца
  (``owner_role_locked``) сюда не доезжает: активный владелец в салоне один
  (``unique_active_owner_per_tenant``), и менять роли может только он — то
  есть «чужой владелец» и «своя роль» здесь одно и то же;
* r4: сделать кого-то владельцем этим путём нельзя (передача владения —
  отдельный путь из двух шагов) — 403;
* r5: неизвестная роль — 400; у человека нет активной роли — 409;
* r6: человек из чужого салона — 404;
* r7: в администраторы — через каталог (DRF-2085: ``grant_staff_role``
  спрашивает его первым). Согласие — роль сменилась; отказ — 409 по имени,
  прежняя роль на месте (одна транзакция сервиса).

Каталог здесь — заглушка ``catalog_admin_link_stub``: файл доказывает ручку,
а не клиент каталога (он — в ``test_salon_admin_link_2085.py``).
"""

from __future__ import annotations

import json
import uuid

import pytest
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.tenancy.models import TenantStaff

from .conftest import init_data_header

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("catalog_admin_link_stub")]


def _post(client: Client, name: str, body: dict, *, user_id: str = "5001", args=None):
    return client.post(
        reverse(f"admin_api:{name}", args=args or []),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(user_id),
    )


def _active_roles(bot_user) -> set[str]:
    return set(
        TenantStaff.all_tenants.filter(bot_user=bot_user, deactivated_at__isnull=True).values_list(
            "role", flat=True
        )
    )


# ─── DRF-2273 — смена роли ──────────────────────────────────────────────────


class TestRoleChange:
    def test_owner_changes_an_admin_to_receptionist(
        self, client, owner_bot_user, tenant, admin_bot_user
    ):
        resp = _post(
            client,
            "staff_role_change",
            {"bot_user_id": str(admin_bot_user.id), "role": "receptionist"},
        )

        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["role"] == "receptionist"
        assert body["previous_roles"] == ["admin"]
        assert _active_roles(admin_bot_user) == {"receptionist"}
        assert AuditLog.all_tenants.filter(action="staff.role_changed").exists()

    def test_an_admin_may_not_change_roles(
        self, client, owner_bot_user, tenant, admin_bot_user, receptionist_bot_user
    ):
        resp = _post(
            client,
            "staff_role_change",
            {"bot_user_id": str(receptionist_bot_user.id), "role": "admin"},
            user_id="5002",
        )
        assert resp.status_code == 403
        assert _active_roles(receptionist_bot_user) == {"receptionist"}

    def test_nobody_changes_their_own_role(self, client, owner_bot_user, tenant):
        resp = _post(
            client, "staff_role_change", {"bot_user_id": str(owner_bot_user.id), "role": "admin"}
        )
        assert resp.status_code == 403
        assert _active_roles(owner_bot_user) == {"owner"}

    def test_nobody_is_made_owner_this_way(self, client, owner_bot_user, tenant, admin_bot_user):
        resp = _post(
            client, "staff_role_change", {"bot_user_id": str(admin_bot_user.id), "role": "owner"}
        )
        assert resp.status_code == 403
        assert _active_roles(admin_bot_user) == {"admin"}

    def test_unknown_role_and_no_active_role(
        self, client, owner_bot_user, tenant, admin_bot_user, customer_bot_user
    ):
        bad = _post(
            client, "staff_role_change", {"bot_user_id": str(admin_bot_user.id), "role": "boss"}
        )
        assert bad.status_code == 400
        none = _post(
            client, "staff_role_change", {"bot_user_id": str(customer_bot_user.id), "role": "admin"}
        )
        assert none.status_code == 409
        assert none.json()["error"] == "no_active_role"

    def test_a_person_from_another_salon_is_not_found(self, client, owner_bot_user, tenant):
        resp = _post(
            client, "staff_role_change", {"bot_user_id": str(uuid.uuid4()), "role": "admin"}
        )
        assert resp.status_code == 404

    def test_promoting_to_admin_asks_the_catalog(
        self, client, owner_bot_user, tenant, receptionist_bot_user, catalog_admin_link_stub
    ):
        resp = _post(
            client,
            "staff_role_change",
            {"bot_user_id": str(receptionist_bot_user.id), "role": "admin"},
        )
        assert resp.status_code == 200, resp.content
        assert _active_roles(receptionist_bot_user) == {"admin"}
        assert len(catalog_admin_link_stub.calls) == 1

    def test_a_catalog_refusal_keeps_the_old_role(
        self, client, owner_bot_user, tenant, receptionist_bot_user, catalog_admin_link_stub
    ):
        catalog_admin_link_stub.refuse_with = "transport_error"
        resp = _post(
            client,
            "staff_role_change",
            {"bot_user_id": str(receptionist_bot_user.id), "role": "admin"},
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"] == "catalog_admin_link_refused"
        assert body["hint"]
        assert _active_roles(receptionist_bot_user) == {"receptionist"}
