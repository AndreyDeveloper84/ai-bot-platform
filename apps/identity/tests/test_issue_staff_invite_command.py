"""`manage.py issue_staff_invite` (DRF-1061).

The command exists to break a chicken-and-egg: the screen that issues
invites is behind ``@require_admin_role``, and a fresh salon has no admins.
It makes the first one from outside the loop.

Most of these tests are about refusing to do the wrong thing quietly —
an operator running this on the pilot host, at a terminal, with one shot at
copying the code.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.identity.services.staff_invites import normalize_code, redeem_staff_invite
from apps.tenancy.models import StaffInvite, Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant() -> Tenant:
    obj, _ = Tenant.all_objects.get_or_create(
        slug="formula-tela", defaults={"name": "Формула тела"}
    )
    return obj


def _master(tenant, name="Тихонова Ольга", **kwargs) -> CatalogMaster:
    defaults = dict(
        name=name,
        external_id=None,
        external_updated_at=timezone.now(),
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        is_active=True,
    )
    defaults.update(kwargs)
    return CatalogMaster.all_tenants.create(tenant=tenant, **defaults)


def _run(**kwargs) -> str:
    out = StringIO()
    call_command("issue_staff_invite", stdout=out, **kwargs)
    return out.getvalue()


def _code_from(output: str) -> str:
    for line in output.splitlines():
        if line.strip().startswith("code:"):
            return line.split("code:", 1)[1].strip()
    raise AssertionError(f"no code in output:\n{output}")


class TestIssuing:
    def test_owner_invite_is_printed_and_usable(self, tenant):
        output = _run(tenant="formula-tela", role="owner")
        code = _code_from(output)

        person = BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="1")
        result = redeem_staff_invite(code=code, bot_user=person, tenant=tenant)

        assert result.role == "owner"

    def test_the_code_is_not_recoverable_from_the_database(self, tenant):
        code = _code_from(_run(tenant="formula-tela", role="admin"))

        invite = StaffInvite.all_tenants.get()
        assert normalize_code(code) not in invite.code_hash

    def test_output_carries_the_deeplink_so_nobody_has_to_type(self, tenant):
        output = _run(tenant="formula-tela", role="admin")

        assert "start=inv_" in output
        # The deeplink payload must be flat — MAX rejects '=' and '&' in it,
        # and a dash would not survive normalization anyway.
        deeplink = [ln for ln in output.splitlines() if "start=inv_" in ln][0]
        assert "-" not in deeplink.split("start=inv_", 1)[1]

    def test_ttl_is_configurable(self, tenant):
        _run(tenant="formula-tela", role="admin", ttl_days=1)

        invite = StaffInvite.all_tenants.get()
        assert (invite.expires_at - timezone.now()).days == 0  # <24h away


class TestMasterInvites:
    def test_links_by_name(self, tenant):
        master = _master(tenant)

        output = _run(tenant="formula-tela", role="master", master_name="Тихонова Ольга")

        invite = StaffInvite.all_tenants.get()
        assert invite.catalog_master_id == master.id
        assert "Тихонова Ольга" in output

    def test_partial_name_is_enough(self, tenant):
        master = _master(tenant)

        _run(tenant="formula-tela", role="master", master_name="Тихонова")

        assert StaffInvite.all_tenants.get().catalog_master_id == master.id

    def test_ambiguous_name_refuses_and_shows_the_ids(self, tenant):
        _master(tenant, name="Ольга Т.")
        _master(tenant, name="Ольга П.")

        with pytest.raises(CommandError, match="2 masters match"):
            _run(tenant="formula-tela", role="master", master_name="Ольга")

        assert not StaffInvite.all_tenants.exists()

    def test_master_role_without_a_name_refuses(self, tenant):
        with pytest.raises(CommandError, match="needs --master-name"):
            _run(tenant="formula-tela", role="master")

    def test_unknown_master_refuses(self, tenant):
        _master(tenant)

        with pytest.raises(CommandError, match="No active master matching"):
            _run(tenant="formula-tela", role="master", master_name="Кто-то Другой")

    def test_archived_masters_are_not_offered(self, tenant):
        _master(tenant, archived_at=timezone.now())

        with pytest.raises(CommandError, match="No active master matching"):
            _run(tenant="formula-tela", role="master", master_name="Тихонова")

    def test_already_linked_master_warns_but_proceeds(self, tenant):
        # Re-inviting is legitimate when someone changes MAX account, but
        # it moves the link and should be a conscious act.
        person = BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="2")
        _master(tenant, linked_bot_user=person)

        output = _run(tenant="formula-tela", role="master", master_name="Тихонова")

        assert "already linked" in output
        assert StaffInvite.all_tenants.exists()


class TestListing:
    def test_lists_masters_with_link_state(self, tenant):
        _master(tenant)

        output = _run(tenant="formula-tela", role="owner", list_masters=True)

        assert "Тихонова Ольга" in output
        assert "NOT linked" in output
        # --list-masters is a read-only inspection, not an issue.
        assert not StaffInvite.all_tenants.exists()


class TestTenantValidation:
    def test_unknown_tenant_lists_the_known_ones(self, tenant):
        with pytest.raises(CommandError, match="No tenant with slug"):
            _run(tenant="does-not-exist", role="owner")


class TestMasterIdPath:
    """--master-id is what the command recommends for the ambiguous case,
    so it must be at least as safe as the by-name path, not less."""

    def test_links_by_id(self, tenant):
        master = _master(tenant)

        _run(tenant="formula-tela", role="master", master_id=str(master.id))

        assert StaffInvite.all_tenants.get().catalog_master_id == master.id

    def test_warns_about_relinking_here_too(self, tenant):
        # This warning used to fire only for --master-name, which is
        # backwards: the operator most at risk of re-pointing someone
        # else's link is the one disambiguating by id.
        person = BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="3")
        master = _master(tenant, linked_bot_user=person)

        output = _run(tenant="formula-tela", role="master", master_id=str(master.id))

        assert "already linked" in output

    def test_malformed_id_is_an_error_not_a_traceback(self, tenant):
        with pytest.raises(CommandError, match="not a valid master id"):
            _run(tenant="formula-tela", role="master", master_id="не-uuid")

    def test_archived_master_by_id_is_refused(self, tenant):
        master = _master(tenant, archived_at=timezone.now())

        with pytest.raises(CommandError, match="No active master"):
            _run(tenant="formula-tela", role="master", master_id=str(master.id))


class TestRoleMismatch:
    @pytest.mark.parametrize("role", ["admin", "owner", "receptionist"])
    def test_master_flags_with_a_non_master_role_are_refused(self, tenant, role):
        # `--role admin --master-name "Ольга"` is a mistyped role. Issuing
        # an ADMIN code while the operator believes they invited a master
        # hands out more access than intended.
        _master(tenant)

        with pytest.raises(CommandError, match="only valid with --role=master"):
            _run(tenant="formula-tela", role=role, master_name="Тихонова")

        assert not StaffInvite.all_tenants.exists()
