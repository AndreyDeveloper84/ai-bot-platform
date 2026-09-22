"""Приглашения в Admin Mini App: список со статусами, «Отменить», «Отправить заново» (DRF-2275).

Решение владельца CD §72 п.15–16 (21.09): Admin Mini App — единственное
рабочее место владельца. Коды доступа выдаются с экрана «Команда»
(``staff/invite/``), но увидеть выданные, отменить или перевыпустить было
нечем — только в Django-админке оператора.

Кто: владелец И администратор (главное окно, 22.09): кто выдаёт коды, тот
ими и управляет. Отсюда же граница: код владельца выдаёт только владелец —
значит, только он его отменяет и перевыпускает (403 администратору).
Ссылки-приглашения мастеров (``CatalogMaster.invite_token``) — отдельный
лист; здесь только коды ``StaffInvite``.

  - i1: список несёт статус каждого кода: ожидает / принято / истекло /
    отменено; для кода мастера — имя карточки;
  - i2: отмена ожидающего — «отменено», повтор — ``changed: false``;
    отмена принятого — 409;
  - i3: «отправить заново» — новый код в ответе (один раз), старый отменён,
    роль/заметка/карточка те же; истёкший тоже перевыпускается; принятый —
    409;
  - i4: администратор видит и управляет — 200; код владельца — 403;
  - i5: код чужого салона — 404.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.identity.services.staff_invites import issue_staff_invite
from apps.tenancy.models import StaffInvite

from .conftest import init_data_header

pytestmark = pytest.mark.django_db


def _post(client: Client, name: str, invite_id, *, user_id: str = "5001"):
    return client.post(
        reverse(f"admin_api:{name}", args=[str(invite_id)]),
        data=json.dumps({}),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(user_id),
    )


def _list(client: Client, *, user_id: str = "5001"):
    return client.get(
        reverse("admin_api:staff_invites_list"), HTTP_AUTHORIZATION=init_data_header(user_id)
    )


def _invite(tenant, role: str = "admin", *, master=None, **over) -> StaffInvite:
    invite, _code = issue_staff_invite(
        tenant=tenant, role=role, catalog_master=master, note=over.pop("note", "")
    )
    if over:
        StaffInvite.all_tenants.filter(pk=invite.pk).update(**over)
        invite.refresh_from_db()
    return invite


class TestList:
    def test_the_list_names_each_status(self, client, owner_bot_user, tenant):
        pending = _invite(tenant, note="Лена")
        accepted = _invite(tenant, used_at=timezone.now())
        expired = _invite(tenant, expires_at=timezone.now() - timedelta(days=1))
        cancelled = _invite(tenant, revoked_at=timezone.now())

        resp = _list(client)
        assert resp.status_code == 200, resp.content
        rows = {row["id"]: row for row in resp.json()["items"]}
        assert rows[str(pending.id)]["status"] == "pending"
        assert rows[str(pending.id)]["note"] == "Лена"
        assert rows[str(accepted.id)]["status"] == "accepted"
        assert rows[str(expired.id)]["status"] == "expired"
        assert rows[str(cancelled.id)]["status"] == "cancelled"
        # The code itself is never in the list — only its hash is stored.
        assert all("code" not in row for row in rows.values())

    def test_a_master_code_names_its_card(self, client, owner_bot_user, tenant, master):
        inv = _invite(tenant, role="master", master=master)
        row = next(r for r in _list(client).json()["items"] if r["id"] == str(inv.id))
        assert row["role"] == "master"
        assert row["master_name"] == master.name

    def test_another_salons_codes_are_not_listed(
        self, client, owner_bot_user, tenant, other_tenant
    ):
        mine = _invite(tenant)
        theirs = _invite(other_tenant)
        ids = {row["id"] for row in _list(client).json()["items"]}
        assert str(mine.id) in ids
        assert str(theirs.id) not in ids

    def test_the_front_desk_sees_nothing(
        self, client, owner_bot_user, tenant, receptionist_bot_user
    ):
        assert _list(client, user_id="5003").status_code == 403


class TestCancel:
    def test_cancel_pending_and_refuse_accepted(self, client, owner_bot_user, tenant):
        pending = _invite(tenant)
        accepted = _invite(tenant, used_at=timezone.now())

        ok = _post(client, "staff_invite_cancel", pending.id)
        assert ok.status_code == 200, ok.content
        assert ok.json() == {"changed": True, "status": "cancelled"}
        pending.refresh_from_db()
        assert pending.revoked_at is not None
        assert AuditLog.all_tenants.filter(
            action="staff.invite_revoked", target_id=pending.id
        ).exists()

        again = _post(client, "staff_invite_cancel", pending.id)
        assert again.status_code == 200
        assert again.json()["changed"] is False

        refused = _post(client, "staff_invite_cancel", accepted.id)
        assert refused.status_code == 409
        assert refused.json()["error"] == "invite_already_used"

    def test_another_salons_code_is_not_found(self, client, owner_bot_user, tenant, other_tenant):
        theirs = _invite(other_tenant)
        assert _post(client, "staff_invite_cancel", theirs.id).status_code == 404
        assert _post(client, "staff_invite_cancel", uuid.uuid4()).status_code == 404
        theirs.refresh_from_db()
        assert theirs.revoked_at is None


class TestResend:
    def test_resend_issues_a_new_code_and_cancels_the_old(self, client, owner_bot_user, tenant):
        old = _invite(tenant, role="receptionist", note="Лена")

        resp = _post(client, "staff_invite_resend", old.id)
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["code"]
        assert body["code_is_shown_once"] is True
        assert body["invite_id"] != str(old.id)
        old.refresh_from_db()
        assert old.revoked_at is not None
        new = StaffInvite.all_tenants.get(pk=body["invite_id"])
        assert (new.role, new.note, new.tenant_id) == ("receptionist", "Лена", tenant.id)
        assert new.expires_at > timezone.now()

    def test_a_master_code_is_resent_for_the_same_card(
        self, client, owner_bot_user, tenant, master
    ):
        old = _invite(tenant, role="master", master=master)
        body = _post(client, "staff_invite_resend", old.id).json()
        assert StaffInvite.all_tenants.get(pk=body["invite_id"]).catalog_master_id == master.id

    def test_an_expired_code_is_resent_too(self, client, owner_bot_user, tenant):
        old = _invite(tenant, expires_at=timezone.now() - timedelta(days=1))
        resp = _post(client, "staff_invite_resend", old.id)
        assert resp.status_code == 200, resp.content
        assert resp.json()["code"]

    def test_an_accepted_code_is_not_resent(self, client, owner_bot_user, tenant):
        used = _invite(tenant, used_at=timezone.now())
        before = StaffInvite.all_tenants.filter(tenant=tenant).count()
        resp = _post(client, "staff_invite_resend", used.id)
        assert resp.status_code == 409
        assert resp.json()["error"] == "invite_already_used"
        assert StaffInvite.all_tenants.filter(tenant=tenant).count() == before


class TestWhoManages:
    def test_an_admin_sees_and_manages_invites(
        self, client, owner_bot_user, tenant, admin_bot_user
    ):
        pending = _invite(tenant, role="receptionist")
        assert _list(client, user_id="5002").status_code == 200
        resent = _post(client, "staff_invite_resend", pending.id, user_id="5002")
        assert resent.status_code == 200, resent.content
        new_id = resent.json()["invite_id"]
        ok = _post(client, "staff_invite_cancel", new_id, user_id="5002")
        assert ok.status_code == 200

    def test_an_admin_may_not_touch_an_owner_code(
        self, client, owner_bot_user, tenant, admin_bot_user
    ):
        owner_code = _invite(tenant, role="owner")
        assert (
            _post(client, "staff_invite_cancel", owner_code.id, user_id="5002").status_code == 403
        )
        assert (
            _post(client, "staff_invite_resend", owner_code.id, user_id="5002").status_code == 403
        )
        owner_code.refresh_from_db()
        assert owner_code.revoked_at is None
        # The owner may.
        assert _post(client, "staff_invite_cancel", owner_code.id).status_code == 200


class TestReviewFindings:
    """Code Reviewer, DRF-2275."""

    def test_resending_twice_never_leaves_two_live_codes(self, client, owner_bot_user, tenant):
        # The UI's «Повторить» after a lost response is the real trigger:
        # the first new code is live and was shown to nobody.
        for old in (
            _invite(tenant, note="ждёт"),
            _invite(tenant, note="истёк", expires_at=timezone.now() - timedelta(days=1)),
        ):
            first = _post(client, "staff_invite_resend", old.id)
            assert first.status_code == 200, first.content
            again = _post(client, "staff_invite_resend", old.id)
            assert again.status_code == 409
            assert again.json()["error"] == "invite_already_resent"
            assert (
                StaffInvite.all_tenants.filter(tenant=tenant, note=old.note)
                .exclude(pk=old.pk)
                .count()
                == 1
            )

    def test_resend_is_recorded(self, client, owner_bot_user, tenant):
        old = _invite(tenant)
        new_id = _post(client, "staff_invite_resend", old.id).json()["invite_id"]
        issued = AuditLog.all_tenants.get(action="staff.invite_issued", target_id=new_id)
        assert issued.payload["resent_from"] == str(old.id)
        assert "code" not in issued.payload
        assert AuditLog.all_tenants.filter(action="staff.invite_revoked", target_id=old.id).exists()

    def test_an_archived_card_gets_no_new_code(self, client, owner_bot_user, tenant, master):
        old = _invite(tenant, role="master", master=master)
        master.archived_at = timezone.now()
        master.save(update_fields=["archived_at"])
        before = StaffInvite.all_tenants.filter(tenant=tenant).count()

        resp = _post(client, "staff_invite_resend", old.id)
        assert resp.status_code == 409
        assert resp.json()["error"] == "invite_master_missing"
        assert StaffInvite.all_tenants.filter(tenant=tenant).count() == before

    def test_another_salon_or_a_malformed_id_is_not_found(
        self, client, owner_bot_user, tenant, other_tenant
    ):
        theirs = _invite(other_tenant)
        assert _post(client, "staff_invite_resend", theirs.id).status_code == 404
        assert _post(client, "staff_invite_resend", "not-a-uuid").status_code == 404
        assert _post(client, "staff_invite_cancel", "not-a-uuid").status_code == 404
        assert StaffInvite.all_tenants.filter(tenant=other_tenant).count() == 1

    def test_the_owner_resends_an_owner_code(self, client, owner_bot_user, tenant):
        owner_code = _invite(tenant, role="owner")
        resp = _post(client, "staff_invite_resend", owner_code.id)
        assert resp.status_code == 200, resp.content
        assert resp.json()["role"] == "owner"

    def test_the_list_says_when_it_is_cut(self, client, owner_bot_user, tenant, monkeypatch):
        from apps.admin_api import views_staff_invites

        monkeypatch.setattr(views_staff_invites, "MAX_INVITES", 2)
        for _ in range(3):
            _invite(tenant)
        body = _list(client).json()
        assert (len(body["items"]), body["total_count"], body["truncated"]) == (2, 3, True)
