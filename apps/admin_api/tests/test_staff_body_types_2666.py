"""DRF-2666 — словарь в заметке приглашения и причине отзыва не хранится текстом.

Доказано исполнением (``b2``): ``note={"a": 1}`` → 201 и
``StaffInvite.note == "{'a': 1}"``. Узлы — по строкам базы: приглашения и
журнала отзыва.
"""

from __future__ import annotations

import pytest

from apps.admin_api.tests.test_staff_invite import _post as _invite
from apps.admin_api.tests.test_staff_revoke import _post as _revoke
from apps.audit.models import AuditLog
from apps.tenancy.models import StaffInvite, TenantStaff

pytestmark = pytest.mark.django_db

DICT = {"a": 1}
RENDERED = "{'a': 1}"


class TestInviteNote:
    def test_a_dict_note_is_refused_and_no_invite_is_stored(self, client, owner_bot_user, tenant):
        resp = _invite(client, {"role": "admin", "note": DICT})

        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_request"
        assert StaffInvite.all_tenants.count() == 0  # empty-assert-ok: пара ниже — строка хранится

    def test_a_string_note_is_stored_verbatim(self, client, owner_bot_user, tenant):
        resp = _invite(client, {"role": "admin", "note": "для Ольги"})

        assert resp.status_code in (200, 201), resp.content
        assert list(StaffInvite.all_tenants.values_list("note", flat=True)) == ["для Ольги"]


class TestRevokeReason:
    def test_a_dict_reason_is_refused_and_the_role_stays(
        self, client, owner_bot_user, tenant, admin_bot_user
    ):
        resp = _revoke(client, {"bot_user_id": str(admin_bot_user.id), "reason": DICT})

        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_request"
        assert TenantStaff.all_tenants.filter(
            bot_user=admin_bot_user, deactivated_at__isnull=True
        ).exists()
        assert not AuditLog.all_tenants.filter(payload__reason=RENDERED).exists()

    def test_a_string_reason_revokes_and_is_journalled_verbatim(
        self, client, owner_bot_user, tenant, admin_bot_user
    ):
        resp = _revoke(client, {"bot_user_id": str(admin_bot_user.id), "reason": "ушла из салона"})

        assert resp.status_code == 200, resp.content
        assert AuditLog.all_tenants.filter(payload__reason="ушла из салона").exists()
