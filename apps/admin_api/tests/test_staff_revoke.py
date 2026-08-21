"""POST /api/v1/admin/staff/revoke/ (DRF-1227).

The invite endpoint could hand out access and nothing could take it back.
These tests weight the refusals, same as the invite ones do, plus the
property that matters most for a revoke: **it has to bite in the live
path**, not only in the model. A deactivated row that still opens the admin
surface is not a revoke.
"""

from __future__ import annotations

import json
import uuid

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.tenancy.models import TenantStaff

from .conftest import init_data_header, link_master_to_bot_user, make_master

pytestmark = pytest.mark.django_db


def _post(client: Client, body: dict, *, user_id: str = "5001"):
    return client.post(
        reverse("admin_api:staff_revoke"),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(user_id),
    )


def _invite(client: Client, body: dict, *, user_id: str):
    return client.post(
        reverse("admin_api:staff_invite_create"),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(user_id),
    )


class TestNamingThePerson:
    def test_by_master_id(self, client, owner_bot_user, tenant, master, master_only_bot_user):
        link_master_to_bot_user(master, master_only_bot_user)

        resp = _post(client, {"master_id": str(master.id)})

        assert resp.status_code == 200, resp.content
        assert resp.json()["master_unlinked"] is True
        master.refresh_from_db()
        assert master.linked_bot_user_id is None

    def test_by_bot_user_id(self, client, owner_bot_user, tenant, admin_bot_user):
        resp = _post(client, {"bot_user_id": str(admin_bot_user.id)})

        assert resp.status_code == 200, resp.content
        assert resp.json()["roles_revoked"] == ["admin"]

    def test_both_identifiers_is_a_bad_request(self, client, owner_bot_user, tenant, master):
        resp = _post(client, {"master_id": str(master.id), "bot_user_id": str(uuid.uuid4())})

        assert resp.status_code == 400

    def test_neither_identifier_is_a_bad_request(self, client, owner_bot_user, tenant):
        assert _post(client, {}).status_code == 400

    def test_a_master_with_nobody_attached_is_not_an_error(
        self, client, owner_bot_user, tenant, master
    ):
        # The master exists, they simply have no MAX account linked. A 404
        # would send the caller looking for a master that is right there.
        resp = _post(client, {"master_id": str(master.id)})

        assert resp.status_code == 200
        assert resp.json()["changed"] is False


class TestRefusals:
    def test_you_cannot_revoke_yourself(self, client, owner_bot_user, tenant, admin_bot_user):
        resp = _post(client, {"bot_user_id": str(admin_bot_user.id)}, user_id="5002")

        assert resp.status_code == 403
        assert TenantStaff.all_tenants.get(bot_user=admin_bot_user).deactivated_at is None

    def test_the_owner_cannot_be_revoked(self, client, owner_bot_user, tenant, admin_bot_user):
        resp = _post(client, {"bot_user_id": str(owner_bot_user.id)}, user_id="5002")

        assert resp.status_code == 409
        assert resp.json()["error"] == "owner_revoke_refused"
        assert TenantStaff.all_tenants.get(bot_user=owner_bot_user).deactivated_at is None

    def test_a_receptionist_may_not_revoke(
        self, client, receptionist_bot_user, tenant, admin_bot_user
    ):
        resp = _post(client, {"bot_user_id": str(admin_bot_user.id)}, user_id="5003")

        assert resp.status_code == 403
        assert TenantStaff.all_tenants.get(bot_user=admin_bot_user).deactivated_at is None

    def test_a_customer_may_not_revoke(self, client, customer_bot_user, tenant, admin_bot_user):
        resp = _post(client, {"bot_user_id": str(admin_bot_user.id)}, user_id="5005")

        assert resp.status_code == 403

    def test_another_salons_master_is_not_found(self, client, owner_bot_user, tenant, other_tenant):
        theirs = make_master(other_tenant, name="Чужой мастер")

        resp = _post(client, {"master_id": str(theirs.id)})

        assert resp.status_code == 404

    def test_a_malformed_id_is_a_bad_request(self, client, owner_bot_user, tenant):
        assert _post(client, {"master_id": "не-uuid"}).status_code == 400


class TestItBitesInTheLivePath:
    """The point of the whole ticket: the surface has to close."""

    def test_a_revoked_admin_can_no_longer_issue_codes(
        self, client, owner_bot_user, tenant, admin_bot_user
    ):
        # Before: the admin can issue access.
        assert _invite(client, {"role": "receptionist"}, user_id="5002").status_code == 201

        _post(client, {"bot_user_id": str(admin_bot_user.id)})

        # After: the same call from the same person is refused.
        after = _invite(client, {"role": "receptionist"}, user_id="5002")
        assert after.status_code == 403

    def test_a_revoked_master_loses_the_master_surface(
        self, client, owner_bot_user, tenant, master, master_only_bot_user
    ):
        from apps.identity.services.role_resolver import resolve_role

        link_master_to_bot_user(master, master_only_bot_user)
        assert resolve_role(master_only_bot_user).is_master is True

        _post(client, {"master_id": str(master.id)})

        assert resolve_role(master_only_bot_user).is_master is False

    def test_the_master_is_still_bookable_afterwards(
        self, client, owner_bot_user, tenant, master, master_only_bot_user
    ):
        # Revoking a login must not quietly take the master off sale.
        link_master_to_bot_user(master, master_only_bot_user)

        _post(client, {"master_id": str(master.id)})

        master.refresh_from_db()
        assert master.is_active is True
        assert master.invite_status == CatalogMaster.InviteStatus.ACCEPTED


class TestRepeatAndRecord:
    def test_revoking_twice_answers_calmly(self, client, owner_bot_user, tenant, admin_bot_user):
        first = _post(client, {"bot_user_id": str(admin_bot_user.id)})
        second = _post(client, {"bot_user_id": str(admin_bot_user.id)})

        assert first.json()["changed"] is True
        assert second.status_code == 200
        assert second.json()["changed"] is False

    def test_the_reason_reaches_the_audit_row(self, client, owner_bot_user, tenant, admin_bot_user):
        from apps.audit.models import AuditLog

        _post(client, {"bot_user_id": str(admin_bot_user.id), "reason": "перешла в другой салон"})

        row = AuditLog.all_tenants.filter(action="staff.access_revoked").get()
        assert row.payload["reason"] == "перешла в другой салон"
        assert row.payload["actor_id"] == str(owner_bot_user.id)

    def test_access_can_be_handed_back_with_a_new_code(
        self, client, owner_bot_user, tenant, admin_bot_user
    ):
        """Re-hiring works — a revoke is not a ban."""

        from apps.identity.services.staff_invites import redeem_staff_invite

        _post(client, {"bot_user_id": str(admin_bot_user.id)})
        code = _invite(client, {"role": "admin"}, user_id="5001").json()["code"]

        result = redeem_staff_invite(code=code, bot_user=admin_bot_user, tenant=tenant)

        assert result.role == "admin"
        assert TenantStaff.all_tenants.filter(
            bot_user=admin_bot_user, role="admin", deactivated_at__isnull=True
        ).exists()


def test_an_unknown_person_is_not_found(client, owner_bot_user, tenant):
    assert _post(client, {"bot_user_id": str(uuid.uuid4())}).status_code == 404


def test_a_person_from_another_salon_is_not_found(client, owner_bot_user, tenant, other_tenant):
    stranger = BotUser.all_tenants.create(
        tenant=other_tenant, channel="max", channel_user_id="6001", chat_id="6001"
    )

    assert _post(client, {"bot_user_id": str(stranger.id)}).status_code == 404
