"""Вернуть отозванный доступ в Admin Mini App (DRF-2274).

Решение владельца CD §72 п.15–16 (21.09): Admin Mini App — единственное
рабочее место владельца салона. Отзыв (``staff/revoke/``) там уже есть,
возврата нет: вернуть человека можно было только новым кодом.

Кто возвращает: ТОЛЬКО владелец. Асимметрия «администратор снимает, но не
возвращает» принята главным окном 22.09: возврат равен выдаче роли, а роли
меняет только владелец (решение в ``views_staff_roster``).

«Вернуть» значит вернуть ТУ роль, что человек держал, — не выдать новую:

* роль сотрудника — есть деактивированная строка ``TenantStaff`` этой роли
  у этого человека в этом салоне (отзыв строки не удаляет);
* мастер — отзыв снимает связь ``CatalogMaster.linked_bot_user`` и больше
  нигде её не хранит, кроме строки журнала ``staff.access_revoked`` с
  ``master_id``. По ней ручка и находит, КОГО возвращать; ``bot_user_id``
  в запросе необязателен, а если он есть — обязан совпасть с журналом.

  - v1: владелец возвращает отозванного администратора — 200, роль снова
    активна;
  - v2: повтор — 200 ``changed: false``;
  - v3: роль, которой человек не держал, — 409 ``role_not_previously_held``;
  - v4: администратор не возвращает — 403; владельцем не делают — 403;
  - v5: мастер получает связь обратно — по журналу, с ``bot_user_id`` и без;
    чужой человек или мастер без отзыва в журнале — 409; карточка уже за
    другим человеком — 409;
  - v6: администратор — через каталог (DRF-2085): отказ — 409 с подсказкой,
    роли нет;
  - v7: ростер отдаёт ``restorable_master`` — иначе экрану не понять, у
    какой строки мастера есть кого возвращать;
  - v8: журнал пережил человека — «забудь всё» журнал не трогает, а
    удаление аккаунта (``account_reset``) оставляет ``AuditLog.target_id``
    намеренно (``KEPT_BY_DESIGN``). Строка есть, человека нет — 409
    ``person_gone``, не «роли не было»: роль была, вернуть некому.

Каталог — заглушка ``catalog_admin_link_stub``: файл доказывает ручку.
"""

from __future__ import annotations

import json
import uuid

import pytest
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.tenancy.models import TenantStaff

from .conftest import _make_bot_user, init_data_header, link_master_to_bot_user, make_master

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("catalog_admin_link_stub")]


