"""Staff invite issue/redeem (DRF-1061).

This is the door into the salon's admin surface, so the tests are weighted
towards what happens when someone pushes on it: wrong codes, reused codes,
expired codes, guessing, and the two idempotency cases a real person will
actually hit.

The master-linking tests carry the most consequence. A master invite must
attach to the catalog row that ALREADY exists — all four pilot masters are
in the mirror, and the booking mirror's `specialist_id` points at those
rows. Linking a person to a fresh duplicate would leave them looking at an
empty day next to their real appointments.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.identity.services.role_resolver import resolve_role
from apps.identity.services.staff_invites import (
    CODE_PREFIX,
    MAX_ATTEMPTS,
    InviteMasterMissing,
    InviteNotFound,
    InviteRateLimited,
    OwnerAlreadyExists,
    format_code,
    generate_code,
    issue_staff_invite,
    looks_like_code,
    normalize_code,
    redeem_staff_invite,
)
from apps.tenancy.models import StaffInvite, TenantStaff

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def tenant():
    from apps.tenancy.models import Tenant

    return Tenant.objects.create(slug="formula-tela-test", name="Формула тела")


@pytest.fixture
def person(tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="900001",
        display_name="Владелец",
    )


@pytest.fixture
def other_person(tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="900002",
        display_name="Администратор",
    )


def _master(tenant, **kwargs) -> CatalogMaster:
    """A catalog master shaped like the pilot's: accepted, active, unlinked."""

    defaults = dict(
        name="Тихонова Ольга",
        external_id=None,
        external_updated_at=timezone.now(),
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        mode=CatalogMaster.Mode.CATALOG_ONLY,
        is_active=True,
    )
    defaults.update(kwargs)
    return CatalogMaster.all_tenants.create(tenant=tenant, **defaults)


class TestCodeFormat:
    def test_generated_codes_are_typeable(self):
        code = generate_code()

        assert len(code) == 4
        # No characters a human confuses when reading aloud.
        assert not set(code) & set("01OIL")

    @pytest.mark.parametrize(
        "typed",
        ["AYLA-7K3M", "ayla-7k3m", "AYLA 7K3M", "7k3m", " 7K3M ", "ayla7k3m"],
    )
    def test_normalization_forgives_how_a_human_types_it(self, typed):
        assert normalize_code(typed) == "7K3M"

    def test_format_round_trips(self):
        assert normalize_code(format_code("7K3M")) == "7K3M"
        assert format_code("7K3M").startswith(CODE_PREFIX)

    @pytest.mark.parametrize("text", ["7K3M", "ayla-7k3m"])
    def test_looks_like_code_accepts_codes(self, text):
        assert looks_like_code(text) is True

    @pytest.mark.parametrize(
        "text",
        ["привет", "", "7K3", "7K3MM", "0OIL", "хочу записаться на маникюр"],
    )
    def test_looks_like_code_rejects_conversation(self, text):
        # Ordinary chat must not burn a rate-limit attempt.
        assert looks_like_code(text) is False


class TestIssue:
    def test_code_is_not_stored_in_plaintext(self, tenant):
        invite, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)

        stored = StaffInvite.all_tenants.get(pk=invite.pk)
        assert normalize_code(code) not in stored.code_hash
        assert len(stored.code_hash) == 64

    def test_master_role_requires_an_existing_catalog_row(self, tenant):
        with pytest.raises(ValueError, match="requires an existing catalog_master"):
            issue_staff_invite(tenant=tenant, role=StaffInvite.Role.MASTER)

    def test_catalog_master_is_rejected_for_non_master_roles(self, tenant):
        with pytest.raises(ValueError, match="only meaningful for role='master'"):
            issue_staff_invite(
                tenant=tenant,
                role=StaffInvite.Role.ADMIN,
                catalog_master=_master(tenant),
            )

    def test_two_invites_never_share_a_code(self, tenant):
        _, first = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)
        _, second = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)

        assert first != second


