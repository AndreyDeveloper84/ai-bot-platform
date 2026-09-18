"""Отзыв кода приглашения до срока (DRF-2082, PR-2) — сервис.

До этого листа приглашение гасилось только пассивно: сроком и однократностью;
«отозвано оператором» и «истекло» были неразличимы, а отозвать до срока было
нечем (§19 п.2 — «у отзыва приглашения нет сервисного слоя»). Здесь:

* отозванный код — ``InviteNotFound`` на ОБОИХ путях погашения (с тенантом
  и по личности) — тем же словом, что использованный/истёкший (DRF-1061:
  набирающий не различает); положительная стража — тот же код до отзыва
  погашается;
* повторный отзыв — ``changed=False``, не ошибка;
* использованный код не отзывается — ``InviteAlreadyUsed`` по имени, доступ
  снимается отзывом доступа, не кодом;
* аудит ``staff.invite_revoked`` с ``surface``/``actor_label``, без кода и хеша.
"""

from __future__ import annotations

import uuid

import pytest

from apps.audit.models import AuditLog
from apps.events.vocabulary import STAFF_INVITE_REVOKED
from apps.identity.models import BotUser
from apps.identity.services.staff_invites import (
    InviteAlreadyUsed,
    InviteNotFound,
    issue_staff_invite,
    redeem_staff_invite,
    redeem_staff_invite_by_identity,
    revoke_staff_invite,
)
from apps.tenancy.models import StaffInvite, Tenant, TenantStaff

# DRF-2085: роль admin спрашивает каталог (свежая учётка + TUR + связь) ДО
# записи TenantStaff; здесь каталог — заглушка, его половина доказывается в
# apps/identity/tests/test_salon_admin_link_2085.py.
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("catalog_admin_link_stub")]


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="invite-revoke-2082", name="Отзыв кода")


def _person(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id=f"rv-{uuid.uuid4().hex[:8]}"
    )


def _revoke(invite: StaffInvite, reason: str = "проба"):  # noqa: ANN202
    return revoke_staff_invite(
        invite, surface="django_admin", actor_label="django_admin:user=7", reason=reason
    )


class TestRevokedCodeIsRefusedOnBothRedeemPaths:
    def test_redeem_with_tenant_refuses_after_revoke_and_accepts_before(self, tenant):
        invite, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)
        # Положительная стража на том же коде: до отзыва он ещё живой.
        assert StaffInvite.all_tenants.get(pk=invite.pk).revoked_at is None

        result = _revoke(invite)
        assert result.changed is True

        with pytest.raises(InviteNotFound):
            redeem_staff_invite(code=code, bot_user=_person(tenant), tenant=tenant)
        assert not TenantStaff.all_tenants.filter(tenant=tenant).exists()

    def test_redeem_by_identity_refuses_after_revoke(self, tenant):
        invite, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.RECEPTIONIST)
        _revoke(invite)

        with pytest.raises(InviteNotFound):
            redeem_staff_invite_by_identity(
                code=code, channel="max", channel_user_id="rv-identity-1", display_name="Х"
            )
        assert not TenantStaff.all_tenants.filter(tenant=tenant).exists()

    def test_the_same_code_redeems_when_not_revoked(self, tenant):
        """Пара к двум узлам выше: без отзыва тот же путь выдаёт роль."""
        invite, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)

        result = redeem_staff_invite(code=code, bot_user=_person(tenant), tenant=tenant)

        assert result.role == StaffInvite.Role.ADMIN
        assert StaffInvite.all_tenants.get(pk=invite.pk).used_at is not None


class TestRevokeSemantics:
    def test_repeat_is_not_an_error_and_keeps_the_first_timestamp(self, tenant):
        invite, _ = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)
        first = _revoke(invite)
        stamp = StaffInvite.all_tenants.get(pk=invite.pk).revoked_at

        second = _revoke(invite)

        assert first.changed is True and second.changed is False
        assert StaffInvite.all_tenants.get(pk=invite.pk).revoked_at == stamp

    def test_a_used_code_is_not_revoked_by_name(self, tenant):
        invite, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)
        redeem_staff_invite(code=code, bot_user=_person(tenant), tenant=tenant)

        with pytest.raises(InviteAlreadyUsed) as exc_info:
            _revoke(invite)

        assert exc_info.value.slug == "invite_already_used"
        row = StaffInvite.all_tenants.get(pk=invite.pk)
        assert row.revoked_at is None and row.used_at is not None

    def test_audit_row_names_the_operator_and_carries_no_secret(self, tenant):
        invite, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)
        _revoke(invite, reason="выдан не тому")

        rows = list(AuditLog.all_tenants.filter(action=STAFF_INVITE_REVOKED, target_id=invite.pk))
        assert len(rows) == 1
        payload = rows[0].payload
        assert payload["surface"] == "django_admin"
        assert payload["actor_label"] == "django_admin:user=7"
        assert payload["reason"] == "выдан не тому"
        assert rows[0].tenant_id == tenant.id
        flat = str(payload)
        assert code not in flat and invite.code_hash not in flat
