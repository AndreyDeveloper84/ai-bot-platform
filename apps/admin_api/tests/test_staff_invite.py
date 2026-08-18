"""POST /api/v1/admin/staff/invite/ (DRF-1061 block 2.4).

This endpoint hands out salon access, so the tests are weighted towards
who *cannot* use it and what it refuses to do.

The one that matters most is privilege escalation: an admin must not be
able to mint an owner code. A tenant has exactly one active owner enforced
by a partial unique index, and without an explicit check that index would
be the only thing between an admin and taking over the salon — and it would
fail at redemption time, on someone else's screen, not here.
"""

from __future__ import annotations

import json
import uuid

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.identity.services.staff_invites import normalize_code, redeem_staff_invite
from apps.tenancy.models import StaffInvite, Tenant, TenantStaff

from .conftest import init_data_header

pytestmark = pytest.mark.django_db


def _url() -> str:
    return reverse("admin_api:staff_invite_create")


def _post(client: Client, body: dict, *, user_id: str = "5001"):
    return client.post(
        _url(),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(user_id),
    )


def _master(tenant: Tenant, name: str = "Тихонова Ольга", **kwargs) -> CatalogMaster:
    defaults = dict(
        name=name,
        external_id=None,
        external_updated_at=timezone.now(),
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        is_active=True,
    )
    defaults.update(kwargs)
    return CatalogMaster.all_tenants.create(tenant=tenant, **defaults)


class TestIssuing:
    def test_owner_can_issue_an_admin_code(self, client, owner_bot_user, tenant):
        resp = _post(client, {"role": "admin", "note": "Марина, ресепшн"})

        assert resp.status_code == 201, resp.content
        data = resp.json()
        assert data["role"] == "admin"
        assert data["code"]
        assert data["code_is_shown_once"] is True

    def test_the_returned_code_actually_works(self, client, owner_bot_user, tenant):
        # End to end: what the admin screen shows must be redeemable by the
        # person it is handed to.
        code = _post(client, {"role": "admin"}).json()["code"]
        newcomer = BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="90001")

        result = redeem_staff_invite(code=code, bot_user=newcomer, tenant=tenant)

        assert result.role == "admin"
        assert TenantStaff.all_tenants.filter(bot_user=newcomer, role="admin").exists()

    def test_only_the_hash_is_stored(self, client, owner_bot_user, tenant):
        code = _post(client, {"role": "receptionist"}).json()["code"]

        invite = StaffInvite.all_tenants.get()
        assert normalize_code(code) not in invite.code_hash

    def test_the_issuer_is_recorded(self, client, owner_bot_user, tenant):
        _post(client, {"role": "admin"})

        assert StaffInvite.all_tenants.get().created_by_id == owner_bot_user.id

    def test_note_is_kept_for_the_issuer(self, client, owner_bot_user, tenant):
        _post(client, {"role": "admin", "note": "для Марины"})

        assert StaffInvite.all_tenants.get().note == "для Марины"


class TestPrivilegeEscalation:
    """The check that must not be removed."""

    def test_an_admin_cannot_mint_an_owner_code(self, client, admin_bot_user, tenant):
        resp = _post(client, {"role": "owner"}, user_id="5002")

        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"
        # Nothing was created — the refusal happens before the write.
        assert not StaffInvite.all_tenants.exists()

    def test_an_owner_can(self, client, owner_bot_user, tenant):
        assert _post(client, {"role": "owner"}).status_code == 201


class TestMasterCodes:
    def test_links_an_existing_master(self, client, owner_bot_user, tenant):
        master = _master(tenant)

        resp = _post(client, {"role": "master", "master_id": str(master.id)})

        assert resp.status_code == 201
        assert StaffInvite.all_tenants.get().catalog_master_id == master.id

    def test_master_role_requires_a_master_id(self, client, owner_bot_user, tenant):
        resp = _post(client, {"role": "master"})

        assert resp.status_code == 400
        assert "master_id" in resp.json()["detail"]

    def test_a_master_from_another_salon_is_invisible(self, client, owner_bot_user, tenant):
        # Tenant scoping, not a hand-rolled check: the scoped manager
        # cannot see the other salon's row at all.
        other = Tenant.all_objects.create(slug="another-salon", name="Другой")
        foreign = _master(other, name="Чужой Мастер")

        resp = _post(client, {"role": "master", "master_id": str(foreign.id)})

        assert resp.status_code == 404
        assert not StaffInvite.all_tenants.exists()

    def test_archived_masters_are_refused(self, client, owner_bot_user, tenant):
        master = _master(tenant, archived_at=timezone.now())

        assert _post(client, {"role": "master", "master_id": str(master.id)}).status_code == 404

    def test_malformed_master_id_is_a_400_not_a_500(self, client, owner_bot_user, tenant):
        resp = _post(client, {"role": "master", "master_id": "не-uuid"})

        assert resp.status_code == 400

    def test_unknown_master_id_is_404(self, client, owner_bot_user, tenant):
        resp = _post(client, {"role": "master", "master_id": str(uuid.uuid4())})

        assert resp.status_code == 404


class TestValidation:
    @pytest.mark.parametrize("role", ["", "superuser", "Admin", "клиент", None])
    def test_unknown_roles_are_refused(self, client, owner_bot_user, tenant, role):
        resp = _post(client, {"role": role})

        assert resp.status_code == 400
        assert not StaffInvite.all_tenants.exists()

    def test_malformed_json(self, client, owner_bot_user, tenant):
        resp = client.post(
            _url(),
            data="{not json",
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )

        assert resp.status_code == 400

    def test_get_is_not_allowed(self, client, owner_bot_user, tenant):
        assert client.get(_url(), HTTP_AUTHORIZATION=init_data_header("5001")).status_code == 405


class TestAuth:
    def test_unauthenticated_is_refused(self, client, tenant):
        resp = client.post(
            _url(), data=json.dumps({"role": "admin"}), content_type="application/json"
        )

        assert resp.status_code in (400, 401)
        assert not StaffInvite.all_tenants.exists()

    def test_a_receptionist_cannot_issue_codes(self, client, receptionist_bot_user, tenant):
        # require_admin_role admits owner and admin only.
        resp = _post(client, {"role": "admin"}, user_id="5003")

        assert resp.status_code in (401, 403, 404)
        assert not StaffInvite.all_tenants.exists()


class TestAudit:
    def test_the_code_never_reaches_the_audit_log(self, client, owner_bot_user, tenant):
        from apps.audit.models import AuditLog

        code = _post(client, {"role": "admin"}).json()["code"]

        rows = AuditLog.all_tenants.filter(action="staff.invite_issued")
        assert rows.exists()
        serialized = json.dumps([r.payload for r in rows], ensure_ascii=False)
        # An audit row is a place people look; a live credential must not
        # be sitting in one — neither the code nor its hash.
        assert normalize_code(code) not in serialized
        assert "code_hash" not in serialized