class TestRedeemStaffRoles:
    def test_admin_code_creates_the_staff_row(self, tenant, person):
        _, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)

        result = redeem_staff_invite(code=code, bot_user=person, tenant=tenant)

        assert result.role == "admin"
        assert result.already_had_role is False
        assert TenantStaff.all_tenants.filter(
            tenant=tenant, bot_user=person, role="admin", deactivated_at__isnull=True
        ).exists()

    def test_the_admin_surface_opens_on_the_next_request(self, tenant, person):
        # The point of the whole ticket: role resolution has no cache, so
        # the row takes effect immediately.
        assert resolve_role(person).is_admin is False

        _, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)
        redeem_staff_invite(code=code, bot_user=person, tenant=tenant)

        role_ctx = resolve_role(person)
        assert role_ctx.is_admin is True
        assert role_ctx.landing_path == "/admin/team"

    def test_owner_code_grants_owner(self, tenant, person):
        _, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.OWNER)

        redeem_staff_invite(code=code, bot_user=person, tenant=tenant)

        assert resolve_role(person).is_owner is True

    def test_roles_are_additive(self, tenant, person):
        _, admin_code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)
        _, recep_code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.RECEPTIONIST)

        redeem_staff_invite(code=admin_code, bot_user=person, tenant=tenant)
        redeem_staff_invite(code=recep_code, bot_user=person, tenant=tenant)

        role_ctx = resolve_role(person)
        assert role_ctx.is_admin is True
        assert role_ctx.is_receptionist is True

    def test_second_owner_is_answered_not_crashed(self, tenant, person, other_person):
        # A partial unique index allows one active owner per tenant. The
        # operator issuing a second owner code deserves an answer, not a 500.
        _, first = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.OWNER)
        _, second = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.OWNER)
        redeem_staff_invite(code=first, bot_user=person, tenant=tenant)

        with pytest.raises(OwnerAlreadyExists):
            redeem_staff_invite(code=second, bot_user=other_person, tenant=tenant)


class TestRedeemMaster:
    def test_links_the_existing_catalog_row(self, tenant, person):
        master = _master(tenant)
        _, code = issue_staff_invite(
            tenant=tenant, role=StaffInvite.Role.MASTER, catalog_master=master
        )

        result = redeem_staff_invite(code=code, bot_user=person, tenant=tenant)

        master.refresh_from_db()
        assert result.catalog_master_id == str(master.id)
        assert master.linked_bot_user_id == person.id
        assert master.invite_status == CatalogMaster.InviteStatus.ACCEPTED
        assert master.mode == CatalogMaster.Mode.INVITE

    def test_does_not_create_a_second_master(self, tenant, person):
        master = _master(tenant)
        _, code = issue_staff_invite(
            tenant=tenant, role=StaffInvite.Role.MASTER, catalog_master=master
        )

        redeem_staff_invite(code=code, bot_user=person, tenant=tenant)

        # A duplicate would be invisible to the booking mirror, whose
        # specialist_id points at the original row.
        assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == 1

    def test_master_is_left_active(self, tenant, person):
        # DRF-1080: the pre-existing invite path leaves is_active=False and
        # nothing flips it, so resolve_role says "master" while every master
        # endpoint answers 403 master_inactive. This path must not repeat it.
        master = _master(tenant, is_active=False)
        _, code = issue_staff_invite(
            tenant=tenant, role=StaffInvite.Role.MASTER, catalog_master=master
        )

        redeem_staff_invite(code=code, bot_user=person, tenant=tenant)

        master.refresh_from_db()
        assert master.is_active is True
        assert resolve_role(person).is_master is True

    def test_archived_master_is_refused(self, tenant, person):
        master = _master(tenant, archived_at=timezone.now())
        _, code = issue_staff_invite(
            tenant=tenant, role=StaffInvite.Role.MASTER, catalog_master=master
        )

        with pytest.raises(InviteMasterMissing):
            redeem_staff_invite(code=code, bot_user=person, tenant=tenant)


class TestRedeemFailures:
    def test_unknown_code(self, tenant, person):
        with pytest.raises(InviteNotFound):
            redeem_staff_invite(code="AYLA-2222", bot_user=person, tenant=tenant)

    def test_used_code_cannot_be_reused_by_someone_else(self, tenant, person, other_person):
        _, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)
        redeem_staff_invite(code=code, bot_user=person, tenant=tenant)

        with pytest.raises(InviteNotFound):
            redeem_staff_invite(code=code, bot_user=other_person, tenant=tenant)

        assert not TenantStaff.all_tenants.filter(bot_user=other_person).exists()

    def test_expired_code(self, tenant, person):
        invite, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)
        invite.expires_at = timezone.now() - timedelta(seconds=1)
        invite.save(update_fields=["expires_at"])

        with pytest.raises(InviteNotFound):
            redeem_staff_invite(code=code, bot_user=person, tenant=tenant)

    def test_failure_modes_are_indistinguishable(self, tenant, person):
        # Unknown, used and expired must present identically: the person
        # cannot tell them apart, and neither should a guesser.
        used_invite, used_code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)
        redeem_staff_invite(code=used_code, bot_user=person, tenant=tenant)
        expired_invite, expired_code = issue_staff_invite(
            tenant=tenant, role=StaffInvite.Role.ADMIN
        )
        expired_invite.expires_at = timezone.now() - timedelta(seconds=1)
        expired_invite.save(update_fields=["expires_at"])

        raised = []
        for candidate in ("AYLA-2222", used_code, expired_code):
            cache.clear()  # isolate from the attempt limiter
            with pytest.raises(InviteNotFound) as exc:
                redeem_staff_invite(code=candidate, bot_user=person, tenant=tenant)
            raised.append(exc.value.slug)

        assert raised == ["invite_not_found"] * 3