def _post(client: Client, name: str, body: dict, *, user_id: str = "5001"):
    return client.post(
        reverse(f"admin_api:{name}"),
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


def _revoke(client, **who) -> None:
    resp = _post(client, "staff_revoke", {k: str(v) for k, v in who.items()})
    assert resp.status_code == 200, resp.content


class TestRestoreStaffRole:
    def test_owner_restores_a_revoked_admin(self, client, owner_bot_user, tenant, admin_bot_user):
        _revoke(client, bot_user_id=admin_bot_user.id)
        assert _active_roles(admin_bot_user) == set()  # присутствие отзыва

        resp = _post(
            client, "staff_restore", {"bot_user_id": str(admin_bot_user.id), "role": "admin"}
        )
        assert resp.status_code == 200, resp.content
        assert resp.json() == {"changed": True, "role": "admin"}
        assert _active_roles(admin_bot_user) == {"admin"}

    def test_restoring_twice_answers_calmly(self, client, owner_bot_user, tenant, admin_bot_user):
        _revoke(client, bot_user_id=admin_bot_user.id)
        body = {"bot_user_id": str(admin_bot_user.id), "role": "admin"}
        assert _post(client, "staff_restore", body).json()["changed"] is True
        again = _post(client, "staff_restore", body)
        assert again.status_code == 200
        assert again.json()["changed"] is False
        assert _active_roles(admin_bot_user) == {"admin"}

    def test_a_role_never_held_is_not_restored(
        self, client, owner_bot_user, tenant, receptionist_bot_user
    ):
        _revoke(client, bot_user_id=receptionist_bot_user.id)
        resp = _post(
            client, "staff_restore", {"bot_user_id": str(receptionist_bot_user.id), "role": "admin"}
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "role_not_previously_held"
        assert _active_roles(receptionist_bot_user) == set()

    def test_an_active_role_is_not_a_revoked_one(
        self, client, owner_bot_user, tenant, receptionist_bot_user
    ):
        # Never revoked: «вернуть» here would be a grant under another name.
        resp = _post(
            client,
            "staff_restore",
            {"bot_user_id": str(receptionist_bot_user.id), "role": "receptionist"},
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "role_not_previously_held"

    def test_an_admin_may_not_restore(
        self, client, owner_bot_user, tenant, admin_bot_user, receptionist_bot_user
    ):
        _revoke(client, bot_user_id=receptionist_bot_user.id)
        resp = _post(
            client,
            "staff_restore",
            {"bot_user_id": str(receptionist_bot_user.id), "role": "receptionist"},
            user_id="5002",
        )
        assert resp.status_code == 403
        assert _active_roles(receptionist_bot_user) == set()

    def test_nobody_is_made_owner_this_way(self, client, owner_bot_user, tenant, admin_bot_user):
        resp = _post(
            client, "staff_restore", {"bot_user_id": str(admin_bot_user.id), "role": "owner"}
        )
        assert resp.status_code == 403
        assert _active_roles(admin_bot_user) == {"admin"}

    def test_a_catalog_refusal_restores_nothing(
        self, client, owner_bot_user, tenant, admin_bot_user, catalog_admin_link_stub
    ):
        _revoke(client, bot_user_id=admin_bot_user.id)
        catalog_admin_link_stub.refuse_with = "transport_error"

        resp = _post(
            client, "staff_restore", {"bot_user_id": str(admin_bot_user.id), "role": "admin"}
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"] == "catalog_admin_link_refused"
        assert body["details"]["hint"]
        assert _active_roles(admin_bot_user) == set()

    def test_a_person_from_another_salon_is_not_found(
        self, client, owner_bot_user, tenant, other_tenant
    ):
        stranger = _make_bot_user(other_tenant, channel_user_id="7001", display_name="Чужая")
        TenantStaff.all_tenants.create(
            tenant=other_tenant, bot_user=stranger, role=TenantStaff.Role.ADMIN
        )
        TenantStaff.all_tenants.filter(bot_user=stranger).update(deactivated_at="2026-09-01T00:00Z")

        resp = _post(client, "staff_restore", {"bot_user_id": str(stranger.id), "role": "admin"})
        assert resp.status_code == 404
        assert _active_roles(stranger) == set()

    @pytest.mark.parametrize(
        "body",
        [
            {"bot_user_id": "not-a-uuid", "role": "admin"},
            {"role": "admin"},
            {"bot_user_id": str(uuid.uuid4()), "role": "boss"},
            {"bot_user_id": str(uuid.uuid4()), "master_id": str(uuid.uuid4()), "role": "admin"},
            {"bot_user_id": str(uuid.uuid4()), "role": "master"},
        ],
        ids=[
            "malformed-id",
            "missing-id",
            "unknown-role",
            "staff-role-with-master",
            "no-master-id",
        ],
    )
    def test_a_malformed_request_is_400(self, client, owner_bot_user, tenant, body):
        assert _post(client, "staff_restore", body).status_code == 400


class TestRestoreMaster:
    def _revoked_master(self, client, master, person):
        link_master_to_bot_user(master, person)
        _revoke(client, master_id=master.id)
        master.refresh_from_db()
        assert master.linked_bot_user_id is None  # присутствие отзыва

    def test_a_revoked_master_gets_the_link_back(
        self, client, owner_bot_user, tenant, master, master_only_bot_user
    ):
        self._revoked_master(client, master, master_only_bot_user)

        resp = _post(
            client,
            "staff_restore",
            {
                "master_id": str(master.id),
                "bot_user_id": str(master_only_bot_user.id),
                "role": "master",
            },
        )
        assert resp.status_code == 200, resp.content
        assert resp.json() == {"changed": True, "role": "master"}
        master.refresh_from_db()
        assert master.linked_bot_user_id == master_only_bot_user.id

    def test_the_master_row_alone_is_enough(
        self, client, owner_bot_user, tenant, master, master_only_bot_user
    ):
        # After the revoke the roster shows the master as a row with no
        # account — the screen holds a master_id and nothing else.
        self._revoked_master(client, master, master_only_bot_user)

        resp = _post(client, "staff_restore", {"master_id": str(master.id), "role": "master"})
        assert resp.status_code == 200, resp.content
        master.refresh_from_db()
        assert master.linked_bot_user_id == master_only_bot_user.id
        again = _post(client, "staff_restore", {"master_id": str(master.id), "role": "master"})
        assert again.json()["changed"] is False

    def test_somebody_else_is_not_restored_onto_the_card(
        self, client, owner_bot_user, tenant, master, master_only_bot_user, customer_bot_user
    ):
        self._revoked_master(client, master, master_only_bot_user)

        resp = _post(
            client,
            "staff_restore",
            {
                "master_id": str(master.id),
                "bot_user_id": str(customer_bot_user.id),
                "role": "master",
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "role_not_previously_held"
        master.refresh_from_db()
        assert master.linked_bot_user_id is None

    def test_a_master_never_revoked_has_nobody_to_restore(
        self, client, owner_bot_user, tenant, master
    ):
        resp = _post(client, "staff_restore", {"master_id": str(master.id), "role": "master"})
        assert resp.status_code == 409
        assert resp.json()["error"] == "role_not_previously_held"

    def test_a_person_gone_since_is_named_as_gone(
        self, client, owner_bot_user, tenant, master, master_only_bot_user
    ):
        self._revoked_master(client, master, master_only_bot_user)
        # The account deleted since: the journal keeps the id by design and
        # the BotUser is gone. Pointing the row elsewhere stands in for the
        # deletion — the real one needs the whole reset plan.
        AuditLog.all_tenants.filter(
            action="staff.access_revoked", payload__master_id=str(master.id)
        ).update(target_id=uuid.uuid4())

        resp = _post(client, "staff_restore", {"master_id": str(master.id), "role": "master"})
        assert resp.status_code == 409
        assert resp.json()["error"] == "person_gone"
        master.refresh_from_db()
        assert master.linked_bot_user_id is None

    def test_a_card_taken_since_is_refused(
        self, client, owner_bot_user, tenant, master, master_only_bot_user, customer_bot_user
    ):
        self._revoked_master(client, master, master_only_bot_user)
        link_master_to_bot_user(master, customer_bot_user)

        resp = _post(client, "staff_restore", {"master_id": str(master.id), "role": "master"})
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_recipient"
        master.refresh_from_db()
        assert master.linked_bot_user_id == customer_bot_user.id


class TestRosterSaysWhatCanBeRestored:
    def test_a_revoked_master_row_is_restorable_and_a_plain_one_is_not(
        self, client, owner_bot_user, tenant, master, master_only_bot_user
    ):
        plain = make_master(tenant, name="Вера Лис", external_id=77)
        link_master_to_bot_user(master, master_only_bot_user)
        _revoke(client, master_id=master.id)

        resp = client.get(reverse("admin_api:staff_roster"), HTTP_AUTHORIZATION=init_data_header())
        assert resp.status_code == 200
        by_master = {row["master_id"]: row for row in resp.json()["items"] if row["master_id"]}
        assert by_master[str(master.id)]["restorable_master"] is True
        assert by_master[str(plain.id)]["restorable_master"] is False