class TestIdempotency:
    def test_redeeming_a_second_code_for_a_role_you_hold_succeeds(self, tenant, person):
        # From the person's side "I am an admin" is already true; an error
        # here would be baffling.
        _, first = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)
        _, second = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)
        redeem_staff_invite(code=first, bot_user=person, tenant=tenant)

        result = redeem_staff_invite(code=second, bot_user=person, tenant=tenant)

        assert result.already_had_role is True
        assert (
            TenantStaff.all_tenants.filter(tenant=tenant, bot_user=person, role="admin").count()
            == 1
        )

    def test_master_relinking_the_same_person_is_a_no_op(self, tenant, person):
        master = _master(tenant)
        _, first = issue_staff_invite(
            tenant=tenant, role=StaffInvite.Role.MASTER, catalog_master=master
        )
        _, second = issue_staff_invite(
            tenant=tenant, role=StaffInvite.Role.MASTER, catalog_master=master
        )
        redeem_staff_invite(code=first, bot_user=person, tenant=tenant)

        result = redeem_staff_invite(code=second, bot_user=person, tenant=tenant)

        assert result.already_had_role is True


class TestRateLimit:
    def test_guessing_is_stopped(self, tenant, person):
        for _ in range(MAX_ATTEMPTS):
            with pytest.raises(InviteNotFound):
                redeem_staff_invite(code="AYLA-2222", bot_user=person, tenant=tenant)

        with pytest.raises(InviteRateLimited):
            redeem_staff_invite(code="AYLA-2222", bot_user=person, tenant=tenant)

    def test_the_limit_is_per_person(self, tenant, person, other_person):
        for _ in range(MAX_ATTEMPTS + 1):
            try:
                redeem_staff_invite(code="AYLA-2222", bot_user=person, tenant=tenant)
            except (InviteNotFound, InviteRateLimited):
                pass

        # A second person is unaffected by the first's fumbling.
        with pytest.raises(InviteNotFound):
            redeem_staff_invite(code="AYLA-3333", bot_user=other_person, tenant=tenant)

    def test_a_valid_code_clears_the_counter(self, tenant, person):
        # Someone who mistyped twice and then got it right must not stay
        # penalised for the rest of the hour.
        for _ in range(2):
            with pytest.raises(InviteNotFound):
                redeem_staff_invite(code="AYLA-2222", bot_user=person, tenant=tenant)

        _, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)
        redeem_staff_invite(code=code, bot_user=person, tenant=tenant)

        for _ in range(MAX_ATTEMPTS):
            with pytest.raises(InviteNotFound):
                redeem_staff_invite(code="AYLA-4444", bot_user=person, tenant=tenant)


class TestCrossTenantIsolation:
    """A code from another salon must not resolve here — and must not burn.

    Reachable by one wrong `--tenant` flag at issue time. Before the tenant
    filter, such a code was found, marked used (single-use, gone), and
    created a TenantStaff row against the OTHER tenant — which resolve_role
    never reads, because it filters by the bot user's own tenant. The person
    was told "you are now the owner", still resolved as a customer on the
    next message, and their code was spent. Recovery needed SQL.
    """

    @pytest.fixture
    def other_tenant(self):
        from apps.tenancy.models import Tenant

        return Tenant.objects.create(slug="another-salon", name="Другой салон")

    def test_a_code_issued_for_another_salon_is_not_found(self, tenant, other_tenant, person):
        _, code = issue_staff_invite(tenant=other_tenant, role=StaffInvite.Role.OWNER)

        with pytest.raises(InviteNotFound):
            redeem_staff_invite(code=code, bot_user=person, tenant=tenant)

    def test_and_it_is_not_burned(self, tenant, other_tenant, person):
        # The operator can still re-issue or use it in the right salon; the
        # wrong attempt must cost nothing.
        invite, code = issue_staff_invite(tenant=other_tenant, role=StaffInvite.Role.OWNER)

        with pytest.raises(InviteNotFound):
            redeem_staff_invite(code=code, bot_user=person, tenant=tenant)

        invite.refresh_from_db()
        assert invite.used_at is None
        assert invite.used_by_id is None

    def test_no_staff_row_leaks_into_the_other_tenant(self, tenant, other_tenant, person):
        _, code = issue_staff_invite(tenant=other_tenant, role=StaffInvite.Role.ADMIN)

        with pytest.raises(InviteNotFound):
            redeem_staff_invite(code=code, bot_user=person, tenant=tenant)

        assert not TenantStaff.all_tenants.exists()
